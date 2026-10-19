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

from flask import Blueprint, render_template, current_app, request, session
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
    options = current_app.job_admin.options()
    return render_page(
        "jobs.html",
        "jobs",
        "Jobs",
        jobs=job_list,
        rotating=rotating,
        active_count=sum(1 for job in job_list if not job["paused"]),
        options=options,
        hidden_jobs=options["hidden"],
    )


@bp.app_template_filter("duration_label")
def duration_label(milliseconds):
    """1234 -> '1.2s', 450 -> '450ms', 65000 -> '1m 05s', None -> '—'."""
    if milliseconds is None:
        return "—"
    if milliseconds < 1000:
        return f"{milliseconds}ms"
    seconds = milliseconds / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    return f"{minutes}m {rest:02d}s"


@bp.route("/jobs/<path:job_id>")
def job_detail(job_id):
    """A single rotation job: status, run history, backups, configuration."""
    detail = current_app.job_status.job_detail(job_id)
    if detail is None:
        return (
            render_page(
                "not_found.html",
                "jobs",
                "Job not found",
                heading="Job not found",
                message=f"There is no job named “{job_id}”.",
                back_url="/jobs",
                back_label="Back to jobs",
            ),
            404,
        )
    rotating = 1 if detail["job"]["status"] == "rotating" else 0
    return render_page(
        "job.html",
        "jobs",
        job_id,
        detail=detail,
        rotating=rotating,
        options=current_app.job_admin.options(),
    )


BACKUPS_PER_PAGE = 25


@bp.route("/backups")
def backups():
    """Backups: filterable, paged list with a details drawer and restore."""
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int) or 1  # junk -> page 1

    result = current_app.rotation_engine.backup_manager.list_backup_page(
        query=query or None, page=page, per_page=BACKUPS_PER_PAGE
    )
    return render_page("backups.html", "backups", "Backups", q=query, result=result)


HEALTH_RANGES = (7, 30, 90)


@bp.route("/backup-health")
def backup_health():
    """Backup health: latest verification, recent runs and history."""
    days = request.args.get("days", HEALTH_RANGES[0], type=int)
    if days not in HEALTH_RANGES:
        days = HEALTH_RANGES[0]
    data = current_app.backup_health.summary(days)
    return render_page(
        "backup_health.html",
        "backup-health",
        "Backup health",
        data=data,
        ranges=HEALTH_RANGES,
    )


ACTIVITY_PER_PAGE = 100
ACTIVITY_LEVELS = (
    ("all", "All"),
    ("info", "Info"),
    ("warning", "Warnings"),
    ("error", "Errors"),
)


@bp.route("/activity")
def activity():
    """Activity log: audit events with level filter, search and live view."""
    level = request.args.get("level", "all").strip().lower()
    if level not in {key for key, _ in ACTIVITY_LEVELS}:
        level = "all"
    query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int) or 1  # junk -> page 1

    result = current_app.activity.query(level, query, page, ACTIVITY_PER_PAGE)
    return render_page(
        "activity.html",
        "activity",
        "Activity log",
        result=result,
        events=list(reversed(result["events"])),  # the pane reads oldest to newest
        level=level,
        q=query,
        levels=ACTIVITY_LEVELS,
    )


_COMPONENT_PAGES = {
    "providers": ("Providers", "Where secrets are stored and written back.", "provider"),
    "rotators": ("Rotators", "How each new secret value is generated.", "rotator"),
}


def _components_page(kind):
    title, blurb, noun = _COMPONENT_PAGES[kind]
    return render_page(
        "components.html",
        kind,
        title,
        kind=kind,
        noun=noun,
        page_blurb=blurb,
        listing=current_app.component_admin.listing(kind),
    )


@bp.route("/providers")
def providers():
    """Providers: where secrets are stored; add, edit, test, delete."""
    return _components_page("providers")


@bp.route("/rotators")
def rotators():
    """Rotators: how new secret values are generated; add, edit, preview, delete."""
    return _components_page("rotators")


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
