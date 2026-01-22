"""
Health monitoring and backup verification endpoints.
"""

from flask import Blueprint, jsonify, request, current_app
from secret_rotator.utils.logger import logger

bp = Blueprint('health', __name__)


@bp.route('/backup-health')
def backup_health():
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