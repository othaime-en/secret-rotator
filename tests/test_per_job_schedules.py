"""Tests for per-job rotation schedules: schedule parsing, the scheduler's
per-schedule rotation entries, and RotationEngine.rotate_jobs."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import schedule

from secret_rotator.audit_log import AuditLog
from secret_rotator.distributed_lock import DistributedLock
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine, RotationInProgressError
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.schedule_spec import (
    SCHEDULE_CHOICES,
    is_valid_schedule,
    next_monthly,
    parse_schedule,
)
from secret_rotator.scheduler import ROTATION_TAG, RotationScheduler


class TestParseSchedule(unittest.TestCase):
    def test_named_schedules(self):
        for name in ("daily", "weekly", "monthly"):
            spec = parse_schedule(name)
            self.assertEqual((spec.name, spec.kind), (name, name))

    def test_intervals(self):
        spec = parse_schedule("every_30_minutes")
        self.assertEqual(
            (spec.name, spec.kind, spec.interval, spec.unit),
            ("every_30_minutes", "interval", 30, "minutes"),
        )
        self.assertEqual(parse_schedule("every_2_hours").unit, "hours")

    def test_case_and_whitespace_are_normalised(self):
        self.assertEqual(parse_schedule("  Daily ").name, "daily")
        self.assertEqual(parse_schedule("EVERY_5_MINUTES").name, "every_5_minutes")

    def test_singular_units_are_the_same_schedule_as_plural(self):
        self.assertEqual(parse_schedule("every_1_hour").name, "every_1_hours")
        self.assertEqual(parse_schedule("every_1_minute").name, "every_1_minutes")
        self.assertEqual(parse_schedule("every_1_hour"), parse_schedule("every_1_hours"))

    def test_invalid_values(self):
        for value in (
            None,
            "",
            "hourly",
            "every_0_minutes",
            "every_x_hours",
            "every_5_days",
            "every__hours",
            "every_5",
            5,
            ["daily"],
            "daily weekly",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    parse_schedule(value)
                self.assertIn("monthly", str(caught.exception))
                self.assertFalse(is_valid_schedule(value))

    def test_every_offered_choice_is_valid(self):
        for choice in SCHEDULE_CHOICES:
            self.assertTrue(is_valid_schedule(choice), choice)


class TestNextMonthly(unittest.TestCase):
    def test_mid_month_goes_to_the_next_month(self):
        self.assertEqual(next_monthly(datetime(2026, 10, 15, 9, 30)), datetime(2026, 11, 1, 2, 0))

    def test_early_on_the_first_is_today(self):
        self.assertEqual(next_monthly(datetime(2026, 10, 1, 1, 0)), datetime(2026, 10, 1, 2, 0))

    def test_exactly_at_the_time_moves_on(self):
        self.assertEqual(next_monthly(datetime(2026, 10, 1, 2, 0)), datetime(2026, 11, 1, 2, 0))

    def test_after_the_time_on_the_first_moves_on(self):
        self.assertEqual(next_monthly(datetime(2026, 10, 1, 2, 0, 1)), datetime(2026, 11, 1, 2, 0))

    def test_december_rolls_into_the_next_year(self):
        self.assertEqual(next_monthly(datetime(2026, 12, 20)), datetime(2027, 1, 1, 2, 0))

    def test_month_ends(self):
        self.assertEqual(next_monthly(datetime(2026, 1, 31, 23, 59)), datetime(2026, 2, 1, 2, 0))


def job(name, schedule_name=None):
    data = {"name": name, "provider": "p", "rotator": "r", "secret_id": name}
    if schedule_name:
        data["schedule"] = schedule_name
    return data


class SchedulerFixture(unittest.TestCase):
    def setUp(self):
        schedule.clear()
        self.addCleanup(schedule.clear)
        self.jobs = [job("a"), job("b")]
        self.rotate_all = MagicMock(return_value={"a": True, "b": True})
        self.rotate_some = MagicMock(side_effect=lambda names: {n: True for n in names})
        self.scheduler = RotationScheduler(
            rotation_function=self.rotate_all,
            backup_manager=MagicMock(),
            rotate_jobs_function=self.rotate_some,
            jobs_provider=lambda: self.jobs,
        )

    def entries(self):
        return schedule.get_jobs(ROTATION_TAG)

    def entry_for(self, name):
        found = schedule.get_jobs(f"{ROTATION_TAG}:{name}")
        self.assertEqual(len(found), 1, name)
        return found[0]

    def fire(self, name):
        self.entry_for(name).run()


class TestSchedulerSetup(SchedulerFixture):
    def test_without_overrides_there_is_one_entry_and_it_rotates_everything(self):
        self.scheduler.setup_schedule("daily")
        self.assertEqual(len(self.entries()), 1)
        self.fire("daily")
        self.rotate_all.assert_called_once_with()
        self.rotate_some.assert_not_called()

    def test_each_distinct_schedule_gets_its_own_entry(self):
        self.jobs = [job("a"), job("b"), job("c", "weekly"), job("d", "every_2_hours")]
        self.scheduler.setup_schedule("daily")
        self.assertEqual(len(self.entries()), 3)
        for name in ("daily", "weekly", "every_2_hours"):
            self.entry_for(name)

    def test_each_entry_rotates_only_its_own_jobs(self):
        self.jobs = [job("a"), job("b"), job("c", "weekly")]
        self.scheduler.setup_schedule("daily")
        self.fire("daily")
        self.rotate_some.assert_called_with(["a", "b"])
        self.fire("weekly")
        self.rotate_some.assert_called_with(["c"])
        self.rotate_all.assert_not_called()

    def test_a_job_naming_the_default_explicitly_is_grouped_with_it(self):
        self.jobs = [job("a"), job("b", "daily"), job("c", " Daily ")]
        self.scheduler.setup_schedule("daily")
        self.assertEqual(len(self.entries()), 1)
        self.fire("daily")
        self.rotate_all.assert_called_once_with()

    def test_equivalent_spellings_share_an_entry(self):
        self.jobs = [job("a", "every_1_hour"), job("b", "every_1_hours")]
        self.scheduler.setup_schedule("daily")
        self.assertEqual(len(self.entries()), 2)  # daily (default, no jobs) + hourly
        self.fire("every_1_hours")
        self.rotate_all.assert_called_once_with()  # both jobs are on it

    def test_the_default_entry_stays_when_every_job_has_its_own_schedule(self):
        self.jobs = [job("a", "weekly"), job("b", "every_2_hours")]
        self.scheduler.setup_schedule("daily")
        self.entry_for("daily")
        with patch.object(self.scheduler, "_verify_backup_integrity") as verify:
            self.fire("daily")
        self.rotate_all.assert_not_called()
        self.rotate_some.assert_not_called()
        verify.assert_not_called()

    def test_intervals_of_one_do_not_crash_the_schedule_library(self):
        self.jobs = [job("a", "every_1_minutes")]
        self.scheduler.setup_schedule("every_1_hours")
        self.entry_for("every_1_hours")
        self.entry_for("every_1_minutes")

    def test_maintenance_schedules_are_still_set_up(self):
        self.scheduler.setup_schedule("daily")
        self.assertGreater(len(schedule.get_jobs()), len(self.entries()))

    def test_works_without_a_jobs_provider(self):
        legacy = RotationScheduler(rotation_function=self.rotate_all, backup_manager=MagicMock())
        legacy.setup_schedule("weekly")
        self.assertEqual(len(self.entries()), 1)
        legacy._run_rotation()
        self.rotate_all.assert_called_once_with()


class TestSchedulerValidation(SchedulerFixture):
    def test_invalid_default_raises(self):
        for bad in ("hourly", "", "every_0_minutes", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.scheduler.setup_schedule(bad)

    def test_invalid_default_leaves_existing_schedules_alone(self):
        self.scheduler.setup_schedule("daily")
        before = len(schedule.get_jobs())
        with self.assertRaises(ValueError):
            self.scheduler.setup_schedule("hourly")
        self.assertEqual(len(schedule.get_jobs()), before)

    def test_invalid_job_schedule_raises_naming_every_bad_job(self):
        self.jobs = [job("a"), job("b", "hourly"), job("c", "every_0_hours")]
        with self.assertRaises(ValueError) as caught:
            self.scheduler.setup_schedule("daily")
        message = str(caught.exception)
        self.assertIn("job 'b'", message)
        self.assertIn("job 'c'", message)
        self.assertNotIn("job 'a'", message)

    def test_monthly_is_now_a_valid_default(self):
        self.scheduler.setup_schedule("monthly")
        self.entry_for("monthly")


class TestFiring(SchedulerFixture):
    def test_monthly_only_acts_on_the_first(self):
        self.scheduler.setup_schedule("monthly")
        for day, expected_calls in ((15, 0), (1, 1)):
            self.rotate_all.reset_mock()
            fake_now = datetime(2026, 10, day, 2, 0)
            with patch("secret_rotator.scheduler.datetime") as fake_datetime:
                fake_datetime.now.return_value = fake_now
                self.scheduler._run_rotation("monthly")
            self.assertEqual(self.rotate_all.call_count, expected_calls, day)

    def test_jobs_added_later_are_picked_up_without_a_refresh(self):
        self.scheduler.setup_schedule("daily")
        self.jobs.append(job("late"))
        self.fire("daily")
        self.rotate_all.assert_called_once_with()

    def test_a_job_moved_to_another_schedule_stops_firing_on_the_old_one(self):
        self.jobs = [job("a"), job("b", "weekly")]
        self.scheduler.setup_schedule("daily")
        self.jobs[0]["schedule"] = "weekly"  # edited after setup; not refreshed
        self.fire("daily")
        self.rotate_some.assert_not_called()  # nothing is left on "daily"

    def test_rotation_in_progress_is_skipped_quietly(self):
        self.jobs = [job("a"), job("b", "weekly")]
        self.rotate_some.side_effect = RotationInProgressError("busy")
        self.scheduler.setup_schedule("daily")
        self.fire("daily")  # must not raise

    def test_a_failure_triggers_a_backup_integrity_check(self):
        self.jobs = [job("a"), job("b", "weekly")]
        self.rotate_some.side_effect = lambda names: {n: False for n in names}
        self.scheduler.setup_schedule("daily")
        with patch.object(self.scheduler, "_verify_backup_integrity") as verify:
            self.fire("daily")
        verify.assert_called_once()

    def test_a_clean_run_does_not_trigger_the_check(self):
        self.scheduler.setup_schedule("daily")
        with patch.object(self.scheduler, "_verify_backup_integrity") as verify:
            self.fire("daily")
        verify.assert_not_called()

    def test_a_partial_schedule_without_a_subset_function_is_skipped_with_an_error(self):
        self.scheduler.rotate_jobs_function = None
        self.jobs = [job("a"), job("b", "weekly")]
        self.scheduler.setup_schedule("daily")
        self.fire("daily")  # must not raise
        self.rotate_all.assert_not_called()

    def test_unexpected_errors_do_not_escape(self):
        self.jobs = [job("a"), job("b", "weekly")]
        self.rotate_some.side_effect = RuntimeError("boom")
        self.scheduler.setup_schedule("daily")
        self.fire("daily")


class TestNextRotation(SchedulerFixture):
    def test_next_rotation_for_a_registered_schedule(self):
        self.jobs = [job("a", "every_2_hours")]
        self.scheduler.setup_schedule("daily")
        when = self.scheduler.next_rotation_for("every_2_hours")
        self.assertEqual(when.utcoffset(), timedelta(0))
        delta = when - datetime.now(timezone.utc)
        self.assertGreater(delta, timedelta(hours=1, minutes=58))
        self.assertLess(delta, timedelta(hours=2, minutes=1))

    def test_each_schedule_reports_its_own_time(self):
        self.jobs = [job("a", "every_2_hours")]
        self.scheduler.setup_schedule("weekly")
        hourly = self.scheduler.next_rotation_for("every_2_hours")
        weekly = self.scheduler.next_rotation_for("weekly")
        self.assertLess(hourly, weekly)

    def test_unknown_unregistered_and_invalid_schedules_give_none(self):
        self.scheduler.setup_schedule("daily")
        self.assertIsNone(self.scheduler.next_rotation_for("weekly"))
        self.assertIsNone(self.scheduler.next_rotation_for("hourly"))
        self.assertIsNone(self.scheduler.next_rotation_for(None))

    def test_equivalent_spellings_are_found(self):
        self.jobs = [job("a", "every_1_hour")]
        self.scheduler.setup_schedule("daily")
        self.assertIsNotNone(self.scheduler.next_rotation_for("Every_1_Hours"))

    def test_monthly_reports_the_first_of_the_month_not_tomorrow(self):
        self.scheduler.setup_schedule("monthly")
        when = self.scheduler.next_rotation_for("monthly")
        expected = next_monthly(datetime.now()).astimezone(timezone.utc)
        self.assertEqual(when, expected)

    def test_next_rotation_time_is_the_soonest_of_all_schedules(self):
        self.jobs = [job("a", "every_2_hours")]
        self.scheduler.setup_schedule("weekly")
        self.assertEqual(
            self.scheduler.next_rotation_time(), self.scheduler.next_rotation_for("every_2_hours")
        )

    def test_next_rotation_time_before_setup(self):
        self.assertIsNone(self.scheduler.next_rotation_time())


class TestRefresh(SchedulerFixture):
    def test_a_new_schedule_is_registered_and_maintenance_is_untouched(self):
        self.scheduler.setup_schedule("daily")
        maintenance = len(schedule.get_jobs()) - len(self.entries())
        self.jobs.append(job("c", "weekly"))
        self.scheduler.refresh_rotation_schedules()
        self.entry_for("weekly")
        self.assertEqual(len(schedule.get_jobs()) - len(self.entries()), maintenance)

    def test_a_schedule_nobody_uses_any_more_is_removed(self):
        self.jobs = [job("a"), job("b", "weekly")]
        self.scheduler.setup_schedule("daily")
        self.jobs[1]["schedule"] = "daily"
        self.scheduler.refresh_rotation_schedules()
        self.assertEqual(schedule.get_jobs(f"{ROTATION_TAG}:weekly"), [])
        self.assertIsNone(self.scheduler.next_rotation_for("weekly"))

    def test_refresh_never_registers_the_same_schedule_twice(self):
        self.jobs = [job("a", "weekly")]
        self.scheduler.setup_schedule("daily")
        self.scheduler.refresh_rotation_schedules()
        self.scheduler.refresh_rotation_schedules()
        self.assertEqual(len(self.entries()), 2)

    def test_an_invalid_schedule_changes_nothing(self):
        self.jobs = [job("a", "weekly")]
        self.scheduler.setup_schedule("daily")
        before = [(e.tags, e.next_run) for e in self.entries()]
        self.jobs.append(job("bad", "hourly"))
        with self.assertRaises(ValueError):
            self.scheduler.refresh_rotation_schedules()
        self.assertEqual([(e.tags, e.next_run) for e in self.entries()], before)

    def test_effective_schedule(self):
        self.scheduler.setup_schedule("weekly")
        self.assertEqual(self.scheduler.effective_schedule(job("a")), "weekly")
        self.assertEqual(self.scheduler.effective_schedule(job("a", "Daily")), "daily")
        with self.assertRaises(ValueError):
            self.scheduler.effective_schedule(job("a", "nope"))


class FakeProvider(SecretProvider):
    def __init__(self):
        super().__init__(name="p", config={})
        self.store = {}

    def get_secret(self, secret_id):
        return "old"

    def update_secret(self, secret_id, new_value):
        self.store[secret_id] = new_value
        return True

    def validate_connection(self):
        return True


class FakeRotator(SecretRotator):
    def __init__(self):
        super().__init__(name="r", config={})

    def generate_new_secret(self):
        return "new"

    def validate_secret(self, value):
        return True


class TestEngineRotateJobs(unittest.TestCase):
    def setUp(self):
        DistributedLock.reset_local_locks()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.audit = AuditLog(audit_file=str(Path(tmp.name) / "audit.log"))
        for target, value in (
            ("secret_rotator.rotation_engine.audit_log", self.audit),
            ("secret_rotator.rotation_engine.time.sleep", lambda seconds: None),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.provider = FakeProvider()
        self.engine = RotationEngine()
        self.engine.backup_manager = MagicMock()
        self.engine.register_provider(self.provider)
        self.engine.register_rotator(FakeRotator())
        self.engine.rotation_jobs = [job("a"), job("b"), job("c")]

    def test_rotates_only_the_named_jobs_in_configuration_order(self):
        results = self.engine.rotate_jobs(["c", "a"])
        self.assertEqual(list(results), ["a", "c"])
        self.assertEqual(set(self.provider.store), {"a", "c"})

    def test_unknown_names_are_skipped(self):
        results = self.engine.rotate_jobs(["a", "ghost"])
        self.assertEqual(results, {"a": True})

    def test_nothing_to_rotate_returns_quickly_without_taking_the_lock(self):
        self.engine._rotation_lock.acquire()
        try:
            self.assertEqual(self.engine.rotate_jobs([]), {})
            self.assertEqual(self.engine.rotate_jobs(["ghost"]), {})
        finally:
            self.engine._rotation_lock.release()

    def test_refused_while_a_rotation_holds_the_lock(self):
        self.engine._rotation_lock.acquire()
        try:
            with self.assertRaises(RotationInProgressError):
                self.engine.rotate_jobs(["a"])
        finally:
            self.engine._rotation_lock.release()
        self.assertEqual(self.provider.store, {})

    def test_lock_is_released_afterwards(self):
        self.engine.rotate_jobs(["a"])
        self.assertFalse(self.engine.is_rotation_in_progress())

    def test_progress_callback_counts_within_the_subset(self):
        seen = []
        self.engine.rotate_jobs(["a", "c"], on_job_complete=lambda *args: seen.append(args))
        self.assertEqual(seen, [("a", True, 1, 2), ("c", True, 2, 2)])

    def test_actor_is_recorded(self):
        self.engine.rotate_jobs(["a"], actor="scheduler-test")
        self.assertEqual(
            self.audit.get_recent_events(action="rotate")[0]["actor"], "scheduler-test"
        )

    def test_rotate_all_secrets_is_unchanged(self):
        results = self.engine.rotate_all_secrets()
        self.assertEqual(list(results), ["a", "b", "c"])
        self.assertTrue(all(results.values()))


if __name__ == "__main__":
    unittest.main()
