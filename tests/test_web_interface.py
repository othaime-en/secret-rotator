import unittest
import tempfile
import json
import sys
from pathlib import Path

from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.providers.file_provider import FileSecretProvider


class TestWebInterface(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures"""
        from secret_rotator.web_interface import WebServer
        from secret_rotator.rotation_engine import RotationEngine

        self.engine = RotationEngine()

        # Create temporary file for testing
        self.temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        self.temp_file.write('{"test_secret": "test_value"}')
        self.temp_file.close()

        # Register test provider and rotator
        provider = FileSecretProvider("test_provider", {"file_path": self.temp_file.name})
        self.engine.register_provider(provider)

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
        self.engine.register_rotator(rotator)

        # Add test job
        self.engine.add_rotation_job(
            {
                "name": "test_job",
                "provider": "test_provider",
                "rotator": "test_rotator",
                "secret_id": "test_secret",
            }
        )

        self.web_server = WebServer(self.engine, port=8081)

    def tearDown(self):
        """Clean up"""
        import os

        if self.web_server.server:
            self.web_server.stop()
        os.unlink(self.temp_file.name)

    def test_web_server_start_stop(self):
        """Test starting and stopping web server"""
        self.web_server.start()
        self.assertIsNotNone(self.web_server.server)
        self.assertTrue(self.web_server.thread.is_alive())

        self.web_server.stop()

    def test_api_status_endpoint(self):
        """Test /api/status endpoint"""
        import urllib.request

        self.web_server.start()

        try:
            response = urllib.request.urlopen("http://localhost:8081/api/status")
            data = json.loads(response.read().decode())

            self.assertEqual(data["status"], "running")
            self.assertEqual(data["providers"], 1)
            self.assertEqual(data["rotators"], 1)
            self.assertEqual(data["jobs"], 1)
        finally:
            self.web_server.stop()

    def test_api_jobs_endpoint(self):
        """Test /api/jobs endpoint"""
        import urllib.request

        self.web_server.start()

        try:
            response = urllib.request.urlopen("http://localhost:8081/api/jobs")
            data = json.loads(response.read().decode())

            self.assertIn("jobs", data)
            self.assertEqual(len(data["jobs"]), 1)
            self.assertEqual(data["jobs"][0]["name"], "test_job")
        finally:
            self.web_server.stop()
