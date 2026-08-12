"""
Health monitoring and backup verification endpoints.

This blueprint provides endpoints for:
- Backup system health metrics
- Verification history
- Manual verification triggers
"""

from flask import Blueprint, jsonify, request, current_app
from secret_rotator.utils.logger import logger

bp = Blueprint('health', __name__)


@bp.route('/healthz')
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
    """
    return jsonify({'status': 'ok'})


@bp.route('/backup-health')
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
    
    if not hasattr(engine, 'scheduler') or engine.scheduler is None:
        logger.warning("Scheduler not available for health check")
        return jsonify({
            'error': 'Scheduler not available',
            'message': 'Backup health monitoring requires scheduler to be running'
        }), 503
    
    try:
        health = engine.scheduler.get_backup_health()
        logger.debug(f"Backup health: {health['status']}")
        return jsonify(health)
    
    except Exception as e:
        logger.error(f"Failed to get backup health: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/verification-history')
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
    
    if not hasattr(engine, 'scheduler') or engine.scheduler is None:
        return jsonify({
            'error': 'Scheduler not available'
        }), 503
    
    # Get days parameter (default: 7)
    days = request.args.get('days', default=7, type=int)
    
    if days < 1 or days > 365:
        return jsonify({
            'error': 'Invalid days parameter',
            'message': 'days must be between 1 and 365'
        }), 400
    
    try:
        history = engine.scheduler.get_verification_history(days)
        
        logger.info(f"Verification history requested for {days} days, "
                   f"returning {len(history)} records")
        
        return jsonify({
            'history': history,
            'days': days
        })
    
    except Exception as e:
        logger.error(f"Failed to get verification history: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/run-verification', methods=['POST'])
def run_verification():
    """
    Trigger manual backup verification.
    
    This endpoint initiates an immediate verification of all backups,
    independent of the scheduled verification time.
    
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
    
    if not hasattr(engine, 'scheduler') or engine.scheduler is None:
        return jsonify({
            'error': 'Scheduler not available'
        }), 503
    
    logger.info("Manual backup verification triggered via API")
    
    try:
        report = engine.scheduler.run_verification_now()
        
        logger.info(f"Verification complete: {report['verified']}/{report['total_backups']} "
                   f"verified, {report['failed']} failed")
        
        return jsonify({
            'success': True,
            'report': report
        })
    
    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500