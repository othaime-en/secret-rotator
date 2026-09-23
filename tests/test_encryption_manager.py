"""
Dedicated unit tests for encryption_manager.py.

End-to-end rotation + rollback-on-failure behavior (the two-phase-commit
flow in rotate_master_key()) is covered separately in test_key_rotation.py
and is intentionally not duplicated here.
"""

import unittest
import tempfile
import json
import os
import shutil
import stat
import base64
from pathlib import Path
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from secret_rotator.encryption_manager import EncryptionManager, SecretMasker


class EncryptionManagerTestCase(unittest.TestCase):
    """Common setup: an EncryptionManager backed by a temp key file."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.key_file = Path(self.test_dir) / ".master.key"
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

    def _new_manager(self):
        return EncryptionManager(key_file=str(self.key_file))


class TestEncryptDecryptRoundTrip(EncryptionManagerTestCase):
    def test_roundtrip_preserves_plaintext(self):
        em = self._new_manager()
        for plaintext in ["simple", "with spaces and punctuation!", "🔐 unicode", "a" * 500]:
            ciphertext = em.encrypt(plaintext)
            self.assertEqual(em.decrypt(ciphertext), plaintext)

    def test_ciphertext_is_not_plaintext(self):
        em = self._new_manager()
        ciphertext = em.encrypt("super-secret-value")
        self.assertNotIn("super-secret-value", ciphertext)

    def test_two_encryptions_of_same_plaintext_differ(self):
        """Fernet includes a random IV/nonce, so ciphertexts should differ
        even for identical plaintext (defense against pattern analysis)."""
        em = self._new_manager()
        c1 = em.encrypt("same-value")
        c2 = em.encrypt("same-value")
        self.assertNotEqual(c1, c2)
        self.assertEqual(em.decrypt(c1), em.decrypt(c2))

    def test_encrypt_empty_string_returns_empty(self):
        em = self._new_manager()
        self.assertEqual(em.encrypt(""), "")

    def test_decrypt_empty_string_returns_empty(self):
        em = self._new_manager()
        self.assertEqual(em.decrypt(""), "")

    def test_encrypt_with_associated_data_roundtrips_and_exposes_metadata(self):
        em = self._new_manager()
        package = em.encrypt("value", associated_data={"secret_id": "db_password"})

        # Package is JSON with ciphertext + metadata, not a bare Fernet token.
        parsed = json.loads(package)
        self.assertIn("ciphertext", parsed)
        self.assertEqual(parsed["metadata"], {"secret_id": "db_password"})

        self.assertEqual(em.decrypt(package), "value")
        self.assertEqual(em.get_metadata(package), {"secret_id": "db_password"})

    def test_get_metadata_returns_none_for_plain_ciphertext(self):
        em = self._new_manager()
        ciphertext = em.encrypt("value")  # no associated_data -> plain base64
        self.assertIsNone(em.get_metadata(ciphertext))


class TestTamperAndCorruptionDetection(EncryptionManagerTestCase):
    """S-tier requirement from the audit: decryption must fail loudly on
    corrupted ciphertext or a wrong key, never silently return garbage."""

    def test_corrupted_ciphertext_raises(self):
        em = self._new_manager()
        ciphertext = em.encrypt("secret-value")

        # Flip a character in the underlying base64 payload.
        corrupted = ciphertext[:-4] + ("A" if ciphertext[-4] != "A" else "B") + ciphertext[-3:]

        with self.assertRaises(Exception):
            em.decrypt(corrupted)

    def test_truncated_ciphertext_raises(self):
        em = self._new_manager()
        ciphertext = em.encrypt("secret-value")
        with self.assertRaises(Exception):
            em.decrypt(ciphertext[: len(ciphertext) // 2])

    def test_decrypting_with_a_different_key_raises_invalid_token(self):
        em = self._new_manager()
        ciphertext = em.encrypt("secret-value")

        other_key_file = Path(self.test_dir) / ".other.key"
        other_em = EncryptionManager(key_file=str(other_key_file))

        with self.assertRaises(InvalidToken):
            other_em.decrypt(ciphertext)

    def test_tampered_json_package_ciphertext_field_raises(self):
        em = self._new_manager()
        package = json.loads(em.encrypt("secret-value", associated_data={"x": 1}))
        # Corrupt just the ciphertext field, leave metadata intact.
        raw = base64.b64decode(package["ciphertext"])
        package["ciphertext"] = base64.b64encode(raw[:-1] + bytes([raw[-1] ^ 0xFF])).decode()

        with self.assertRaises(Exception):
            em.decrypt(json.dumps(package))

    def test_master_key_integrity_check_failure_raises(self):
        """If the key file's stored key_id no longer matches a hash of the
        key bytes, loading must refuse rather than silently trust it."""
        self._new_manager()  # writes a valid key file first
        with open(self.key_file, "r") as f:
            key_data = json.load(f)

        key_data["metadata"]["key_id"] = "0" * 16  # deliberately wrong
        with open(self.key_file, "w") as f:
            json.dump(key_data, f)

        with self.assertRaises(ValueError):
            EncryptionManager(key_file=str(self.key_file))


class TestKeyFileHandling(EncryptionManagerTestCase):
    def test_new_key_file_has_owner_only_permissions(self):
        self._new_manager()
        mode = stat.S_IMODE(os.stat(self.key_file).st_mode)
        self.assertEqual(mode, 0o600)

    def test_new_key_file_contains_expected_metadata_fields(self):
        self._new_manager()
        with open(self.key_file) as f:
            data = json.load(f)

        self.assertIn("key", data)
        meta = data["metadata"]
        for field in ("version", "created_at", "algorithm", "key_id"):
            self.assertIn(field, meta)
        self.assertEqual(meta["algorithm"], "Fernet")

    def test_reloading_existing_key_file_preserves_key(self):
        em1 = self._new_manager()
        ciphertext = em1.encrypt("value-before-reload")

        em2 = EncryptionManager(key_file=str(self.key_file))
        self.assertEqual(em2.decrypt(ciphertext), "value-before-reload")

    def test_legacy_key_file_without_metadata_loads_with_warning_metadata(self):
        """Older key files were raw Fernet key bytes with no JSON wrapper.
        Loading must still work and should mark the key as legacy."""
        raw_key = Fernet.generate_key()
        with open(self.key_file, "wb") as f:
            f.write(raw_key)

        em = EncryptionManager(key_file=str(self.key_file))
        self.assertTrue(em.key_metadata.get("legacy"))
        self.assertEqual(em.key_metadata.get("version"), 0)

        # And it's actually usable for encrypt/decrypt.
        ciphertext = em.encrypt("value")
        self.assertEqual(em.decrypt(ciphertext), "value")


class TestKeyAgeAndRotationRecommendation(EncryptionManagerTestCase):
    """should_rotate_key() / get_key_info() only ever read in-memory
    metadata, so tests mutate em.key_metadata directly rather than
    round-tripping through the key file."""

    def test_should_rotate_true_when_no_created_at(self):
        em = self._new_manager()
        em.key_metadata.pop("created_at", None)
        self.assertTrue(em.should_rotate_key())

    def test_should_rotate_false_when_recently_created(self):
        em = self._new_manager()
        em.key_metadata["created_at"] = datetime.now().isoformat()
        self.assertFalse(em.should_rotate_key(max_age_days=90))

    def test_should_rotate_true_when_older_than_max_age(self):
        em = self._new_manager()
        em.key_metadata["created_at"] = (datetime.now() - timedelta(days=200)).isoformat()
        self.assertTrue(em.should_rotate_key(max_age_days=90))

    def test_get_key_info_computes_age_days(self):
        em = self._new_manager()
        em.key_metadata["created_at"] = (datetime.now() - timedelta(days=10)).isoformat()
        info = em.get_key_info()
        self.assertEqual(info["age_days"], 10)

    def test_get_key_info_handles_missing_created_at(self):
        em = self._new_manager()
        em.key_metadata.pop("created_at", None)
        info = em.get_key_info()
        self.assertNotIn("age_days", info)


class TestPassphraseDerivedKeys(unittest.TestCase):
    def test_derive_key_is_deterministic_given_same_salt(self):
        salt = os.urandom(32)
        d1 = EncryptionManager.derive_key_from_passphrase("correct horse battery", salt=salt)
        d2 = EncryptionManager.derive_key_from_passphrase("correct horse battery", salt=salt)
        self.assertEqual(d1["key"], d2["key"])

    def test_derive_key_differs_with_random_salt(self):
        d1 = EncryptionManager.derive_key_from_passphrase("same passphrase")
        d2 = EncryptionManager.derive_key_from_passphrase("same passphrase")
        self.assertNotEqual(d1["key"], d2["key"])
        self.assertNotEqual(d1["salt"], d2["salt"])

    def test_derive_key_default_iterations_meet_owasp_2023_minimum(self):
        derived = EncryptionManager.derive_key_from_passphrase("passphrase")
        self.assertGreaterEqual(derived["iterations"], 600_000)

    def test_create_from_passphrase_encrypts_and_decrypts(self):
        salt = os.urandom(32)
        em = EncryptionManager.create_from_passphrase("a strong passphrase", salt=salt)
        ciphertext = em.encrypt("value")
        self.assertEqual(em.decrypt(ciphertext), "value")

    def test_create_from_passphrase_wrong_passphrase_cannot_decrypt(self):
        salt = os.urandom(32)
        em1 = EncryptionManager.create_from_passphrase("right passphrase", salt=salt)
        ciphertext = em1.encrypt("value")

        em2 = EncryptionManager.create_from_passphrase("wrong passphrase", salt=salt)
        with self.assertRaises(InvalidToken):
            em2.decrypt(ciphertext)


class TestSecretMasker(unittest.TestCase):
    def test_mask_secret_shows_only_prefix(self):
        secret = "my_secret_password"
        masked = SecretMasker.mask_secret(secret)
        self.assertEqual(masked, "my_s" + "*" * (len(secret) - 4))
        self.assertTrue(masked.startswith("my_s"))

    def test_mask_secret_shorter_than_visible_chars_fully_masked(self):
        self.assertEqual(SecretMasker.mask_secret("abc"), "***")

    def test_mask_secret_empty_string(self):
        self.assertEqual(SecretMasker.mask_secret(""), "")

    def test_mask_secret_never_leaks_full_value_for_long_secrets(self):
        secret = "s" * 64
        masked = SecretMasker.mask_secret(secret)
        self.assertNotEqual(masked, secret)
        self.assertTrue(masked.startswith(secret[:4]))

    def test_mask_for_backup_display_short_secret(self):
        self.assertEqual(SecretMasker.mask_for_backup_display("short"), "****")

    def test_mask_for_backup_display_long_secret_shows_edges_only(self):
        result = SecretMasker.mask_for_backup_display("abcdefghij")
        self.assertEqual(result, "ab...ij")
        self.assertNotIn("cdefgh", result)

    def test_hash_secret_for_comparison_is_deterministic(self):
        h1 = SecretMasker.hash_secret_for_comparison("value")
        h2 = SecretMasker.hash_secret_for_comparison("value")
        self.assertEqual(h1, h2)

    def test_hash_secret_for_comparison_differs_for_different_values(self):
        h1 = SecretMasker.hash_secret_for_comparison("value-a")
        h2 = SecretMasker.hash_secret_for_comparison("value-b")
        self.assertNotEqual(h1, h2)

    def test_hash_secret_for_comparison_never_contains_the_secret(self):
        h = SecretMasker.hash_secret_for_comparison("super-secret-password")
        self.assertNotIn("super-secret-password", h)


if __name__ == "__main__":
    unittest.main()
