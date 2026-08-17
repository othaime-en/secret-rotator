import os
import time
import unittest
import urllib.request

from werkzeug.security import generate_password_hash

from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.web import FlaskWebServer


class TestFlaskWebServer(unittest.TestCase):
    """
    These start a real server on an OS-assigned loopback port rather
    than using Flask's test client, since the point is to exercise the
    actual socket-handling server class, not just the WSGI app.
    """

    @classmethod
    def setUpClass(cls):
        cls._saved_env = {
            k: os.environ.pop(k, None)
            for k in (
                "SECRET_ROTATOR_ADMIN_USERNAME",
                "SECRET_ROTATOR_ADMIN_PASSWORD_HASH",
                "FLASK_SECRET_KEY",
            )
        }
        os.environ["SECRET_ROTATOR_ADMIN_USERNAME"] = "testadmin"
        os.environ["SECRET_ROTATOR_ADMIN_PASSWORD_HASH"] = generate_password_hash(
            "irrelevant-for-this-test"
        )
        os.environ["FLASK_SECRET_KEY"] = "test-secret-key-not-for-prod"

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def setUp(self):
        engine = RotationEngine()
        # port=0 asks the OS for a free ephemeral port so tests don't
        # collide with anything else running on the host/CI runner.
        self.server = FlaskWebServer(engine, port=0, host="127.0.0.1", threads=2)

    def tearDown(self):
        self.server.stop()

    def test_serves_requests_over_a_real_socket(self):
        """The app should be reachable over an actual TCP connection,
        not just Flask's in-process test client."""
        self.server.start()
        time.sleep(0.2)

        port = self.server.server.effective_port
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/healthz", timeout=3
        ) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn(b'"status":"ok"', resp.read())

    def test_start_is_idempotent(self):
        """Calling start() twice should not raise or open a second
        listener; it should log and no-op."""
        self.server.start()
        time.sleep(0.2)
        first_server = self.server.server
        self.server.start()
        self.assertIs(self.server.server, first_server)

    def test_stop_before_start_does_not_raise(self):
        """stop() should be safe to call even if start() never ran."""
        self.server.stop()

    def test_stop_releases_the_port(self):
        """After stop(), the port should be free again — i.e. the
        server actually released its socket rather than leaking it."""
        self.server.start()
        time.sleep(0.2)
        port = self.server.server.effective_port

        self.server.stop()
        time.sleep(0.2)

        # A fresh server bound to the same port should succeed.
        engine2 = RotationEngine()
        second = FlaskWebServer(engine2, port=port, host="127.0.0.1", threads=2)
        try:
            second.start()
            time.sleep(0.2)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/healthz", timeout=3
            ) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            second.stop()


if __name__ == "__main__":
    unittest.main()