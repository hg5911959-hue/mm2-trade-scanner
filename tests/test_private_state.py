import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from test_support import IsolatedDataTestCase

from private_state import (
    MAX_ENCRYPTED_BYTES, MAX_FILE_BYTES,
    PrivateStateError, _read_bounded_regular_file,
    decrypt_private_state, encrypt_private_state,
)


class PrivateStateEncryptionTests(IsolatedDataTestCase):
    def test_private_files_are_authenticated_encrypted_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            restored = root / "restored"
            source.mkdir()
            restored.mkdir()
            private_files = {
                "cash_market_offers.json": b'{"offers":[{"item":"Secret Candy"}]}',
                "inventory.json": b'{"items":{"secret candy":{"quantity":3}}}',
                "telegram_update_state.json": b'{"offset":98765}',
                "telegram_last_run.json": b'{"started_at_epoch":1800000000}',
                "market_book_history.json": b'{"snapshots":[]}',
                "market_alert_state.json": b'{"opportunities":{}}',
                "discord_alert_state.json": b'{"version":1,"alerted":["private-id"],"uncertain":{}}',
                "discord_messages.json": b'{"messages":[{"content":"private discord message","author_id":"123456789012345"}]}',
            }
            for name, payload in private_files.items():
                (source / name).write_bytes(payload)
            encrypted = root / "private_state.enc"
            count = encrypt_private_state(source, encrypted, "a" * 48)
            self.assertEqual(count, len(private_files))
            self.assertTrue(encrypted.exists())
            self.assertFalse(any((source / name).exists() for name in private_files))
            ciphertext = encrypted.read_bytes()
            self.assertNotIn(b"Secret Candy", ciphertext)
            self.assertNotIn(b"98765", ciphertext)
            self.assertNotIn(b"private discord message", ciphertext)
            self.assertNotIn(b"123456789012345", ciphertext)
            restored_count = decrypt_private_state(encrypted, restored, "a" * 48)
            self.assertEqual(restored_count, len(private_files))
            for name, payload in private_files.items():
                self.assertEqual((restored / name).read_bytes(), payload)

    def test_wrong_key_and_tampering_fail_before_writing_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            restored = root / "restored"
            source.mkdir()
            restored.mkdir()
            (source / "inventory.json").write_text('{"private":true}', encoding="utf-8")
            encrypted = root / "private_state.enc"
            encrypt_private_state(source, encrypted, "b" * 48)
            with self.assertRaises(PrivateStateError):
                decrypt_private_state(encrypted, restored, "c" * 48)
            tampered = bytearray(encrypted.read_bytes())
            tampered[-40] ^= 0x01
            encrypted.write_bytes(tampered)
            with self.assertRaises(PrivateStateError):
                decrypt_private_state(encrypted, restored, "b" * 48)
            self.assertFalse((restored / "inventory.json").exists())

    def test_unexpected_files_are_never_encrypted_or_decrypted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "unrelated.json").write_text('{"private":"body"}', encoding="utf-8")
            encrypted = root / "private_state.enc"
            encrypt_private_state(source, encrypted, "d" * 48)
            self.assertTrue((source / "unrelated.json").exists())
            self.assertNotIn(b"private", encrypted.read_bytes())

    def test_oversized_private_file_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            oversized = source / "inventory.json"
            with oversized.open("wb") as handle:
                handle.truncate(MAX_FILE_BYTES + 1)

            encrypted = root / "private_state.enc"
            with self.assertRaises(PrivateStateError):
                encrypt_private_state(source, encrypted, "e" * 48)

            self.assertFalse(encrypted.exists())
            self.assertTrue(oversized.exists())

    def test_oversized_encrypted_bundle_is_rejected_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            encrypted = root / "private_state.enc"
            with encrypted.open("wb") as handle:
                handle.truncate(MAX_ENCRYPTED_BYTES + 1)

            with self.assertRaises(PrivateStateError):
                decrypt_private_state(encrypted, root / "restored", "f" * 48)

            self.assertFalse((root / "restored" / "inventory.json").exists())

    def test_bounded_reader_catches_growth_after_fstat(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "growing.json"
            path.write_bytes(b"0123456789")
            real_fstat = os.fstat

            def report_small_size(fd):
                actual = real_fstat(fd)
                return os.stat_result((
                    actual.st_mode, actual.st_ino, actual.st_dev, actual.st_nlink,
                    actual.st_uid, actual.st_gid, 0, actual.st_atime,
                    actual.st_mtime, actual.st_ctime,
                ))

            with patch("private_state.os.fstat", side_effect=report_small_size):
                with self.assertRaisesRegex(PrivateStateError, "size limit"):
                    _read_bounded_regular_file(path, 4)

    def test_bounded_reader_rejects_fifo_without_blocking(self):
        if not hasattr(os, "mkfifo") or not hasattr(os, "O_NONBLOCK"):
            self.skipTest("FIFO/nonblocking open is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-state.pipe"
            os.mkfifo(path)
            with self.assertRaisesRegex(PrivateStateError, "not a regular file"):
                _read_bounded_regular_file(path, 1024)

    def test_encrypt_rejects_symlinked_private_input(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            target = root / "outside.json"
            target.write_text('{"private":true}', encoding="utf-8")
            (source / "inventory.json").symlink_to(target)

            with self.assertRaises(PrivateStateError):
                encrypt_private_state(source, root / "private_state.enc", "g" * 48)

            self.assertEqual(target.read_text(encoding="utf-8"), '{"private":true}')
            self.assertFalse((root / "private_state.enc").exists())

    def test_decrypt_rejects_symlinked_encrypted_input(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW is unavailable on this platform")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "inventory.json").write_text('{"private":true}', encoding="utf-8")
            encrypted = root / "private_state.enc"
            encrypt_private_state(source, encrypted, "h" * 48)
            linked = root / "linked.enc"
            linked.symlink_to(encrypted)
            restored = root / "restored"

            with self.assertRaises(PrivateStateError):
                decrypt_private_state(linked, restored, "h" * 48)

            self.assertFalse((restored / "inventory.json").exists())

    def test_short_or_missing_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "inventory.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(PrivateStateError):
                encrypt_private_state(source, root / "private_state.enc", "short")


if __name__ == "__main__":
    unittest.main()
