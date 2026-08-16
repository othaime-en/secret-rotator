import os
import shutil
import tempfile
import time
import unittest

from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.rotation_engine import RotationEngine, RotationInProgressError
from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.web.job_manager import RotationJobManager


def _wait_until(predicate, timeout=5, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class TestRotationJobManager(unittest.TestCase):
    """Covers roadmap Phase 2 ("background jobs for /api/rotate"): a
    full rotation sweep should run off the calling thread, report live
    progress, and never let two sweeps overlap in the same process."""

    def setUp(self):
        self.engine = RotationEngine()

        self.temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.temp_file.write('{"secret_a": "old_a", "secret_b": "old_b"}')
        self.temp_file.close()
        self.temp_backup_dir = tempfile.mkdtemp()

        self.provider = FileSecretProvider(
            "test_provider", {"file_path": self.temp_file.name}
        )
        self.engine.register_provider(self.provider)

        self.rotator = PasswordRotator(
            "test_rotator",
            {"length": 12, "use_symbols": True, "use_numbers": True,
             "use_uppercase": True, "use_lowercase": True},
        )
        self.engine.register_rotator(self.rotator)

        self.engine.add_rotation_job({
            "name": "job_a", "provider": "test_provider",
            "rotator": "test_rotator", "secret_id": "secret_a",
        })
        self.engine.add_rotation_job({
            "name": "job_b", "provider": "test_provider",
            "rotator": "test_rotator", "secret_id": "secret_b",
        })

        # rotate_all_secrets sleeps 1s between jobs by design (avoid
        # overwhelming downstream systems) — not worth waiting out in
        # every test, so speed it up here.
        import secret_rotator.rotation_engine as rotation_engine_module
        self._orig_sleep = rotation_engine_module.time.sleep
        rotation_engine_module.time.sleep = lambda _seconds: None

        self.manager = RotationJobManager(self.engine)

    def tearDown(self):
        import secret_rotator.rotation_engine as rotation_engine_module
        rotation_engine_module.time.sleep = self._orig_sleep

        os.unlink(self.temp_file.name)
        if os.path.exists(self.temp_backup_dir):
            shutil.rmtree(self.temp_backup_dir)

    def test_start_rotation_returns_immediately_with_queued_job(self):
        """The call should return before rotation finishes — this is
        the whole point of backgrounding it."""
        job = self.manager.start_rotation(actor="alice")
        self.assertIn(job["status"], ("queued", "running"))
        self.assertEqual(job["actor"], "alice")
        self.assertEqual(job["progress"], {"completed": 0, "total": 2})

    def test_job_reaches_completed_with_full_results(self):
        job = self.manager.start_rotation(actor="alice")
        job_id = job["job_id"]

        completed = _wait_until(
            lambda: self.manager.get_job(job_id)["status"] == "completed"
        )
        self.assertTrue(completed, "job did not complete in time")

        final = self.manager.get_job(job_id)
        self.assertEqual(final["progress"], {"completed": 2, "total": 2})
        self.assertEqual(set(final["results"].keys()), {"job_a", "job_b"})
        self.assertIsNone(final["error"])
        self.assertIsNotNone(final["finished_at"])

    def test_progress_is_visible_while_running(self):
        """A poller checking mid-run should see partial progress, not
        just nothing-then-everything."""
        job = self.manager.start_rotation(actor="alice")
        job_id = job["job_id"]

        saw_partial_progress = _wait_until(
            lambda: self.manager.get_job(job_id)["progress"]["completed"] >= 1
        )
        self.assertTrue(saw_partial_progress, "never observed partial progress")

    def test_get_job_returns_none_for_unknown_id(self):
        self.assertIsNone(self.manager.get_job("does-not-exist"))

    def test_second_call_while_running_reports_already_running(self):
        first = self.manager.start_rotation(actor="alice")
        second = self.manager.start_rotation(actor="bob")

        self.assertTrue(second.get("already_running"))
        self.assertEqual(second["job_id"], first["job_id"])

        # Let the background thread finish so it doesn't leak into
        # other tests.
        _wait_until(lambda: self.manager.get_job(first["job_id"])["status"] == "completed")

    def test_engine_rejects_overlapping_sweeps_directly(self):
        """The lock lives on RotationEngine itself, not just the job
        manager — a second direct call (e.g. from the scheduler) must
        also be rejected while a job-manager-started sweep is running."""
        job = self.manager.start_rotation(actor="alice")

        # The background thread may not have acquired the engine lock
        # yet the instant start_rotation() returns; give it a moment.
        _wait_until(lambda: self.manager.get_job(job["job_id"])["status"] == "running")

        with self.assertRaises(RotationInProgressError):
            self.engine.rotate_all_secrets(actor="scheduler")

        _wait_until(lambda: self.manager.get_job(job["job_id"])["status"] == "completed")

    def test_lock_is_released_after_completion_allowing_a_new_run(self):
        first = self.manager.start_rotation(actor="alice")
        _wait_until(lambda: self.manager.get_job(first["job_id"])["status"] == "completed")

        second = self.manager.start_rotation(actor="bob")
        self.assertFalse(second.get("already_running", False))
        self.assertNotEqual(second["job_id"], first["job_id"])

        _wait_until(lambda: self.manager.get_job(second["job_id"])["status"] == "completed")


if __name__ == "__main__":
    unittest.main()