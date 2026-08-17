"""
RESTful API endpoints for Secret Rotation operations.

This blueprint provides JSON API endpoints for:
- Rotation job management
- Backup operations
- Secret rotation triggers
- System status

All endpoints return JSON responses and use standard HTTP status codes.
"""

from flask import Blueprint, jsonify, request, current_app, session
from urllib.parse import unquote
from secret_rotator.utils.logger import logger
from secret_rotator.audit_log import audit_log
from secret_rotator.web.rate_limit import limiter

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
@limiter.limit("10 per minute")
def rotate():
    """
    Start a background job that rotates all configured secrets.

    This no longer runs rotation synchronously in the request thread. Rotating
    every configured job with a 1-second delay between each — see
    RotationEngine.rotate_all_secrets — could mean tens of seconds or
    more blocking the HTTP request with no progress feedback, and
    would eventually just time out on a large enough job list. This
    endpoint now returns immediately with a job id; poll
    GET /api/rotate/<job_id> for live progress and the final results.

    Rate limited to 10/minute per user — raised from the
    previous 3/minute now that this call itself is cheap (it only
    enqueues work); the actual rotation work is still protected from
    overlap by RotationEngine's own lock (see rotate_all_secrets),
    which this endpoint surfaces as a 409 with the in-flight job's id
    rather than starting a second sweep.

    Returns:
        202 Accepted with the new job's initial state, or
        409 Conflict with the already-running job's state if a
        rotation (triggered via this endpoint) is already in flight.

    Example Response (202):
        {
            "job_id": "3f9c2e1a-...",
            "status": "queued",
            "actor": "alice",
            "progress": {"completed": 0, "total": 8},
            "status_url": "/api/rotate/3f9c2e1a-..."
        }
    """
    actor = session.get('username', 'unknown')
    logger.info(f"Manual rotation triggered via API by {actor}")

    job = current_app.job_manager.start_rotation(actor=actor)
    job['status_url'] = f"/api/rotate/{job['job_id']}"

    if job.get('already_running', False):
        logger.info(
            f"Rotation requested by {actor} but job {job['job_id']} "
            f"is already in progress; returning its status instead of "
            f"starting a new one"
        )
        return jsonify(job), 409

    return jsonify(job), 202


@bp.route('/rotate/<job_id>')
@limiter.limit("120 per minute")
def rotate_job_status(job_id):
    """
    Poll the status of a background rotation job started via
    POST /api/rotate.

    Rate limited separately from the app default (200/hour): the
    dashboard polls this every ~1.5s while a job is in flight, which
    would otherwise trip the default limit within the first minute of
    watching a single rotation run.

    Returns:
        200 with the job's current state, or 404 if job_id is unknown
        (never existed, or aged out — jobs are retained for 1 hour
        after completion).

    Example Response:
        {
            "job_id": "3f9c2e1a-...",
            "status": "running",
            "actor": "alice",
            "created_at": "2026-08-15T09:00:00+00:00",
            "started_at": "2026-08-15T09:00:00+00:00",
            "finished_at": null,
            "progress": {"completed": 3, "total": 8},
            "results": {
                "database_password": true,
                "api_key": true,
                "service_token": false
            },
            "error": null
        }
    """
    job = current_app.job_manager.get_job(job_id)
    if job is None:
        return jsonify({'error': 'Job not found'}), 404

    return jsonify(job)


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


@bp.route('/backups/<path:backup_file>')
def backup_detail(backup_file):
    """
    Get detailed information about a specific backup.
    
    Path Parameters:
        backup_file: URL-encoded path to backup file
    
    Returns:
        JSON with decrypted backup metadata
    
    Example Response:
        {
            "secret_id": "db_password",
            "timestamp": "20250122_143022_123456",
            "old_value": "old_password_masked",
            "new_value": "new_password_masked",
            "backup_created": "2025-01-22T14:30:22.123456",
            "encrypted": true
        }
    """
    engine = current_app.rotation_engine
    
    # Flask automatically decodes the path parameter
    # but we'll be extra careful
    decoded_path = unquote(backup_file)
    
    logger.info(f"Backup detail requested for: {decoded_path}")
    
    try:
        backup_data = engine.backup_manager.restore_backup(decoded_path, decrypt=True)
        
        # Mask sensitive values for display
        from secret_rotator.encryption_manager import SecretMasker
        backup_data['old_value'] = SecretMasker.mask_for_backup_display(
            backup_data['old_value']
        )
        backup_data['new_value'] = SecretMasker.mask_for_backup_display(
            backup_data['new_value']
        )
        
        return jsonify(backup_data)
    
    except FileNotFoundError:
        logger.warning(f"Backup file not found: {decoded_path}")
        return jsonify({'error': 'Backup not found'}), 404
    
    except ValueError as e:
        # Raised by BackupManager when the requested path resolves
        # outside the backup directory (path traversal attempt).
        logger.warning(f"Rejected backup path outside backup directory: {decoded_path} ({e})")
        return jsonify({'error': 'Invalid backup file'}), 400
    
    except Exception as e:
        logger.error(f"Failed to load backup detail: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.route('/restore', methods=['POST'])
@limiter.limit("10 per minute")
def restore():
    """
    Restore a secret from a backup.

    Rate limited to 10/minute per user - restoring overwrites a
    live secret with an old value, so this shouldn't be something a
    script can loop on unnoticed.
    
    Request Body (JSON):
        {
            "backup_file": "/path/to/backup.json"
        }
    
    Returns:
        JSON with restoration result
    
    Example Response:
        {
            "success": true,
            "secret_id": "db_password",
            "message": "Restored backup for db_password"
        }
    """
    engine = current_app.rotation_engine
    actor = session.get('username', 'unknown')
    
    # Parse request body
    data = request.get_json()
    if not data or 'backup_file' not in data:
        return jsonify({
            'success': False,
            'error': 'backup_file required in request body'
        }), 400
    
    backup_file = data['backup_file']
    
    logger.info(f"Restore requested for backup: {backup_file} by {actor}")
    
    try:
        # Load backup data
        backup_data = engine.backup_manager.restore_backup(backup_file, decrypt=True)
        secret_id = backup_data['secret_id']
        old_value = backup_data['old_value']
        
        # Get the first provider (for now - could be enhanced to specify provider)
        provider = list(engine.providers.values())[0]
        
        # Restore the old value
        success = provider.update_secret(secret_id, old_value)
        
        if success:
            logger.info(f"Successfully restored backup for {secret_id} from {backup_file}")
            audit_log.log(
                "restore", actor, secret_id=secret_id, success=True,
                details={"backup_file": backup_file},
            )
            return jsonify({
                'success': True,
                'secret_id': secret_id,
                'message': f'Restored backup for {secret_id}'
            })
        else:
            logger.error(f"Failed to update secret {secret_id} during restoration")
            audit_log.log(
                "restore", actor, secret_id=secret_id, success=False,
                details={"backup_file": backup_file, "reason": "provider update_secret returned False"},
            )
            return jsonify({
                'success': False,
                'error': 'Failed to update secret'
            }), 500
    
    except FileNotFoundError:
        logger.warning(f"Backup file not found: {backup_file}")
        audit_log.log(
            "restore", actor, success=False,
            details={"backup_file": backup_file, "reason": "backup not found"},
        )
        return jsonify({
            'success': False,
            'error': 'Backup file not found'
        }), 404
    
    except ValueError as e:
        # Raised by BackupManager when the requested path resolves
        # outside the backup directory (path traversal attempt).
        logger.warning(f"Rejected backup path outside backup directory: {backup_file} ({e})")
        audit_log.log(
            "restore", actor, success=False,
            details={"backup_file": backup_file, "reason": "path traversal rejected"},
        )
        return jsonify({
            'success': False,
            'error': 'Invalid backup file'
        }), 400
    
    except Exception as e:
        logger.error(f"Restoration failed: {e}", exc_info=True)
        audit_log.log(
            "restore", actor, success=False,
            details={"backup_file": backup_file, "reason": str(e)},
        )
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500