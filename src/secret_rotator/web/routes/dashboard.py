"""
Dashboard routes for serving HTML pages.

Every page lives at a real URL and renders inside the shared app shell
(``app_base.html``). Authentication is enforced app-wide by the
``before_request`` hook registered in ``web/app.py``, so nothing here
needs to repeat it.

Pages are rebuilt one at a time as part of the dashboard redesign; those
not yet rebuilt render a placeholder (``page_stub.html``). The previous
single-page dashboard stays reachable at ``/legacy`` until the redesign is
complete, so rotation/restore remain usable in the meantime.
"""

import os
from datetime import datetime, timezone

from flask import Blueprint, render_template, current_app, session
from secret_rotator.utils.logger import logger

bp = Blueprint("dashboard", __name__)


def _environment():
    """Label shown in the sidebar footer: (text, is_production)."""
    env = os.getenv("SECRET_ROTATOR_ENV", "development").strip().lower()
    if env == "production":
        return "Production", True
    return "Development", False


def render_page(template, active, title, **context):
    """Render a page inside the app shell.

    Args:
        template: Page template name.
        active: Nav key to highlight (e.g. ``"jobs"``).
        title: Page title, used for <title> and the placeholder heading.
        **context: Extra template variables.
    """
    env_label, env_is_production = _environment()
    return render_template(
        template,
        active=active,
        page_title=title,
        username=session.get("username"),
        env_label=env_label,
        env_is_production=env_is_production,
        **context,
    )


def _stub(active, title, blurb, **context):
    return render_page("page_stub.html", active, title, page_blurb=blurb, **context)


@bp.app_template_filter("schedule_label")
def schedule_label(value):
    """'every_2_hours' -> 'Every 2 hours', 'daily' -> 'Daily'."""
    return str(value or "").replace("_", " ").capitalize()


@bp.app_template_filter("format_utc")
def format_utc(value):
    """ISO timestamp -> '2026-10-13 04:00 UTC'.

    The no-JavaScript fallback text inside <time>; static/js/time.js
    replaces it with the viewer's local time.
    """
    try:
        parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return str(value or "")
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


@bp.route("/")
def index():
    """Overview: totals, items needing attention, job status, activity."""
    data = current_app.job_status.overview()
    return render_page("overview.html", "overview", "Overview", data=data)


@bp.route("/jobs")
def jobs():
    """Rotation jobs."""
    job_list = current_app.job_status.job_summaries()
    rotating = sum(1 for job in job_list if job["status"] == "rotating")
    return render_page("jobs.html", "jobs", "Jobs", jobs=job_list, rotating=rotating)


@bp.route("/jobs/<path:job_id>")
def job_detail(job_id):
    """A single rotation job."""
    return _stub(
        "jobs",
        job_id,
        "Job details, run history and configuration.",
        job_id=job_id,
    )


@bp.route("/backups")
def backups():
    """Backups."""
    return _stub("backups", "Backups", "Encrypted snapshots created on every rotation.")


@bp.route("/backup-health")
def backup_health():
    """Backup health."""
    return _stub("backup-health", "Backup health", "Integrity verification across all backups.")


@bp.route("/activity")
def activity():
    """Activity log."""
    return _stub("activity", "Activity log", "Rotations, restores, and system events.")


@bp.route("/providers")
def providers():
    """Providers."""
    return _stub("providers", "Providers", "Where secrets are stored and written back.")


@bp.route("/rotators")
def rotators():
    """Rotators."""
    return _stub("rotators", "Rotators", "How each new secret value is generated.")


@bp.route("/settings")
def settings():
    """Settings."""
    return _stub("settings", "Settings", "Edits config.yaml. Some changes need a restart.")


@bp.route("/legacy")
def legacy_dashboard():
    """Previous single-page dashboard.

    Temporary: kept only so rotation and restore stay usable while the
    redesigned pages are built. Removed together with web_interface.py
    and the old static bundle at the end of the redesign.
    """
    logger.info("Legacy dashboard page accessed")

    engine = current_app.rotation_engine
    stats = {
        "total_jobs": len(engine.rotation_jobs),
        "total_providers": len(engine.providers),
        "total_rotators": len(engine.rotators),
    }
    return render_template("dashboard.html", stats=stats, username=session.get("username"))


@bp.route("/health")
def health_page():
    """Previous backup health page (superseded by /backup-health)."""
    logger.info("Health page accessed")
    return render_template("health.html")
