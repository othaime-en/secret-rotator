from flask import Blueprint, jsonify, current_app
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