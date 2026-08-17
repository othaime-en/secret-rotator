"""
Background job tracking for full-rotation sweeps triggered via the
web API.
"""

import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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
    Runs RotationEngine.rotate_all_secrets() in a background thread
    per invocation and tracks status/progress for polling.

    Storage is an in-memory, insertion-ordered dict — like the
    RotationEngine and scheduler it wraps, job history does not
    survive a process restart, which is consistent with this app's
    existing single-instance, in-memory design (see the same trade-off
    documented in web/__init__.py for the WSGI server and
    web/rate_limit.py for rate limiting).
    """

    def __init__(self, engine):
        self.engine = engine
        self._jobs: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = threading.Lock()  # guards self._jobs only

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
        Start a new full-rotation job in the background.

        Returns immediately with the new job's initial state
        (status: "queued"). If a rotation started via this manager is
        already queued/running, returns that job's current state
        instead (with an added "already_running": True) rather than
        starting a second overlapping sweep.

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
        unknown (never existed, or has aged out — see
        JOB_RETENTION_SECONDS / MAX_RETAINED_JOBS)."""
        with self._lock:
            job = self._jobs.get(job_id)
            return None if job is None else self._public_view(job)