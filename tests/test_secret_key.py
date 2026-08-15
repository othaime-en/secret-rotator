import os
import unittest

from secret_rotator.web.secret_key import resolve_secret_key


class TestResolveSecretKey(unittest.TestCase):
    """Regression tests for S3: Flask SECRET_KEY was hardcoded and never
    actually configurable, so this covers the resolution priority and the
    production fail-fast behavior that replaces it."""

    def setUp(self):
        # Make sure ambient env vars from a real environment don't leak
        # into these tests.
        self._saved_env = {
            k: os.environ.pop(k, None)
            for k in ("FLASK_SECRET_KEY", "SECRET_ROTATOR_ENV")
        }

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_explicit_real_key_is_used(self):
        key = resolve_secret_key("a-real-explicit-secret-key")
        self.assertEqual(key, "a-real-explicit-secret-key")

    def test_hardcoded_dev_default_is_rejected_dev_env(self):
        # The old hardcoded value must never be returned as-is; in dev
        # mode it should fall through to an ephemeral generated key.
        key = resolve_secret_key("dev-key-change-in-production")
        self.assertNotEqual(key, "dev-key-change-in-production")
        self.assertGreaterEqual(len(key), 32)

    def test_unexpanded_placeholder_is_rejected(self):
        key = resolve_secret_key("${FLASK_SECRET_KEY}")
        self.assertNotEqual(key, "${FLASK_SECRET_KEY}")

    def test_env_var_used_when_no_explicit_key(self):
        os.environ["FLASK_SECRET_KEY"] = "key-from-environment"
        key = resolve_secret_key(None)
        self.assertEqual(key, "key-from-environment")

    def test_explicit_key_takes_priority_over_env_var(self):
        os.environ["FLASK_SECRET_KEY"] = "key-from-environment"
        key = resolve_secret_key("explicit-config-key")
        self.assertEqual(key, "explicit-config-key")

    def test_dev_fallback_generates_random_key(self):
        key1 = resolve_secret_key(None)
        key2 = resolve_secret_key(None)
        self.assertTrue(key1)
        self.assertNotEqual(key1, key2)  # ephemeral, regenerated each call

    def test_production_without_any_key_raises(self):
        os.environ["SECRET_ROTATOR_ENV"] = "production"
        with self.assertRaises(RuntimeError):
            resolve_secret_key(None)

    def test_production_with_placeholder_still_raises(self):
        os.environ["SECRET_ROTATOR_ENV"] = "production"
        with self.assertRaises(RuntimeError):
            resolve_secret_key("${FLASK_SECRET_KEY}")

    def test_production_with_real_explicit_key_succeeds(self):
        os.environ["SECRET_ROTATOR_ENV"] = "production"
        key = resolve_secret_key("a-real-production-secret-key")
        self.assertEqual(key, "a-real-production-secret-key")

    def test_production_with_env_var_succeeds(self):
        os.environ["SECRET_ROTATOR_ENV"] = "production"
        os.environ["FLASK_SECRET_KEY"] = "key-from-environment"
        key = resolve_secret_key(None)
        self.assertEqual(key, "key-from-environment")


if __name__ == "__main__":
    unittest.main()