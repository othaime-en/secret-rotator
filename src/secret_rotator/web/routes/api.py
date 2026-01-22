"""
RESTful API endpoints for Secret Rotation operations.

This blueprint provides JSON API endpoints for:
- Rotation job management
- Backup operations
- Secret rotation triggers
- System status

All endpoints return JSON responses and use standard HTTP status codes.
"""

from flask import Blueprint, jsonify, request, current_app
from secret_rotator.utils.logger import logger

bp = Blueprint('api', __name__)


@bp.route('/status')
def status():
    """
    Get system status and statistics.
    
    Returns:
        JSON with status, provider count, rotator count, and job count
    
    Example Response:
        {
            "status": "running",
            "providers": 2,
            "rotators": 3,
            "jobs": 5
        }
    """
    engine = current_app.rotation_engine
    
    status_data = {
        'status': 'running',
        'providers': len(engine.providers),
        'rotators': len(engine.rotators),
        'jobs': len(engine.rotation_jobs)
    }
    
    logger.debug(f"Status endpoint called: {status_data}")
    return jsonify(status_data)


@bp.route('/jobs')
def jobs():
    """
    List all configured rotation jobs.
    
    Returns:
        JSON with list of job configurations
    
    Example Response:
        {
            "jobs": [
                {
                    "name": "database_password",
                    "provider": "file_storage",
                    "rotator": "password_gen",
                    "secret_id": "db_password",
                    "schedule": "weekly"
                }
            ]
        }
    """
    engine = current_app.rotation_engine
    jobs_data = {'jobs': engine.rotation_jobs}
    
    logger.info(f"Jobs endpoint called, returning {len(engine.rotation_jobs)} jobs")
    return jsonify(jobs_data)


@bp.route('/rotate', methods=['POST'])
def rotate():
    """
    Trigger rotation of all configured secrets.
    
    This endpoint initiates immediate rotation of all jobs,
    regardless of their schedule. Use with caution.
    
    Returns:
        JSON with rotation results for each job
    
    Example Response:
        {
            "results": {
                "database_password": true,
                "api_key": true,
                "service_token": false
            }
        }
    """
    engine = current_app.rotation_engine
    
    logger.info("Manual rotation triggered via API")
    
    try:
        results = engine.rotate_all_secrets()
        successful = sum(1 for r in results.values() if r)
        total = len(results)
        
        logger.info(f"Rotation complete: {successful}/{total} successful")
        
        return jsonify({'results': results})
    
    except Exception as e:
        logger.error(f"Rotation failed: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/backups')
def backups():
    """
    List available backups with optional filtering.
    
    Query Parameters:
        secret_id (optional): Filter backups by secret ID
    
    Returns:
        JSON with list of backup metadata
    
    Example Response:
        {
            "backups": [
                {
                    "secret_id": "db_password",
                    "timestamp": "20250122_143022_123456",
                    "backup_file": "/path/to/backup.json",
                    "encrypted": true,
                    "backup_created": "2025-01-22T14:30:22.123456"
                }
            ]
        }
    """
    engine = current_app.rotation_engine
    secret_id = request.args.get('secret_id')
    
    try:
        backups = engine.backup_manager.list_backups(secret_id, mask_values=True)
        
        logger.info(f"Backups endpoint called, returning {len(backups)} backups"
                   + (f" for secret_id={secret_id}" if secret_id else ""))
        
        return jsonify({'backups': backups})
    
    except Exception as e:
        logger.error(f"Failed to list backups: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500