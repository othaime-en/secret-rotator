"""
Session-based authentication for the Secret Rotator web dashboard.
"""

import os
import time

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from secret_rotator.config.settings import settings
from secret_rotator.utils.logger import logger

bp = Blueprint("auth", __name__)

# Endpoints that must remain reachable without a session. Keep this list
# as small as possible — anything added here is unauthenticated by
# definition.
EXEMPT_ENDPOINTS = {
    "auth.login",
    "health.healthz",  # unauthenticated liveness probe for Docker/orchestrators
    "static",
}


def _get_configured_username() -> str:
    return os.getenv("SECRET_ROTATOR_ADMIN_USERNAME") or settings.get(
        "web.auth.username", "admin"
    )


def _get_configured_password_hash():
    return os.getenv("SECRET_ROTATOR_ADMIN_PASSWORD_HASH") or settings.get(
        "web.auth.password_hash"
    )


def credentials_configured() -> bool:
    """True if there's a password hash configured anywhere (env or config)."""
    return bool(_get_configured_password_hash())


def hash_password(plaintext_password: str) -> str:
    """Hash a plaintext password for storage in config.yaml or an env var.

    Used by `secret-rotator --mode set-web-password` and available for
    scripting/tests.
    """
    return generate_password_hash(plaintext_password)


def verify_credentials(username: str, password: str) -> bool:
    """Check a submitted username/password against the configured admin
    credentials. Returns False (never raises) for any misconfiguration
    or mismatch, so callers can treat this as a plain pass/fail check."""
    configured_hash = _get_configured_password_hash()
    if not configured_hash:
        return False

    configured_username = _get_configured_username()

    # check_password_hash uses hmac.compare_digest internally, so the
    # password comparison itself is constant-time. Still run it even on
    # a username mismatch (against the real configured hash) so a wrong
    # username doesn't short-circuit faster than a wrong password would
    # — a small mitigation against username enumeration via timing.
    password_ok = check_password_hash(configured_hash, password)
    username_ok = username == configured_username
    return username_ok and password_ok


def require_login():
    """Registered as an app-wide before_request hook in app.py.

    Returns None to let the request proceed, or a Flask response to
    short-circuit it (redirect to /login for page requests, 401 JSON
    for API requests).
    """
    if request.endpoint is None or request.endpoint in EXEMPT_ENDPOINTS:
        return None

    if session.get("authenticated"):
        return None

    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401

    return redirect(url_for("auth.login", next=request.path))


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if not credentials_configured():
            logger.error(
                "Login attempted but no web admin password is configured. "
                "Run `secret-rotator --mode set-web-password` to set one."
            )
            error = "Login is not configured on this server. Contact your administrator."
        elif verify_credentials(username, password):
            session.clear()
            session["authenticated"] = True
            session["username"] = username
            session.permanent = True
            logger.info(f"Successful web login for user '{username}'")

            next_url = request.form.get("next") or request.args.get("next")
            # Only ever redirect to a same-site relative path, never an
            # absolute/external URL, to avoid this becoming an open
            # redirect.
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("dashboard.index"))
        else:
            logger.warning(f"Failed web login attempt for user '{username!r}'")
            error = "Invalid username or password"
            # Small, deliberate delay to add friction against rapid
            # credential-guessing. Not a substitute for real rate
            # limiting (tracked separately under S10).
            time.sleep(0.5)

    return render_template(
        "login.html", error=error, next=request.args.get("next", "")
    )


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    username = session.get("username")
    session.clear()
    if username:
        logger.info(f"User '{username}' logged out")
    return redirect(url_for("auth.login"))