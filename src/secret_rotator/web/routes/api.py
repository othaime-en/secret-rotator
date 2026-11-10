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
from secret_rotator.component_admin import (
    ComponentExistsError,
    ComponentInUseError,
    ComponentNotFoundError,
    ComponentValidationError,
)
from secret_rotator.config.overrides import ConflictError, OverridesError
from secret_rotator.job_admin import JobExistsError, JobNotFoundError, JobValidationError
from secret_rotator.rotation_engine import RotationInProgressError
from secret_rotator.settings_admin import SettingsValidationError
from secret_rotator.web.rate_limit import limiter

bp = Blueprint("api", __name__)


@bp.route("/status")
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
        "status": "running",
        "providers": len(engine.providers),
        "rotators": len(engine.rotators),
        "jobs": len(engine.rotation_jobs),
    }

    logger.debug(f"Status endpoint called: {status_data}")
    return jsonify(status_data)


@bp.route("/jobs")
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
    jobs_data = {"jobs": engine.rotation_jobs}

    logger.info(f"Jobs endpoint called, returning {len(engine.rotation_jobs)} jobs")
    return jsonify(jobs_data)


@bp.route("/job-status")
@limiter.limit("120 per minute")
def job_status():
    """
    Per-job rotation status for the Jobs list.

    Rate limited separately from the app default: the dashboard polls
    this while a rotation is running.

    Returns:
        JSON with one record per configured job. ``status`` is one of
        ``current``, ``failed``, ``rotating`` or ``never_rotated``;
        timestamps are ISO 8601 UTC, or null when unknown.

    Example Response:
        {
            "jobs": [
                {
                    "name": "database_password",
                    "secret_id": "db_password",
                    "provider": "file_storage",
                    "rotator": "password_gen",
                    "schedule": "weekly",
                    "status": "failed",
                    "last_rotated": "2026-10-08T04:00:01+00:00",
                    "last_attempt": "2026-10-13T04:00:30+00:00",
                    "last_error": "provider update_secret returned False",
                    "next_rotation": "2026-10-14T02:00:00+00:00"
                }
            ]
        }
    """
    return jsonify({"jobs": current_app.job_status.job_summaries()})


def _json_body():
    """The request's JSON object, or None if it has none."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def _job_admin(action):
    """Run a job management action and translate its failures to HTTP."""
    try:
        return action()
    except JobValidationError as e:
        return jsonify({"error": "Invalid job", "fields": e.fields}), 400
    except JobNotFoundError:
        return jsonify({"error": "Job not found"}), 404
    except JobExistsError as e:
        return jsonify({"error": str(e), "fields": {"name": "already exists"}}), 409
    except ConflictError as e:
        return jsonify({"error": str(e), "conflict": True}), 409
    except OverridesError as e:
        return jsonify({"error": str(e)}), 400


def _definition_from(body):
    """The job fields of a request body (everything but the revision)."""
    return {key: value for key, value in body.items() if key != "revision"}


def _revision_from(body):
    """The settings revision the client loaded, if it sent one."""
    revision = (body or {}).get("revision")
    return revision if isinstance(revision, int) and not isinstance(revision, bool) else None


def _job_response(name, status=200, **extra):
    admin = current_app.job_admin
    return (
        jsonify(
            {"job": current_app.job_status.job_summary(name), "revision": admin.revision, **extra}
        ),
        status,
    )


@bp.route("/job-options")
@limiter.limit("120 per minute")
def job_options():
    """
    What the job form can offer.

    Example Response:
        {"providers": ["file_storage"], "rotators": ["password_gen"],
         "schedules": ["every_30_minutes", "every_2_hours", "daily",
                       "weekly", "monthly"],
         "default_schedule": "daily", "revision": 3}
    """
    return jsonify(current_app.job_admin.options())


@bp.route("/jobs", methods=["POST"])
@limiter.limit("30 per minute")
def create_job():
    """
    Create a job. The change is saved in the dashboard's settings file
    (never config.yaml) and applied to the running engine and scheduler.

    Request Body (JSON):
        name, provider, rotator, secret_id (required);
        schedule (optional, e.g. "weekly"; omit to follow the global
        schedule); notification (optional, true/false);
        revision (optional: the settings revision you loaded, to be told
        if something else changed meanwhile)

    Returns:
        201 with the new job's status and the new settings revision;
        400 with per-field messages; 409 if the name is taken or the
        settings changed since ``revision``.
    """
    body = _json_body()
    if body is None:
        return jsonify({"error": "Send the job as a JSON object"}), 400
    actor = session.get("username", "unknown")

    def action():
        name = current_app.job_admin.create(
            _definition_from(body), actor, revision=_revision_from(body)
        )
        return _job_response(name, 201)

    return _job_admin(action)


@bp.route("/jobs/<path:job_name>", methods=["PUT"])
@limiter.limit("30 per minute")
def update_job(job_name):
    """
    Replace a job's settings (everything except its name, which cannot
    change). Same body as POST /api/jobs. Returns 200 with the job, the
    new revision and ``changed`` (the fields that differ; empty if the
    request changed nothing and nothing was saved).
    """
    body = _json_body()
    if body is None:
        return jsonify({"error": "Send the job as a JSON object"}), 400
    actor = session.get("username", "unknown")

    def action():
        changed = current_app.job_admin.update(
            job_name, _definition_from(body), actor, revision=_revision_from(body)
        )
        return _job_response(job_name, 200, changed=changed)

    return _job_admin(action)


@bp.route("/jobs/<path:job_name>", methods=["DELETE"])
@limiter.limit("30 per minute")
def delete_job(job_name):
    """
    Delete a job created in the dashboard, or hide one defined in
    config.yaml (config.yaml itself is not changed; POST .../reset brings
    it back). Optional JSON body: {"revision": N}. Existing backups are kept.
    """
    body = _json_body()
    actor = session.get("username", "unknown")

    def action():
        source = current_app.job_admin.delete(job_name, actor, revision=_revision_from(body))
        return (
            jsonify(
                {"deleted": True, "source": source, "revision": current_app.job_admin.revision}
            ),
            200,
        )

    return _job_admin(action)


@bp.route("/jobs/<path:job_name>/pause", methods=["POST"])
@limiter.limit("30 per minute")
def pause_job(job_name):
    """Pause scheduled rotation of a job (manual "Rotate now" still works,
    "Rotate all" skips it). Optional JSON body: {"revision": N}."""
    body = _json_body()
    actor = session.get("username", "unknown")

    def action():
        changed = current_app.job_admin.set_paused(
            job_name, True, actor, revision=_revision_from(body)
        )
        return _job_response(job_name, 200, changed=changed)

    return _job_admin(action)


@bp.route("/jobs/<path:job_name>/resume", methods=["POST"])
@limiter.limit("30 per minute")
def resume_job(job_name):
    """Resume scheduled rotation of a paused job."""
    body = _json_body()
    actor = session.get("username", "unknown")

    def action():
        changed = current_app.job_admin.set_paused(
            job_name, False, actor, revision=_revision_from(body)
        )
        return _job_response(job_name, 200, changed=changed)

    return _job_admin(action)


@bp.route("/jobs/<path:job_name>/reset", methods=["POST"])
@limiter.limit("30 per minute")
def reset_job(job_name):
    """Drop every dashboard change to a job: a job defined only in the
    dashboard is deleted; one from config.yaml is back as written there."""
    body = _json_body()
    actor = session.get("username", "unknown")

    def action():
        source = current_app.job_admin.reset(job_name, actor, revision=_revision_from(body))
        job = current_app.job_status.job_summary(job_name)
        return (
            jsonify(
                {
                    "reset": True,
                    "source": source,
                    "job": job,
                    "revision": current_app.job_admin.revision,
                }
            ),
            200,
        )

    return _job_admin(action)


@bp.route("/jobs/<path:job_name>/detail")
@limiter.limit("120 per minute")
def job_detail(job_name):
    """
    Everything the job page shows.

    Query Parameters:
        runs (optional): how many recent runs to include, 1-100 (default 20)

    Example Response (abridged):
        {
            "job": {"name": "database_password", "status": "current", ...},
            "config": {
                "secret_id": "db_password", "provider": "file_storage",
                "rotator_name": "password_gen", "schedule": "weekly",
                "notifications": true,
                "rotator": {"type": "Password", "settings": [
                    {"label": "Length", "value": "16"}]}
            },
            "stats": {"runs": 25, "succeeded": 24, "failed": 1,
                      "success_rate": 96.0, "backups": 10},
            "runs": [{"timestamp": "...", "actor": "system",
                      "trigger": "schedule", "success": true,
                      "reason": null, "duration_ms": 1200}],
            "backups": [{"file": "...", "created": "...",
                         "encrypted": true, "readable": true}]
        }

    Only display-safe rotator settings are returned (never connection
    details), and no secret values. 404 if no job has this name.
    """
    try:
        run_limit = int(request.args.get("runs", 20))
    except ValueError:
        return jsonify({"error": "runs must be an integer"}), 400
    if not 1 <= run_limit <= 100:
        return jsonify({"error": "runs must be between 1 and 100"}), 400

    detail = current_app.job_status.job_detail(job_name, run_limit=run_limit)
    if detail is None:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(detail)


@bp.route("/jobs/<path:job_name>/rotate", methods=["POST"])
@limiter.limit("30 per minute")
def rotate_job(job_name):
    """
    Rotate one job now and return the outcome.

    Unlike POST /api/rotate this runs synchronously: a single rotation is
    one provider round trip, so there is nothing to poll. It shares the
    rotation lock with full sweeps, so it can never overlap one.

    Returns:
        200 with ``success`` (whether the rotation worked) and the job's
        updated status record. A rotation that ran but failed is still a
        200 with ``success: false`` and the reason in ``job.last_error``.
        404 if no job has this name; 409 if a rotation is already running.

    Example Response (200):
        {
            "success": true,
            "job": {"name": "database_password", "status": "current", ...}
        }
    """
    actor = session.get("username", "unknown")
    logger.info(f"Single-job rotation of '{job_name}' requested by {actor}")

    try:
        success = current_app.rotation_engine.rotate_job(job_name, actor=actor)
    except KeyError:
        return jsonify({"error": "Job not found"}), 404
    except RotationInProgressError as e:
        logger.info(f"Rotation of '{job_name}' refused: {e}")
        return jsonify({"error": "A rotation is already in progress. Try again shortly."}), 409

    return jsonify({"success": success, "job": current_app.job_status.job_summary(job_name)})


def _activity_filters():
    """Parse the level/q parameters shared by the activity endpoints."""
    return request.args.get("level", "all").strip().lower(), request.args.get("q", "")


@bp.route("/activity")
@limiter.limit("120 per minute")
def activity():
    """
    Audit events for the Activity page, newest first.

    Query Parameters:
        level (optional): all (default), info, warning or error
        q (optional): case-insensitive text to find in the action, actor,
            secret ID or message
        page, per_page (optional): paging, per_page 1-500 (default 100)
        after (optional): a ``seq``; returns only newer events (used by
            the live view) and ignores page/per_page

    Example Response:
        {
            "events": [
                {"seq": 1204, "timestamp": "2026-10-13T04:00:30+00:00",
                 "level": "error", "action": "rotate", "actor": "system",
                 "secret_id": "jwt_secret", "success": false,
                 "message": "jwt_signing_secret rotation failed: timeout"}
            ],
            "total": 311, "page": 1, "per_page": 100, "pages": 4,
            "latest_seq": 1204
        }

    With ``after`` the response is just ``events`` and ``latest_seq``.
    """
    level, query = _activity_filters()
    service = current_app.activity
    try:
        if "after" in request.args:
            after = int(request.args["after"])
            if after < 0:
                raise ValueError("after must not be negative")
            return jsonify(service.since(after, level, query))
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 100))
        return jsonify(service.query(level, query, page, per_page))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/activity/export")
@limiter.limit("10 per minute")
def export_activity():
    """
    Download the matching audit events as CSV or JSON, oldest first.

    Query Parameters:
        format (optional): csv (default) or json
        level, q (optional): the same filters as GET /api/activity

    At most the newest 10,000 matching events are exported; the
    ``X-Export-Truncated`` header is ``true`` when the cap applied. Every
    export is itself recorded in the audit log.
    """
    fmt = request.args.get("format", "csv").strip().lower()
    if fmt not in ("csv", "json"):
        return jsonify({"error": "format must be csv or json"}), 400

    level, query = _activity_filters()
    service = current_app.activity
    try:
        rows, truncated = service.export_rows(level, query)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    actor = session.get("username", "unknown")
    service.record_export(actor, fmt, len(rows))

    body = service.to_csv(rows) if fmt == "csv" else service.to_json(rows)
    response = current_app.response_class(
        body, mimetype="text/csv" if fmt == "csv" else "application/json"
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{service.export_filename(fmt)}"'
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Export-Truncated"] = "true" if truncated else "false"
    return response


@bp.route("/overview")
@limiter.limit("120 per minute")
def overview():
    """
    Everything the Overview page needs in one request: totals, items
    needing attention (failed jobs), per-job status, the latest rotation
    and restore activity, and recent backup verification results.

    Example Response (abridged):
        {
            "summary": {"jobs": 5, "backups": 48, "rotating": 0},
            "attention": [
                {"kind": "job_failed", "job": "jwt_signing_secret",
                 "detail": "timeout", "timestamp": "2026-10-13T04:00:30+00:00"}
            ],
            "jobs": [...],
            "recent_activity": [
                {"timestamp": "...", "action": "rotate", "success": true,
                 "actor": "system", "message": "api_key_stripe rotated"}
            ],
            "verification": {
                "status": "healthy", "last_verification": "...",
                "total": 48, "verified": 48, "failed": 0,
                "recent_runs": [{"timestamp": "...", "ok": true}]
            }
        }
    """
    return jsonify(current_app.job_status.overview())


@bp.route("/rotate", methods=["POST"])
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
    actor = session.get("username", "unknown")
    logger.info(f"Manual rotation triggered via API by {actor}")

    job = current_app.job_manager.start_rotation(actor=actor)
    job["status_url"] = f"/api/rotate/{job['job_id']}"

    if job.get("already_running", False):
        logger.info(
            f"Rotation requested by {actor} but job {job['job_id']} "
            f"is already in progress; returning its status instead of "
            f"starting a new one"
        )
        return jsonify(job), 409

    return jsonify(job), 202


@bp.route("/rotate/<job_id>")
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
        return jsonify({"error": "Job not found"}), 404

    return jsonify(job)


@bp.route("/backups")
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
    secret_id = request.args.get("secret_id")

    try:
        backups = engine.backup_manager.list_backups(secret_id, mask_values=True)

        logger.info(
            f"Backups endpoint called, returning {len(backups)} backups"
            + (f" for secret_id={secret_id}" if secret_id else "")
        )

        return jsonify({"backups": backups})

    except Exception as e:
        logger.error(f"Failed to list backups: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/backup-list")
@limiter.limit("120 per minute")
def backup_list():
    """
    One page of backups for the dashboard, newest first.

    Unlike GET /api/backups this does not decrypt anything and returns
    only file names (never server paths), so it stays fast with
    thousands of backups.

    Query Parameters:
        q (optional): case-insensitive text to look for in the secret ID
        page (optional): 1-based page number, default 1
        per_page (optional): page size, 1-100, default 25

    Example Response:
        {
            "backups": [
                {
                    "file": "db_password_20261012_040000_123456.json",
                    "secret_id": "db_password",
                    "created": "2026-10-12T04:00:00+00:00",
                    "encrypted": true,
                    "readable": true
                }
            ],
            "total": 48, "page": 1, "per_page": 25, "pages": 2
        }
    """
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 25))
    except ValueError:
        return jsonify({"error": "page and per_page must be integers"}), 400

    result = current_app.rotation_engine.backup_manager.list_backup_page(
        query=request.args.get("q"), page=page, per_page=per_page
    )
    return jsonify(result)


@bp.route("/backups/<path:backup_file>")
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

        backup_data["old_value"] = SecretMasker.mask_for_backup_display(backup_data["old_value"])
        backup_data["new_value"] = SecretMasker.mask_for_backup_display(backup_data["new_value"])

        return jsonify(backup_data)

    except FileNotFoundError:
        logger.warning(f"Backup file not found: {decoded_path}")
        return jsonify({"error": "Backup not found"}), 404

    except ValueError as e:
        # Raised by BackupManager when the requested path resolves
        # outside the backup directory (path traversal attempt).
        logger.warning(f"Rejected backup path outside backup directory: {decoded_path} ({e})")
        return jsonify({"error": "Invalid backup file"}), 400

    except Exception as e:
        logger.error(f"Failed to load backup detail: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def _resolve_restore_provider(engine, backup_data, secret_id):
    """
    Work out which registered provider a restore should write to.

    Historically this endpoint always restored to
    ``list(engine.providers.values())[0]`` — the first provider,
    regardless of which one the secret actually belongs to. With more
    than one provider configured (the normal case for anything beyond
    a single-secret demo), that silently wrote the restored value into
    the wrong system.

    Resolution order, most to least reliable:
      1. ``backup_data["provider_name"]`` — recorded at backup-creation
         time (see BackupManager.create_backup_with_checksum) for any
         backup made by this version or later. Exact match.
      2. The configured rotation jobs — if exactly one job's
         ``secret_id`` matches, its ``provider`` field tells us where
         this secret lives even for older backups made before
         provider_name existed.
      3. Last resort: the first registered provider, matching the old
         behavior — only reached for a pre-upgrade backup whose
         secret_id isn't (or is no longer) in any configured job. The
         caller logs a loud warning whenever this path is taken and
         more than one provider is configured, since it's a guess.

    Returns:
        (provider, source) — source is one of "backup_metadata",
        "job_config", or "first_provider_fallback", so the caller can
        decide whether the outcome is trustworthy enough to skip the
        warning. provider is None only if engine.providers is empty.
    """
    provider_name = backup_data.get("provider_name")
    if provider_name:
        provider = engine.providers.get(provider_name)
        if provider is not None:
            return provider, "backup_metadata"
        logger.warning(
            f"Backup for {secret_id} names provider '{provider_name}', "
            f"which is not currently registered — falling back to job "
            f"config / first-provider inference."
        )

    matching_job_providers = {
        job["provider"]
        for job in engine.rotation_jobs
        if job.get("secret_id") == secret_id and job.get("provider") in engine.providers
    }
    if len(matching_job_providers) == 1:
        return engine.providers[next(iter(matching_job_providers))], "job_config"

    if not engine.providers:
        return None, "no_provider"

    return next(iter(engine.providers.values())), "first_provider_fallback"


@bp.route("/restore", methods=["POST"])
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
    actor = session.get("username", "unknown")

    # Parse request body
    data = request.get_json()
    if not data or "backup_file" not in data:
        return jsonify({"success": False, "error": "backup_file required in request body"}), 400

    backup_file = data["backup_file"]

    logger.info(f"Restore requested for backup: {backup_file} by {actor}")

    try:
        # Load backup data
        backup_data = engine.backup_manager.restore_backup(backup_file, decrypt=True)
        secret_id = backup_data["secret_id"]
        old_value = backup_data["old_value"]

        provider, provider_source = _resolve_restore_provider(engine, backup_data, secret_id)

        if provider is None:
            logger.error(
                f"Cannot restore {secret_id}: no provider is registered "
                f"(engine.providers is empty)"
            )
            audit_log.log(
                "restore",
                actor,
                secret_id=secret_id,
                success=False,
                details={"backup_file": backup_file, "reason": "no provider registered"},
            )
            return jsonify({"success": False, "error": "No provider registered"}), 500

        if provider_source != "backup_metadata" and len(engine.providers) > 1:
            # We couldn't confidently identify which provider this
            # secret belongs to, and there's more than one to choose
            # from — restoring to the wrong one silently corrupts a
            # live secret, so this is worth a loud warning even though
            # we still proceed (see _resolve_restore_provider docstring
            # for why guessing is still better than refusing outright).
            logger.warning(
                f"Restoring {secret_id} to provider '{provider.name}' "
                f"({provider_source}) — this backup predates provider "
                f"tracking and {len(engine.providers)} providers are "
                f"configured, so this is a best-effort guess. Verify the "
                f"restored value landed in the right place."
            )

        # Restore the old value
        success = provider.update_secret(secret_id, old_value)

        if success:
            logger.info(
                f"Successfully restored backup for {secret_id} from {backup_file} "
                f"to provider '{provider.name}'"
            )
            audit_log.log(
                "restore",
                actor,
                secret_id=secret_id,
                success=True,
                details={"backup_file": backup_file, "provider": provider.name},
            )
            return jsonify(
                {
                    "success": True,
                    "secret_id": secret_id,
                    "message": f"Restored backup for {secret_id}",
                }
            )
        else:
            logger.error(f"Failed to update secret {secret_id} during restoration")
            audit_log.log(
                "restore",
                actor,
                secret_id=secret_id,
                success=False,
                details={
                    "backup_file": backup_file,
                    "reason": "provider update_secret returned False",
                },
            )
            return jsonify({"success": False, "error": "Failed to update secret"}), 500

    except FileNotFoundError:
        logger.warning(f"Backup file not found: {backup_file}")
        audit_log.log(
            "restore",
            actor,
            success=False,
            details={"backup_file": backup_file, "reason": "backup not found"},
        )
        return jsonify({"success": False, "error": "Backup file not found"}), 404

    except ValueError as e:
        # Raised by BackupManager when the requested path resolves
        # outside the backup directory (path traversal attempt).
        logger.warning(f"Rejected backup path outside backup directory: {backup_file} ({e})")
        audit_log.log(
            "restore",
            actor,
            success=False,
            details={"backup_file": backup_file, "reason": "path traversal rejected"},
        )
        return jsonify({"success": False, "error": "Invalid backup file"}), 400

    except Exception as e:
        logger.error(f"Restoration failed: {e}", exc_info=True)
        audit_log.log(
            "restore",
            actor,
            success=False,
            details={"backup_file": backup_file, "reason": str(e)},
        )
        return jsonify({"success": False, "error": str(e)}), 500


# ---- providers and rotators ---------------------------------------------------
#
# The two kinds have identical management endpoints, so they are registered
# from one definition below:
#
#   GET    /api/<kind>                      list, with the type descriptions
#                                           the forms are built from
#   POST   /api/<kind>                      create
#   PUT    /api/<kind>/<name>               change settings (not name/type)
#   DELETE /api/<kind>/<name>               delete, or hide a config.yaml one
#                                           (409 while jobs use it)
#   POST   /api/<kind>/<name>/reset         drop dashboard changes
#   POST   /api/providers/<name>/test       check a provider can be used
#   POST   /api/rotators/<name>/sample      an example of a saved rotator's output
#   POST   /api/rotators/sample             ... or of unsaved form settings
#
# Changes are saved in the dashboard's settings file (never config.yaml) and
# applied to the running engine immediately. Only the fields listed for each
# type in component_catalog.py are accepted. An optional ``revision`` in the
# body gives conflict detection, as for jobs (409 with ``conflict: true``).


def _component_admin(action, kind):
    """Run a provider/rotator action and translate its failures to HTTP."""
    noun = kind[:-1]
    try:
        return action()
    except ComponentValidationError as e:
        return jsonify({"error": f"Invalid {noun}", "fields": e.fields}), 400
    except ComponentNotFoundError:
        return jsonify({"error": f"{noun.capitalize()} not found"}), 404
    except ComponentExistsError as e:
        return jsonify({"error": str(e), "fields": {"name": "already exists"}}), 409
    except ComponentInUseError as e:
        return jsonify({"error": str(e), "jobs": e.jobs}), 409
    except ConflictError as e:
        return jsonify({"error": str(e), "conflict": True}), 409
    except OverridesError as e:
        return jsonify({"error": str(e)}), 400


def _item_response(kind, name, status=200, **extra):
    service = current_app.component_admin
    item = service.describe(kind, name, service.effective(kind).get(name))
    return jsonify({"item": item, "revision": service.revision, **extra}), status


def _register_component_routes(kind):
    write_limit = limiter.limit("30 per minute")

    def listing():
        return jsonify(current_app.component_admin.listing(kind))

    def create():
        body = _json_body()
        if body is None:
            return jsonify({"error": "Send the definition as a JSON object"}), 400
        actor = session.get("username", "unknown")

        def action():
            name = current_app.component_admin.create(
                kind, _definition_from(body), actor, revision=_revision_from(body)
            )
            return _item_response(kind, name, 201)

        return _component_admin(action, kind)

    def update(name):
        body = _json_body()
        if body is None:
            return jsonify({"error": "Send the definition as a JSON object"}), 400
        actor = session.get("username", "unknown")

        def action():
            changed = current_app.component_admin.update(
                kind, name, _definition_from(body), actor, revision=_revision_from(body)
            )
            return _item_response(kind, name, 200, changed=changed)

        return _component_admin(action, kind)

    def delete(name):
        body = _json_body()
        actor = session.get("username", "unknown")

        def action():
            service = current_app.component_admin
            source = service.delete(kind, name, actor, revision=_revision_from(body))
            return jsonify({"deleted": True, "source": source, "revision": service.revision}), 200

        return _component_admin(action, kind)

    def reset(name):
        body = _json_body()
        actor = session.get("username", "unknown")

        def action():
            service = current_app.component_admin
            source = service.reset(kind, name, actor, revision=_revision_from(body))
            current = service.effective(kind)
            item = service.describe(kind, name, current[name]) if name in current else None
            return (
                jsonify(
                    {"reset": True, "source": source, "item": item, "revision": service.revision}
                ),
                200,
            )

        return _component_admin(action, kind)

    bp.add_url_rule(f"/{kind}", f"{kind}_list", limiter.limit("120 per minute")(listing))
    bp.add_url_rule(f"/{kind}", f"{kind}_create", write_limit(create), methods=["POST"])
    bp.add_url_rule(f"/{kind}/<path:name>", f"{kind}_update", write_limit(update), methods=["PUT"])
    bp.add_url_rule(
        f"/{kind}/<path:name>", f"{kind}_delete", write_limit(delete), methods=["DELETE"]
    )
    bp.add_url_rule(
        f"/{kind}/<path:name>/reset", f"{kind}_reset", write_limit(reset), methods=["POST"]
    )


_register_component_routes("providers")
_register_component_routes("rotators")


@bp.route("/providers/<path:name>/test", methods=["POST"])
@limiter.limit("10 per minute")
def test_provider(name):
    """Check that a saved provider can be reached and used. Returns
    ``{"ok": bool, "message": str}``; 404 for an unknown provider."""
    return _component_admin(
        lambda: jsonify(current_app.component_admin.test_provider(name)), "providers"
    )


@bp.route("/rotators/sample", methods=["POST"])
@limiter.limit("30 per minute")
def sample_unsaved_rotator():
    """An example of what a rotator with the given (unsaved) settings would
    generate, e.g. for the form's preview. The example is random, is never
    stored or logged, and exists only to show the shape of the values.
    Body: the same fields as creating a rotator."""
    body = _json_body()
    if body is None:
        return jsonify({"error": "Send the settings as a JSON object"}), 400
    return _component_admin(
        lambda: jsonify(
            current_app.component_admin.sample_rotator(definition=_definition_from(body))
        ),
        "rotators",
    )


@bp.route("/rotators/<path:name>/sample", methods=["POST"])
@limiter.limit("30 per minute")
def sample_saved_rotator(name):
    """An example of what a saved rotator generates (see POST
    /api/rotators/sample). 404 for an unknown rotator."""
    return _component_admin(
        lambda: jsonify(current_app.component_admin.sample_rotator(name=name)), "rotators"
    )


# ---- settings -------------------------------------------------------------------


@bp.route("/settings")
@limiter.limit("120 per minute")
def get_settings():
    """
    Every setting the dashboard can show, grouped by section.

    Each setting has its constraints (for building the form), its current
    value, where that value comes from (``default``, ``config`` for
    config.yaml, or ``dashboard``), whether changing it needs a restart,
    and whether a change is pending a restart. Credentials are write-only:
    they have ``is_set`` but no value. Settings that cannot be changed here
    carry the reason. Each section also lists ``unavailable`` controls this
    version cannot honour, with why.

    Example Response (abridged):
        {"sections": [{"key": "rotation", "label": "Rotation",
           "settings": [{"key": "rotation.schedule", "kind": "schedule",
                         "value": "daily", "source": "default",
                         "restart": false, "restart_pending": false}],
           "unavailable": [...]}],
         "pending_restart": [], "warnings": [], "revision": 3}
    """
    return jsonify(current_app.settings_admin.listing())


@bp.route("/settings", methods=["PUT"])
@limiter.limit("30 per minute")
def update_settings():
    """
    Save several settings at once, all or nothing.

    Request Body (JSON):
        changes: {"rotation.retry_attempts": 5, ...}
        reset: ["web.port", ...]  (drop the dashboard's value for these)
        revision (optional): the revision you loaded, to be told if the
            settings changed meanwhile

    Saved in the dashboard's settings file, never config.yaml; credentials
    are stored encrypted. Returns 200 with ``saved``, ``reset``,
    ``restart_required`` and the refreshed ``settings`` listing; 400 with
    a message per setting (nothing saved); 409 on a stale revision.
    """
    body = _json_body()
    if body is None:
        return jsonify({"error": "Send the changes as a JSON object"}), 400
    actor = session.get("username", "unknown")
    service = current_app.settings_admin

    try:
        result = service.update(
            body.get("changes", {}), body.get("reset", []), actor, revision=_revision_from(body)
        )
    except SettingsValidationError as e:
        return jsonify({"error": "Invalid settings", "fields": e.fields}), 400
    except ConflictError as e:
        return jsonify({"error": str(e), "conflict": True}), 409
    except OverridesError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({**result, "revision": service.revision, "settings": service.listing()})
