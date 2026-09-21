"""
Background job tracking for full-rotation sweeps triggered via the
web API.

Two modes, chosen by `distributed.enabled` in config (same switch
distributed_lock.py uses):

  - Local (default, distributed.enabled: false): a rotation runs in a
    background thread of *this* process, tracked in an in-memory dict.
    Unchanged from Phase 2 — single-instance deployments need no new
    infrastructure.
  - Distributed (distributed.enabled: true): a rotation is enqueued as
    an RQ job in Redis (see job_queue.py) and executed by whichever
    `secret-rotator --mode worker` process picks it up next — possibly
    on a different instance entirely. Job state lives in Redis, not in
    this process's memory, so GET /api/rotate/<job_id> answers
    correctly regardless of which instance handles the request.

Either way, routes/api.py calls exactly the same two methods
(start_rotation, get_job) and gets back the exact same dict shape —
the mode switch is invisible above this class.
"""

import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from secret_rotator.config.settings import settings
from secret_rotator.rotation_engine import RotationInProgressError
from secret_rotator.utils.logger import logger

# How many finished (completed/failed) jobs to keep around for
# polling after they're done, and for how long. Bounds memory growth
# in a process that's been up a long time; a user checking on a job
# they triggered a few minutes ago is the case this serves — nobody
# needs job history from last week.
MAX_RETAINED_JOBS = 100
JOB_RETENTION_SECONDS = 60 * 60  # 1 hour


class RotationJobManager:
    """
    Starts and tracks full-rotation-sweep jobs triggered via
    POST /api/rotate. See module docstring for the two modes.

    Local-mode storage is an in-memory, insertion-ordered dict — like
    the RotationEngine and scheduler it wraps, job history does not
    survive a process restart in this mode. Distributed mode's job
    history lives in Redis instead (see job_queue.py) and survives
    this process restarting, though not Redis data loss.
    """

    def __init__(self, engine):
        self.engine = engine
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()  # guards self._jobs only (local mode)

    @staticmethod
    def _distributed_enabled() -> bool:
        return bool(settings.get("distributed.enabled", False))

    def _prune_locked(self) -> None:
        """Drop expired/excess finished jobs. Caller must hold self._lock."""
        now = time.monotonic()
        for job_id in list(self._jobs.keys()):
            job = self._jobs[job_id]
            finished_at = job.get("_finished_monotonic")
            if finished_at is not None and now - finished_at > JOB_RETENTION_SECONDS:
                del self._jobs[job_id]

        while len(self._jobs) > MAX_RETAINED_JOBS:
            # Oldest first (insertion order); a running job is always
            # more recent than this would ever need to evict in
            # practice, but skip it defensively just in case.
            oldest_id = next(iter(self._jobs))
            if self._jobs[oldest_id]["status"] in ("completed", "failed"):
                del self._jobs[oldest_id]
            else:
                break

    @staticmethod
    def _public_view(job: Dict[str, Any]) -> Dict[str, Any]:
        """Strip internal (leading-underscore) bookkeeping fields before
        handing a job dict back to a caller outside this class."""
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def start_rotation(self, actor: str) -> Dict[str, Any]:
        """
        Start a new full-rotation job.

        Returns immediately with the new job's initial state
        (status: "queued"). If a rotation is already queued/running —
        tracked here in local mode, or anywhere in the cluster in
        distributed mode — returns that job's current state instead
        (with an added "already_running": True) rather than starting a
        second overlapping sweep.
        """
        if self._distributed_enabled():
            return self._start_rotation_distributed(actor)
        return self._start_rotation_local(actor)

    def _start_rotation_distributed(self, actor: str) -> Dict[str, Any]:
        from secret_rotator.job_queue import enqueue_rotation, find_in_flight_job, get_queue, job_to_view

        queue = get_queue()
        existing = find_in_flight_job(queue)
        if existing is not None:
            view = job_to_view(existing)
            view["already_running"] = True
            logger.info(
                f"Rotation requested by {actor} but job {view['job_id']} "
                f"is already in progress; returning its status instead of "
                f"enqueuing a new one"
            )
            return view

        job = enqueue_rotation(actor)
        logger.info(f"Enqueued rotation job {job.id} for {actor}")
        return job_to_view(job)

    def _start_rotation_local(self, actor: str) -> Dict[str, Any]:
        """
        Note this only catches overlap with *other API-triggered*
        jobs tracked here. A rotation triggered independently by the
        scheduler isn't tracked by this manager at all — that case is
        still caught correctly, just one layer down: the new job will
        start, immediately hit RotationEngine's own lock (see
        RotationInProgressError), and land in "failed" status with a
        clear error message rather than silently overlapping.
        """
        with self._lock:
            self._prune_locked()
            for job in reversed(self._jobs.values()):
                if job["status"] in ("queued", "running"):
                    view = self._public_view(job)
                    view["already_running"] = True
                    return view

            job_id = str(uuid.uuid4())
            job: Dict[str, Any] = {
                "job_id": job_id,
                "status": "queued",
                "actor": actor,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": None,
                "finished_at": None,
                "progress": {"completed": 0, "total": len(self.engine.rotation_jobs)},
                "results": {},
                "error": None,
                "_finished_monotonic": None,
            }
            self._jobs[job_id] = job

        thread = threading.Thread(
            target=self._run,
            args=(job_id, actor),
            daemon=True,
            name=f"rotation-job-{job_id[:8]}",
        )
        thread.start()

        return self._public_view(job)

    def _run(self, job_id: str, actor: str) -> None:
        with self._lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["started_at"] = datetime.now(timezone.utc).isoformat()

        def on_job_complete(job_name, success, completed, total):
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                job["results"][job_name] = success
                job["progress"] = {"completed": completed, "total": total}

        try:
            results = self.engine.rotate_all_secrets(
                actor=actor, on_job_complete=on_job_complete
            )
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "completed"
                job["results"] = results

        except RotationInProgressError as e:
            # Lost a race to a concurrently-started run (the
            # scheduler, or — in principle — another request that
            # slipped in between this thread starting and acquiring
            # the engine's lock). Report it as a failed job rather
            # than leaving the client polling a job that will never
            # finish.
            logger.warning(f"Background rotation job {job_id} could not start: {e}")
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["error"] = str(e)

        except Exception as e:
            logger.error(f"Background rotation job {job_id} failed: {e}", exc_info=True)
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "failed"
                job["error"] = str(e)

        finally:
            with self._lock:
                job = self._jobs[job_id]
                job["finished_at"] = datetime.now(timezone.utc).isoformat()
                job["_finished_monotonic"] = time.monotonic()

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Return the current state of a job, or None if job_id is
        unknown (never existed, or has aged out).

        Local mode: aged out means past JOB_RETENTION_SECONDS /
        MAX_RETAINED_JOBS. Distributed mode: aged out means past
        job_queue.JOB_RETENTION_SECONDS in Redis (result_ttl/failure_ttl
        on the RQ job), or the job never existed in this Redis at all.
        """
        if self._distributed_enabled():
            return self._get_job_distributed(job_id)
        return self._get_job_local(job_id)

    def _get_job_distributed(self, job_id: str) -> Optional[Dict[str, Any]]:
        from rq.exceptions import NoSuchJobError
        from rq.job import Job

        from secret_rotator.job_queue import get_queue, job_to_view

        queue = get_queue()
        try:
            job = Job.fetch(job_id, connection=queue.connection)
        except NoSuchJobError:
            return None
        return job_to_view(job)

    def _get_job_local(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else self._public_view(job)