"""Tests for the redesigned app shell: real page URLs, navigation,
authentication and the theme switch."""

import os
import re
import unittest

from werkzeug.security import generate_password_hash

from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web.app import create_app
from secret_rotator.web.rate_limit import limiter

TEST_USERNAME = "shell-admin"
TEST_PASSWORD = "correct-horse-battery-staple"

# (url, nav key expected to be highlighted, heading expected on the page)
PAGES = [
    ("/", "overview", "Overview"),
    ("/jobs", "jobs", "Jobs"),
    ("/jobs/database_password", "jobs", "database_password"),
    ("/backups", "backups", "Backups"),
    ("/backup-health", "backup-health", "Backup health"),
    ("/activity", "activity", "Activity log"),
    ("/providers", "providers", "Providers"),
    ("/rotators", "rotators", "Rotators"),
    ("/settings", "settings", "Settings"),
]

NAV_LINKS = {
    "Overview": "/",
    "Jobs": "/jobs",
    "Backups": "/backups",
    "Backup health": "/backup-health",
    "Activity": "/activity",
    "Providers": "/providers",
    "Rotators": "/rotators",
    "Settings": "/settings",
}


class TestAppShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.pop(k, None)
            for k in (
                "SECRET_ROTATOR_ADMIN_USERNAME",
                "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
                "FLASK_SECRET_KEY",
                "SECRET_ROTATOR_ENV",
            )
        }
        os.environ["SECRET_ROTATOR_ADMIN_USERNAME"] = TEST_USERNAME
        os.environ["SECRET_ROTATOR_ADMIN_PASSWORD_HASH"] = generate_password_hash(TEST_PASSWORD)
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for k in (
            "SECRET_ROTATOR_ADMIN_USERNAME",
            "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
            "FLASK_SECRET_KEY",
            "SECRET_ROTATOR_ENV",
        ):
            os.environ.pop(k, None)
        for k, v in cls._saved_env.items():
            if v is not None:
                os.environ[k] = v

    def setUp(self):
        DistributedLock.reset_local_locks()
        app = create_app(RotationEngine())
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        limiter.reset()

    def _login(self):
        self.client.post("/login", data={"username": TEST_USERNAME, "password": TEST_PASSWORD})

    def _get(self, url):
        resp = self.client.get(url)
        html = resp.get_data(as_text=True)
        resp.close()
        return resp, html

    # ---- authentication ----

    def test_every_page_requires_login(self):
        for url, _, _ in PAGES + [("/legacy", None, None)]:
            resp = self.client.get(url, follow_redirects=False)
            self.assertEqual(resp.status_code, 302, url)
            self.assertIn("/login", resp.headers["Location"], url)

    # ---- pages ----

    def test_every_page_renders_in_the_shell(self):
        self._login()
        for url, _, heading in PAGES:
            resp, html = self._get(url)
            self.assertEqual(resp.status_code, 200, url)
            self.assertIn(f"<h1>{heading}</h1>", html, url)
            self.assertIn('class="side"', html, url)
            self.assertIn("css/layout.css", html, url)

    def test_active_nav_item_matches_the_page(self):
        self._login()
        for url, key, _ in PAGES:
            _, html = self._get(url)
            current = re.findall(r'<a href="([^"]+)" class="on" aria-current="page">', html)
            expected = [
                NAV_LINKS[label] for label in NAV_LINKS if NAV_LINKS[label] == _nav_url(key)
            ]
            self.assertEqual(current, expected, url)

    def test_nav_uses_real_urls_not_hash_links(self):
        self._login()
        _, html = self._get("/")
        for label, href in NAV_LINKS.items():
            self.assertRegex(html, rf'<a href="{re.escape(href)}"[^>]*>{re.escape(label)}</a>')
        nav = html[html.index('<nav class="nav"') : html.index("</nav>")]
        self.assertNotIn('href="#', nav)

    def test_job_detail_escapes_the_job_id(self):
        self._login()
        _, html = self._get("/jobs/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E")
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)

    # ---- shell controls ----

    def test_theme_switch_has_three_choices(self):
        self._login()
        _, html = self._get("/")
        for choice in ("system", "light", "dark"):
            self.assertIn(f'data-theme-choice="{choice}"', html)

    def test_sign_out_is_a_csrf_protected_post_form(self):
        self._login()
        _, html = self._get("/")
        match = re.search(r'<form method="POST" action="/logout">(.*?)</form>', html, re.S)
        self.assertIsNotNone(match)
        self.assertIn('name="csrf_token"', match.group(1))

    def test_shows_signed_in_username(self):
        self._login()
        _, html = self._get("/")
        self.assertIn(TEST_USERNAME, html)

    def test_environment_label_follows_secret_rotator_env(self):
        self._login()
        _, html = self._get("/")
        self.assertIn("Development", html)
        os.environ["SECRET_ROTATOR_ENV"] = "production"
        try:
            _, html = self._get("/")
        finally:
            os.environ.pop("SECRET_ROTATOR_ENV", None)
        self.assertIn("Production", html)

    def test_shell_loads_no_third_party_resources(self):
        self._login()
        _, html = self._get("/")
        self.assertNotRegex(html, r'(?:src|href)="https?://')

    # ---- temporary legacy route ----

    def test_legacy_dashboard_still_available(self):
        self._login()
        resp, html = self._get("/legacy")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("dashboard.js", html)


def _nav_url(key):
    return {
        "overview": "/",
        "jobs": "/jobs",
        "backups": "/backups",
        "backup-health": "/backup-health",
        "activity": "/activity",
        "providers": "/providers",
        "rotators": "/rotators",
        "settings": "/settings",
    }[key]


if __name__ == "__main__":
    unittest.main()
