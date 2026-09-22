"""
Unit tests for rotators/advanced_rotators.py.
"""

import sys
import json
import unittest
from types import ModuleType
from unittest import mock

from secret_rotator.rotators.advanced_rotators import (
    DatabasePasswordRotator,
    APIKeyRotator,
    JWTSecretRotator,
    SSHKeyRotator,
    CertificateRotator,
    OAuth2TokenRotator,
)


def _fake_module(name: str) -> ModuleType:
    """Register and return a blank fake module under sys.modules,
    cleaned up automatically by the caller's addCleanup."""
    mod = ModuleType(name)
    sys.modules[name] = mod
    return mod


class TestDatabasePasswordRotator(unittest.TestCase):
    def _rotator(self, **config):
        config.setdefault("test_connection", False)
        return DatabasePasswordRotator("db", config)

    def test_generated_password_meets_its_own_validator(self):
        rotator = self._rotator()
        password = rotator.generate_new_secret()
        self.assertTrue(rotator.validate_secret(password))

    def test_generated_password_respects_configured_length(self):
        rotator = self._rotator(length=40)
        self.assertEqual(len(rotator.generate_new_secret()), 40)

    def test_generated_password_starts_with_a_letter(self):
        # Run several times since generation is random.
        rotator = self._rotator()
        for _ in range(20):
            self.assertTrue(rotator.generate_new_secret()[0].isalpha())

    def test_validate_rejects_short_password(self):
        rotator = self._rotator()
        self.assertFalse(rotator.validate_secret("Ab1!"))

    def test_validate_rejects_password_missing_complexity(self):
        rotator = self._rotator()
        self.assertFalse(rotator.validate_secret("alllowercase12345"))
        self.assertFalse(rotator.validate_secret("ALLUPPERCASE12345"))
        self.assertFalse(rotator.validate_secret("NoDigitsHereAtAll"))

    def test_validate_with_test_connection_but_driver_missing_does_not_fail_validation(self):
        """psycopg2 isn't in the base install — a missing optional
        driver must not silently break rotation for people who aren't
        using that database type."""
        sys.modules.pop("psycopg2", None)
        rotator = self._rotator(db_type="postgresql", test_connection=True)
        self.assertTrue(rotator.validate_secret("StrongPassw0rd!!"))

    def test_validate_with_mocked_successful_postgres_connection(self):
        fake = _fake_module("psycopg2")
        fake.connect = mock.Mock(return_value=mock.Mock())
        self.addCleanup(sys.modules.pop, "psycopg2", None)

        rotator = self._rotator(db_type="postgresql", test_connection=True)
        self.assertTrue(rotator.validate_secret("StrongPassw0rd!!"))
        fake.connect.assert_called_once()

    def test_validate_with_mocked_failing_postgres_connection(self):
        fake = _fake_module("psycopg2")
        fake.connect = mock.Mock(side_effect=RuntimeError("auth failed"))
        self.addCleanup(sys.modules.pop, "psycopg2", None)

        rotator = self._rotator(db_type="postgresql", test_connection=True)
        self.assertFalse(rotator.validate_secret("StrongPassw0rd!!"))

    def test_unknown_db_type_skips_connection_test(self):
        rotator = self._rotator(db_type="some_future_db", test_connection=True)
        self.assertTrue(rotator.validate_secret("StrongPassw0rd!!"))


class TestAPIKeyRotator(unittest.TestCase):
    def test_hex_format_generates_expected_length(self):
        rotator = APIKeyRotator("key", {"length": 32, "format": "hex"})
        key = rotator.generate_new_secret()
        self.assertEqual(len(key), 32)
        int(key, 16)  # raises if not valid hex

    def test_base64_format_generates_expected_length(self):
        rotator = APIKeyRotator("key", {"length": 24, "format": "base64"})
        self.assertEqual(len(rotator.generate_new_secret()), 24)

    def test_alphanumeric_format_generates_expected_length(self):
        rotator = APIKeyRotator("key", {"length": 20, "format": "alphanumeric"})
        key = rotator.generate_new_secret()
        self.assertEqual(len(key), 20)
        self.assertTrue(key.isalnum())

    def test_prefix_is_prepended(self):
        rotator = APIKeyRotator("key", {"prefix": "sk_live_", "length": 16})
        self.assertTrue(rotator.generate_new_secret().startswith("sk_live_"))

    def test_validate_rejects_wrong_prefix(self):
        rotator = APIKeyRotator("key", {"prefix": "sk_live_"})
        self.assertFalse(rotator.validate_secret("sk_test_abc123"))

    def test_validate_rejects_too_short_key(self):
        rotator = APIKeyRotator("key", {"length": 32})
        self.assertFalse(rotator.validate_secret("short"))

    def test_checksum_roundtrip_validates(self):
        rotator = APIKeyRotator("key", {"include_checksum": True, "length": 16})
        key = rotator.generate_new_secret()
        self.assertTrue(rotator.validate_secret(key))

    def test_tampered_checksum_fails_validation(self):
        rotator = APIKeyRotator("key", {"include_checksum": True, "length": 16})
        key = rotator.generate_new_secret()
        key_part, _, checksum = key.rpartition("_")
        tampered = f"{key_part}_{'0' * len(checksum)}"
        self.assertFalse(rotator.validate_secret(tampered))

    def test_checksum_required_but_missing_underscore_fails(self):
        rotator = APIKeyRotator("key", {"include_checksum": True})
        self.assertFalse(rotator.validate_secret("nounderscorehere"))


class TestJWTSecretRotator(unittest.TestCase):
    def test_min_length_by_algorithm(self):
        self.assertEqual(JWTSecretRotator("j", {"algorithm": "HS256"}).min_length, 32)
        self.assertEqual(JWTSecretRotator("j", {"algorithm": "HS384"}).min_length, 48)
        self.assertEqual(JWTSecretRotator("j", {"algorithm": "HS512"}).min_length, 64)

    def test_unknown_algorithm_falls_back_to_256_bit_minimum(self):
        self.assertEqual(JWTSecretRotator("j", {"algorithm": "HS999"}).min_length, 32)

    def test_generated_secret_meets_its_own_validator_without_pyjwt(self):
        sys.modules.pop("jwt", None)  # PyJWT isn't in the base install
        rotator = JWTSecretRotator("j", {"algorithm": "HS256"})
        secret = rotator.generate_new_secret()
        self.assertTrue(rotator.validate_secret(secret))

    def test_validate_rejects_too_short_secret(self):
        rotator = JWTSecretRotator("j", {"algorithm": "HS256"})
        self.assertFalse(rotator.validate_secret("short"))

    def test_validate_with_mocked_pyjwt_round_trip_success(self):
        fake = _fake_module("jwt")
        fake.encode = mock.Mock(return_value="a.b.c")
        fake.decode = mock.Mock(return_value={"test": "data"})
        self.addCleanup(sys.modules.pop, "jwt", None)

        rotator = JWTSecretRotator("j", {"algorithm": "HS256"})
        secret = "x" * 32
        self.assertTrue(rotator.validate_secret(secret))
        fake.encode.assert_called_once()
        fake.decode.assert_called_once()

    def test_validate_with_mocked_pyjwt_failure(self):
        fake = _fake_module("jwt")
        fake.encode = mock.Mock(side_effect=RuntimeError("bad key"))
        self.addCleanup(sys.modules.pop, "jwt", None)

        rotator = JWTSecretRotator("j", {"algorithm": "HS256"})
        self.assertFalse(rotator.validate_secret("x" * 32))


class TestSSHKeyRotator(unittest.TestCase):
    def test_ed25519_generates_valid_key_pair_json(self):
        rotator = SSHKeyRotator("ssh", {"key_type": "ed25519"})
        secret = rotator.generate_new_secret()
        self.assertTrue(rotator.validate_secret(secret))

        parsed = json.loads(secret)
        self.assertIn("BEGIN OPENSSH PRIVATE KEY", parsed["private_key"])
        self.assertTrue(parsed["public_key"].startswith("ssh-ed25519"))

    def test_comment_is_appended_to_public_key(self):
        rotator = SSHKeyRotator("ssh", {"key_type": "ed25519", "comment": "deploy@ci"})
        parsed = json.loads(rotator.generate_new_secret())
        self.assertTrue(parsed["public_key"].endswith("deploy@ci"))

    def test_unsupported_key_type_raises(self):
        rotator = SSHKeyRotator("ssh", {"key_type": "dsa"})
        with self.assertRaises(ValueError):
            rotator.generate_new_secret()

    def test_validate_rejects_non_json(self):
        rotator = SSHKeyRotator("ssh", {})
        self.assertFalse(rotator.validate_secret("not json"))

    def test_validate_rejects_json_missing_keys(self):
        rotator = SSHKeyRotator("ssh", {})
        self.assertFalse(rotator.validate_secret(json.dumps({"private_key": "only"})))


class TestCertificateRotator(unittest.TestCase):
    def test_generates_valid_self_signed_certificate(self):
        rotator = CertificateRotator(
            "cert", {"common_name": "test.local", "key_size": 2048, "validity_days": 30}
        )
        secret = rotator.generate_new_secret()
        self.assertTrue(rotator.validate_secret(secret))

        parsed = json.loads(secret)
        self.assertIn("BEGIN CERTIFICATE", parsed["certificate"])
        self.assertIn("BEGIN RSA PRIVATE KEY", parsed["private_key"])

    def test_validate_rejects_non_json(self):
        rotator = CertificateRotator("cert", {})
        self.assertFalse(rotator.validate_secret("not json"))

    def test_validate_rejects_json_missing_fields(self):
        rotator = CertificateRotator("cert", {})
        self.assertFalse(rotator.validate_secret(json.dumps({"certificate": "only"})))


class TestOAuth2TokenRotator(unittest.TestCase):
    def test_generated_secret_meets_its_own_validator(self):
        rotator = OAuth2TokenRotator("oauth", {"provider": "github", "length": 48})
        secret = rotator.generate_new_secret()
        self.assertTrue(rotator.validate_secret(secret))

    def test_validate_rejects_too_short_secret(self):
        rotator = OAuth2TokenRotator("oauth", {})
        self.assertFalse(rotator.validate_secret("tooshort"))


if __name__ == "__main__":
    unittest.main()