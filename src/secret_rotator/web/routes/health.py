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


@bp.route("/backup-health")
def backup_health():
    """
    Get backup system health metrics.

    Returns overall health status, success rates, and recent
    verification statistics.

    Returns:
        JSON with health metrics

    Example Response:
        {
            "status": "healthy",
            "success_rate": 100.0,
            "total_backups": 15,
            "verified": 15,
            "failed": 0,
            "last_verification": "2025-01-22T14:30:22.123456"
        }
    """
    engine = current_app.rotation_engine

    if not hasattr(engine, "scheduler") or engine.scheduler is None:
        logger.warning("Scheduler not available for health check")
        return (
            jsonify(
                {
                    "error": "Scheduler not available",
                    "message": "Backup health monitoring requires scheduler to be running",
                }
            ),
            503,
        )

    try:
        health = engine.scheduler.get_backup_health()
        logger.debug(f"Backup health: {health['status']}")
        return jsonify(health)

    except Exception as e:
        logger.error(f"Failed to get backup health: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/verification-history")
def verification_history():
    """
    Get backup verification history.

    Query Parameters:
        days (optional): Number of days to include (default: 7)

    Returns:
        JSON with verification history

    Example Response:
        {
            "history": [
                {
                    "timestamp": "2025-01-22T04:00:00",
                    "total_backups": 15,
                    "verified": 15,
                    "failed": 0
                }
            ],
            "days": 7
        }
    """
    engine = current_app.rotation_engine

    if not hasattr(engine, "scheduler") or engine.scheduler is None:
        return jsonify({"error": "Scheduler not available"}), 503

    # Get days parameter (default: 7)
    days = request.args.get("days", default=7, type=int)

    if days < 1 or days > 365:
        return (
            jsonify(
                {"error": "Invalid days parameter", "message": "days must be between 1 and 365"}
            ),
            400,
        )

    try:
        history = engine.scheduler.get_verification_history(days)

        logger.info(
            f"Verification history requested for {days} days, " f"returning {len(history)} records"
        )

        return jsonify({"history": history, "days": days})

    except Exception as e:
        logger.error(f"Failed to get verification history: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/run-verification", methods=["POST"])
@limiter.limit("5 per minute")
def run_verification():
    """
    Trigger manual backup verification.

    This endpoint initiates an immediate verification of all backups,
    independent of the scheduled verification time.

    Rate limited to 5/minute per user — verification reads and
    checksums every backup on disk, so it's not free to call in a
    loop.

    Returns:
        JSON with verification report

    Example Response:
        {
            "success": true,
            "report": {
                "timestamp": "2025-01-22T14:30:22.123456",
                "total_backups": 15,
                "verified": 15,
                "failed": 0,
                "corrupted": [],
                "errors": []
            }
        }
    """
    engine = current_app.rotation_engine

    if not hasattr(engine, "scheduler") or engine.scheduler is None:
        return jsonify({"error": "Scheduler not available"}), 503

    logger.info("Manual backup verification triggered via API")

    try:
        report = engine.scheduler.run_verification_now()

        logger.info(
            f"Verification complete: {report['verified']}/{report['total_backups']} "
            f"verified, {report['failed']} failed"
        )

        return jsonify({"success": True, "report": report})

    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


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
