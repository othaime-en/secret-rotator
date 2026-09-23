import os
import tempfile
import unittest
from unittest.mock import patch

import fakeredis

from secret_rotator.config.settings import settings
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.web.job_manager import RotationJobManager


def _build_test_engine(file_path: str) -> RotationEngine:
    engine = RotationEngine()
    provider = FileSecretProvider("test_provider", {"file_path": file_path})
    engine.register_provider(provider)
    rotator = PasswordRotator(
        "test_rotator",
        {
            "length": 12,
            "use_symbols": True,
            "use_numbers": True,
            "use_uppercase": True,
            "use_lowercase": True,
        },
    )
    engine.register_rotator(rotator)
    engine.add_rotation_job(
        {
            "name": "job_a",
            "provider": "test_provider",
            "rotator": "test_rotator",
            "secret_id": "secret_a",
        }
    )
    engine.add_rotation_job(
        {
            "name": "job_b",
            "provider": "test_provider",
            "rotator": "test_rotator",
            "secret_id": "secret_b",
        }
    )
    return engine


class TestRotationJobManagerDistributedMode(unittest.TestCase):
    """Exercises RotationJobManager's distributed-mode path
    (distributed.enabled: true) against an in-memory fakeredis instead
    of a real Redis server. This is the multi-instance-safe
    counterpart to tests/test_rotation_jobs.py, which covers the
    default local (in-process thread) mode — every test here has a
    same-named local-mode sibling there, so the two modes' behavior
    can be compared directly.

    A real worker process is `secret-rotator --mode worker`; here,
    `_run_pending_jobs()` plays that role synchronously via RQ's
    SimpleWorker (no fork — fakeredis's in-memory store isn't shared
    across a forked child process, so the default forking Worker
    can't be used against it. This is a testing-only substitution: a
    real worker with a real Redis uses the default Worker.)
    """

    def setUp(self):
        DistributedLock.reset_local_locks()
        self._original_enabled = settings.get("distributed.enabled", False)
        self._original_url = settings.get("distributed.redis_url", None)
        settings.set("distributed.enabled", True)
        settings.set("distributed.redis_url", "redis://localhost:6379/0")

        self.fake_redis = fakeredis.FakeRedis()

        self.temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.temp_file.write('{"secret_a": "old_a", "secret_b": "old_b"}')
        self.temp_file.close()

        self.engine = _build_test_engine(self.temp_file.name)

        # rotate_all_secrets sleeps 1s between jobs by design; not
        # worth waiting out in tests (same trick as test_rotation_jobs.py).
        import secret_rotator.rotation_engine as rotation_engine_module

        self._orig_sleep = rotation_engine_module.time.sleep
        rotation_engine_module.time.sleep = lambda _s: None

        # Every place that builds a Redis connection (get_queue(),
        # DistributedLock) should get the same fakeredis instance
        # instead of trying to reach a real server.
        redis_patcher = patch("redis.Redis.from_url", return_value=self.fake_redis)
        self.addCleanup(redis_patcher.stop)
        redis_patcher.start()

        # bootstrap.build_rotation_engine() is what a real worker
        # process calls to build ITS OWN engine from config.yaml —
        # patch it to return a fresh copy of our test engine instead
        # of trying to load a real config file.
        bootstrap_patcher = patch(
            "secret_rotator.bootstrap.build_rotation_engine",
            side_effect=lambda: (_build_test_engine(self.temp_file.name), None, None),
        )
        self.addCleanup(bootstrap_patcher.stop)
        bootstrap_patcher.start()

        self.manager = RotationJobManager(self.engine)

    def tearDown(self):
        import secret_rotator.rotation_engine as rotation_engine_module

        rotation_engine_module.time.sleep = self._orig_sleep
        settings.set("distributed.enabled", self._original_enabled)
        if self._original_url is not None:
            settings.set("distributed.redis_url", self._original_url)
        os.unlink(self.temp_file.name)

    def _run_pending_jobs(self):
        from rq.worker import SimpleWorker

        from secret_rotator.job_queue import get_queue

        queue = get_queue()
        worker = SimpleWorker([queue], connection=queue.connection)
        worker.work(burst=True)

    def test_start_rotation_enqueues_and_reports_queued(self):
        job = self.manager.start_rotation(actor="alice")
        self.assertIn(job["status"], ("queued", "running"))
        self.assertEqual(job["actor"], "alice")
        self.assertEqual(job["progress"], {"completed": 0, "total": 2})

    def test_job_completes_with_full_results_after_worker_runs(self):
        job = self.manager.start_rotation(actor="alice")
        self._run_pending_jobs()

        final = self.manager.get_job(job["job_id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(set(final["results"].keys()), {"job_a", "job_b"})
        self.assertIsNone(final["error"])
        self.assertIsNotNone(final["finished_at"])

    def test_get_job_returns_none_for_unknown_id(self):
        self.assertIsNone(self.manager.get_job("does-not-exist"))

    def test_second_call_while_queued_reports_already_running(self):
        first = self.manager.start_rotation(actor="alice")
        second = self.manager.start_rotation(actor="bob")

        self.assertTrue(second.get("already_running"))
        self.assertEqual(second["job_id"], first["job_id"])

        self._run_pending_jobs()  # drain so nothing leaks into other tests

    def test_lock_is_released_after_completion_allowing_a_new_run(self):
        """The distributed lock guarding rotate_all_secrets() (Commit 1)
        is global to the "rotation-sweep" resource, not tied to any one
        RotationEngine instance — a fresh engine built by a second
        worker invocation must still see the lock as free once the
        first run released it."""
        first = self.manager.start_rotation(actor="alice")
        self._run_pending_jobs()
        self.assertEqual(self.manager.get_job(first["job_id"])["status"], "completed")

        second = self.manager.start_rotation(actor="bob")
        self.assertFalse(second.get("already_running", False))
        self.assertNotEqual(second["job_id"], first["job_id"])

        self._run_pending_jobs()
        self.assertEqual(self.manager.get_job(second["job_id"])["status"], "completed")

    def test_run_rotation_job_fails_cleanly_if_lock_already_held(self):
        """If the rotation-sweep lock is already held (e.g. by another
        worker mid-run, or another instance's direct call) when a
        worker picks up a job, it must fail with a clear error rather
        than corrupt anything or hang — this is what protects against
        two jobs, enqueued by two different instances before either
        could see the other, actually executing at the same time.

        Exercises this directly against run_rotation_job() rather than
        through two queued jobs: draining a queue via a single
        SimpleWorker is strictly sequential (job 1 fully finishes and
        releases the lock before job 2 starts), so it can never
        actually reproduce two jobs racing for the lock at once — this
        gets the same guarantee under direct control instead."""
        from secret_rotator.distributed_lock import distributed_lock
        from secret_rotator.job_queue import run_rotation_job
        from secret_rotator.rotation_engine import RotationInProgressError

        lock = distributed_lock("rotation-sweep", timeout=60, blocking_timeout=0)
        lock.acquire()
        try:
            with self.assertRaises(RotationInProgressError):
                run_rotation_job("bob")
        finally:
            lock.release()

    def test_missing_redis_url_raises_clearly(self):
        """distributed.enabled: true with no Redis configured must
        fail loudly, matching distributed_lock.py's philosophy —
        never silently fall back to unprotected/no-op behavior."""
        settings.set("distributed.redis_url", None)
        with self.assertRaises(RuntimeError):
            self.manager.start_rotation(actor="alice")


if __name__ == "__main__":
    unittest.main()
