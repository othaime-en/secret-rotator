"""
Health monitoring and backup verification endpoints.

This provides endpoints for:
- Backup system health metrics
- Verification history
- Manual verification triggers
"""

from flask import Blueprint, jsonify, request, current_app, session
from secret_rotator.utils.logger import logger
from secret_rotator.web.rate_limit import limiter

bp = Blueprint("health", __name__)


@bp.route("/healthz")
@limiter.exempt
def healthz():
    """
    Minimal, unauthenticated liveness probe.

    This is intentionally separate from /status (in api_bp), which now
    requires login (S1) and returns operational details (job/provider
    counts). Docker/orchestrator healthchecks need a plain "is the
    process up" check that doesn't require credentials and doesn't leak
    any internal state — this is that endpoint. Keep it exempt from auth
    in web/auth.py's EXEMPT_ENDPOINTS, and keep it free of anything more
    revealing than a static "ok".

    Exempt from rate limiting: orchestrator healthchecks poll
    this every ~30s indefinitely by design (see the Dockerfile
    HEALTHCHECK), and it does no real work, so there's no abuse case
    to defend against here — only a false-positive "unhealthy"
    container to avoid.
    """
    return jsonify({"status": "ok"})


@bp.route("/verification/summary")
@limiter.limit("120 per minute")
def verification_summary():
    """
    Everything the Backup health page shows, in one request.

    Query Parameters:
        days (optional): how far back the history table reaches, 1-365
            (default 7)

    Example Response (abridged):
        {
            "status": "warning",
            "latest": {
                "timestamp": "2026-10-12T04:00:00+00:00",
                "total": 44, "verified": 41, "failed": 3,
                "success_rate": 93.2, "status": "critical",
                "failures": [{"secret_id": "db", "file": "db_2026....json",
                              "reason": "Integrity check failed"}],
                "failures_total": 3
            },
            "recent_runs": [{"timestamp": "...", "ok": true}],
            "history": [{"timestamp": "...", "total": 44, "verified": 41,
                         "failed": 3, "success_rate": 93.2}],
            "days": 7,
            "running": false
        }
    """
    days = request.args.get("days", default=7, type=int)
    if days < 1 or days > 365:
        return jsonify({"error": "days must be between 1 and 365"}), 400
    return jsonify(current_app.backup_health.summary(days))


@bp.route("/verification/run", methods=["POST"])
@limiter.limit("5 per minute")
def start_verification():
    """
    Start a verification of every backup in the background.

    Verification decrypts each backup, so it can take a while; poll
    GET /api/verification/status for the outcome. The finished report is
    also written to the verification history.

    Returns:
        202 with the new run's state, or 409 with the state of the run
        that is already in progress.
    """
    actor = session.get("username", "unknown")
    run, started = current_app.backup_health.start_verification(actor)
    if started:
        logger.info(f"Manual backup verification started by {actor}")
        return jsonify({"run": run}), 202
    return jsonify({"error": "A verification is already running", "run": run}), 409


@bp.route("/verification/status")
@limiter.limit("120 per minute")
def verification_status():
    """
    State of the latest manual verification started in this process.

    Example Response:
        {"run": {"status": "completed", "actor": "admin",
                 "started_at": "...", "finished_at": "...",
                 "report": {"total": 48, "verified": 48, "failed": 0},
                 "error": null}}

    ``run`` is null if none has been started since the process began.
    """
    return jsonify({"run": current_app.backup_health.verification_status()})
