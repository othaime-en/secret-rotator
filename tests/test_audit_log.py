import json
import os
import tempfile
import unittest
from pathlib import Path

from secret_rotator.audit_log import AuditLog
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotators.base import SecretRotator


class FakeProvider(SecretProvider):
    def __init__(self):
        super().__init__(name="fake_provider", config={})
        self.store = {"svc": "old-value"}

    def get_secret(self, secret_id):
        return self.store.get(secret_id)

    def update_secret(self, secret_id, new_value):
        self.store[secret_id] = new_value
        return True

    def validate_connection(self):
        return True


class FakeRotator(SecretRotator):
    def __init__(self):
        super().__init__(name="fake_rotator", config={})

    def generate_new_secret(self):
        return "new-value"

    def validate_secret(self, value):
        return True


class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.audit_file = Path(self.tmpdir) / "audit.log"
        self.audit_log = AuditLog(audit_file=str(self.audit_file))

    def test_log_writes_json_line(self):
        self.audit_log.log("rotate", "admin", secret_id="db", success=True)
        lines = self.audit_file.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        event = json.loads(lines[0])
        self.assertEqual(event["action"], "rotate")
        self.assertEqual(event["actor"], "admin")
        self.assertEqual(event["secret_id"], "db")
        self.assertTrue(event["success"])

    def test_get_recent_events_filters_by_secret_id_and_action(self):
        self.audit_log.log("rotate", "admin", secret_id="db")
        self.audit_log.log("rotate", "admin", secret_id="api_key")
        self.audit_log.log("restore", "admin", secret_id="db")

        db_rotates = self.audit_log.get_recent_events(secret_id="db", action="rotate")
        self.assertEqual(len(db_rotates), 1)
        self.assertEqual(db_rotates[0]["secret_id"], "db")
        self.assertEqual(db_rotates[0]["action"], "rotate")

    def test_get_recent_events_excludes_old_events(self):
        self.audit_log.log("rotate", "admin", secret_id="db")
        recent = self.audit_log.get_recent_events(hours=0)
        # hours=0 -> cutoff is "now", so an event logged microseconds ago
        # should already have fallen outside the window.
        self.assertEqual(recent, [])

    def test_no_secret_values_are_ever_logged(self):
        self.audit_log.log(
            "rotate", "admin", secret_id="db", success=True,
            details={"job": "db_rotation"},
        )
        raw = self.audit_file.read_text()
        self.assertNotIn("old-value", raw)
        self.assertNotIn("new-value", raw)


class TestRotationEngineAudit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.audit_file = Path(self.tmpdir) / "audit.log"

        # Patch the module-level singleton used inside rotation_engine.py
        import secret_rotator.rotation_engine as re_mod
        self._orig_audit_log = re_mod.audit_log
        re_mod.audit_log = AuditLog(audit_file=str(self.audit_file))

        self.engine = RotationEngine()
        self.engine.register_provider(FakeProvider())
        self.engine.register_rotator(FakeRotator())
        self.engine.add_rotation_job({
            "name": "test_job",
            "provider": "fake_provider",
            "rotator": "fake_rotator",
            "secret_id": "svc",
        })

    def tearDown(self):
        import secret_rotator.rotation_engine as re_mod
        re_mod.audit_log = self._orig_audit_log

    def test_successful_rotation_is_audited_with_actor(self):
        result = self.engine.rotate_secret(
            self.engine.rotation_jobs[0], actor="alice"
        )
        self.assertTrue(result)

        lines = self.audit_file.read_text().strip().splitlines()
        events = [json.loads(l) for l in lines]
        rotate_events = [e for e in events if e["action"] == "rotate"]
        self.assertEqual(len(rotate_events), 1)
        self.assertEqual(rotate_events[0]["actor"], "alice")
        self.assertEqual(rotate_events[0]["secret_id"], "svc")
        self.assertTrue(rotate_events[0]["success"])

    def test_default_actor_is_system(self):
        self.engine.rotate_secret(self.engine.rotation_jobs[0])
        events = [json.loads(l) for l in self.audit_file.read_text().strip().splitlines()]
        self.assertEqual(events[0]["actor"], "system")

    def test_missing_provider_is_audited_as_failure(self):
        bad_job = {
            "name": "bad_job",
            "provider": "does_not_exist",
            "rotator": "fake_rotator",
            "secret_id": "svc",
        }
        result = self.engine.rotate_secret(bad_job, actor="alice")
        self.assertFalse(result)

        events = [json.loads(l) for l in self.audit_file.read_text().strip().splitlines()]
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["success"])


if __name__ == "__main__":
    unittest.main()