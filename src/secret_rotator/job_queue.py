"""
RQ-backed job queue for full-rotation sweeps.

This is the multi-instance-safe counterpart to
web/job_manager.RotationJobManager's original in-process background
thread. The problem it solves: a background thread only exists in the
process that started it. If your dashboard's POST /api/rotate lands on
instance A but the RQ worker consuming the resulting job happens to be
instance B (or a dedicated worker process/container), the rotation
still runs — with an in-process thread, it simply couldn't, since
nothing outside instance A would ever know the job existed.

Everything here is only used when `distributed.enabled: true` in
config - see web/job_manager.py for the mode switch. With it false,
RotationJobManager keeps using the original in-process
threading.Thread approach unchanged, and this module (and Redis) is
never touched.

Job identity and status live entirely in Redis (that's RQ's job): no
job state is kept in this process's memory, so any instance's
GET /api/rotate/<job_id> can answer correctly regardless of which
instance enqueued the job or which worker is executing it.
"""

import sys
import traceback
from typing import Any, Dict, Optional

from secret_rotator.config.settings import settings
from secret_rotator.utils.logger import logger

DEFAULT_QUEUE_NAME = "secret-rotator-rotations"

# Maximum seconds RQ lets a single rotation-sweep job run before
# killing it. Generous by default for the same reason as
# distributed.rotation_lock_timeout in distributed_lock.py: a large
# job list at the existing 1s-per-job pacing can legitimately take a
# while.
DEFAULT_JOB_TIMEOUT = 3600

# How long a finished job's result/status stays queryable in Redis
# after it completes — mirrors job_manager.py's JOB_RETENTION_SECONDS
# so both modes behave the same from the dashboard's point of view.
JOB_RETENTION_SECONDS = 60 * 60  # 1 hour

_RQ_STATUS_TO_PUBLIC = {
    "queued": "queued",
    "deferred": "queued",
    "scheduled": "queued",
    "created": "queued",
    "started": "running",
    "finished": "completed",
    "failed": "failed",
    "stopped": "failed",
    "canceled": "failed",
}


def _redis_url() -> str:
    import os

    url = os.getenv("REDIS_URL") or settings.get("distributed.redis_url")
    if not url:
        raise RuntimeError(
            "distributed.enabled is true but no Redis connection is "
            "configured. Set distributed.redis_url in config.yaml or "
            "the REDIS_URL environment variable."
        )
    return url


def get_queue():
    """Return the RQ Queue used for full-rotation-sweep jobs.

    Only call this when `distributed.enabled` is true — see
    web/job_manager.py for the mode switch that decides that.
    """
    from redis import Redis
    from rq import Queue

    connection = Redis.from_url(_redis_url(), socket_connect_timeout=5, socket_timeout=5)
    queue_name = settings.get("distributed.queue_name", DEFAULT_QUEUE_NAME)
    return Queue(queue_name, connection=connection)


def find_in_flight_job(queue) -> Optional[Any]:
    """Return the first job that's queued or actively running on
    `queue`, or None.

    Used to dedupe rotation triggers across instances: if a sweep is
    already in flight — started by this instance's dashboard, another
    instance's dashboard, or the scheduler — a new trigger should
    report that existing job rather than enqueue a duplicate that's
    just going to fail once a worker picks it up and it hits
    RotationEngine's own distributed lock.
    """
    from rq.registry import StartedJobRegistry

    for job_id in queue.job_ids:  # queued, not yet picked up by a worker
        job = queue.fetch_job(job_id)
        if job is not None:
            return job

    started = StartedJobRegistry(queue=queue)
    for job_id in started.get_job_ids():
        job = queue.fetch_job(job_id)
        if job is not None:
            return job

    return None


def enqueue_rotation(actor: str) -> Any:
    """Enqueue a new rotation-sweep job. Returns the RQ Job."""
    from secret_rotator import bootstrap

    queue = get_queue()

    # Build a throwaway engine just to size `total` for the initial
    # progress readout — the worker builds its own engine again when
    # it actually runs the job (see run_rotation_job). Cheap: this is
    # config parsing + provider connection checks, not a rotation.
    engine, _, _ = bootstrap.build_rotation_engine()
    total = len(engine.rotation_jobs)

    job_timeout = settings.get("distributed.job_timeout", DEFAULT_JOB_TIMEOUT)

    job = queue.enqueue(
        run_rotation_job,
        actor,
        job_timeout=job_timeout,
        result_ttl=JOB_RETENTION_SECONDS,
        failure_ttl=JOB_RETENTION_SECONDS,
        meta={
            "actor": actor,
            "progress": {"completed": 0, "total": total},
            "results": {},
        },
    )
    return job


def run_rotation_job(actor: str) -> Dict[str, bool]:
    """Entry point executed by an RQ worker process (`secret-rotator
    --mode worker`) — never called directly by the web process.

    Builds a fresh RotationEngine from the current config (see
    bootstrap.py) since a worker has no access to any other process's
    in-memory objects, then runs a full rotation sweep. Updates this
    job's `meta.progress`/`meta.results` as each job in the sweep
    completes so GET /api/rotate/<job_id> shows live progress —
    mirroring the on_job_complete callback the in-process job manager
    already used.

    RotationInProgressError propagates normally if this job lost a
    race for RotationEngine's distributed lock (e.g. two workers
    picked up jobs enqueued by two instances at nearly the same
    moment) — RQ records that as a failed job, same as any other
    exception, and web/job_manager.py surfaces it the same way the
    in-process mode always has.
    """
    from rq import get_current_job

    from secret_rotator import bootstrap

    current_job = get_current_job()
    engine, _, _ = bootstrap.build_rotation_engine()

    if current_job is not None:
        current_job.meta["progress"] = {"completed": 0, "total": len(engine.rotation_jobs)}
        current_job.meta["results"] = {}
        current_job.save_meta()

    def on_job_complete(job_name, success, completed, total):
        if current_job is None:
            return
        current_job.meta["progress"] = {"completed": completed, "total": total}
        current_job.meta.setdefault("results", {})[job_name] = success
        current_job.save_meta()

    return engine.rotate_all_secrets(actor=actor, on_job_complete=on_job_complete)


def job_to_view(job) -> Dict[str, Any]:
    """Convert an RQ Job into the same public dict shape
    web/job_manager.py's in-process mode has always returned, so
    routes/api.py and the dashboard need no changes regardless of
    which mode is active.

    Uses job.latest_result()/job.return_value() rather than the
    older job.exc_info/job.result properties — both are available on
    rq==2.1.0 (the pinned version) and confirmed working here against
    fakeredis[lua] in tests/test_job_queue.py; the older properties
    are soft-deprecated as of 2.1.0 and print a DeprecationWarning on
    every call.
    """
    job.refresh()
    rq_status = job.get_status(refresh=False).value
    status = _RQ_STATUS_TO_PUBLIC.get(rq_status, "failed")

    error = None
    if status == "failed":
        result = job.latest_result()
        if result is not None and result.exc_string:
            # exc_string is a full traceback string; surface just the
            # final line (the exception message) rather than dumping
            # a stack trace into an API response.
            lines = [line for line in result.exc_string.strip().splitlines() if line.strip()]
            error = lines[-1] if lines else "Job failed"
        else:
            error = "Job failed"

    if status == "completed":
        return_value = job.return_value()
        results = return_value if isinstance(return_value, dict) else job.meta.get("results", {})
    else:
        results = job.meta.get("results", {})

    default_progress = {"completed": 0, "total": 0}

    return {
        "job_id": job.id,
        "status": status,
        "actor": job.meta.get("actor", "unknown"),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.ended_at.isoformat() if job.ended_at else None,
        "progress": job.meta.get("progress", default_progress),
        "results": results,
        "error": error,
    }


def run_worker() -> None:
    """Start an RQ worker that pops rotation jobs off the queue and
    executes them via run_rotation_job(). Called by
    `secret-rotator --mode worker`.

    Only meaningful when distributed.enabled: true — with it false
    there's no queue to listen to; single-instance deployments run
    rotations directly, in-process, via the scheduler and
    RotationJobManager's background-thread mode.
    """
    if not settings.get("distributed.enabled", False):
        logger.error(
            "`secret-rotator --mode worker` requires distributed.enabled: true "
            "in config.yaml (plus distributed.redis_url or REDIS_URL). "
            "Single-instance deployments don't need a worker process — "
            "rotations already run directly via the scheduler and the "
            "web dashboard's background thread."
        )
        sys.exit(1)

    from rq import Worker

    try:
        queue = get_queue()
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    logger.info(f"Starting RQ worker on queue '{queue.name}'")
    worker = Worker([queue], connection=queue.connection)
    try:
        worker.work(with_scheduler=False)
    except Exception:
        logger.error(f"RQ worker crashed:\n{traceback.format_exc()}")
        raise