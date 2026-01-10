import unittest
import tempfile
import sys
from pathlib import Path


class TestRotationScheduler(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        from secret_rotator.scheduler import RotationScheduler
        from secret_rotator.backup_manager import BackupManager

        self.rotation_called = False

        def mock_rotation():
            self.rotation_called = True
            return {"test_job": True}

        self.temp_backup_dir = tempfile.mkdtemp()
        self.backup_manager = BackupManager(backup_dir=self.temp_backup_dir)
        self.scheduler = RotationScheduler(
            rotation_function=mock_rotation, backup_manager=self.backup_manager
        )

    def tearDown(self):
        """Clean up"""
        import shutil

        if self.scheduler.running:
            self.scheduler.stop()
        if Path(self.temp_backup_dir).exists():
            shutil.rmtree(self.temp_backup_dir)

    def test_setup_daily_schedule(self):
        """Test setting up daily schedule"""
        self.scheduler.setup_schedule("daily")
        import schedule

        self.assertTrue(len(schedule.jobs) > 0)

    def test_setup_weekly_schedule(self):
        """Test setting up weekly schedule"""
        self.scheduler.setup_schedule("weekly")
        import schedule

        self.assertTrue(len(schedule.jobs) > 0)

    def test_setup_interval_schedule(self):
        """Test setting up interval schedule"""
        self.scheduler.setup_schedule("every_30_minutes")
        import schedule

        self.assertTrue(len(schedule.jobs) > 0)

    def test_start_stop_scheduler(self):
        """Test starting and stopping scheduler"""
        self.scheduler.setup_schedule("daily")
        self.scheduler.start()
        self.assertTrue(self.scheduler.running)

        self.scheduler.stop()
        self.assertFalse(self.scheduler.running)

    def test_run_rotation(self):
        """Test running rotation through scheduler"""
        self.scheduler._run_rotation()
        self.assertTrue(self.rotation_called)
