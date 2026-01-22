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