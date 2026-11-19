import tempfile
import unittest
from pathlib import Path

from test_support import IsolatedDataTestCase

from private_state import (
    PrivateStateError, decrypt_private_state, encrypt_private_state,
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
            (source / "discord_messages.json").write_text('{"private":"body"}', encoding="utf-8")
            encrypted = root / "private_state.enc"
            encrypt_private_state(source, encrypted, "d" * 48)
            self.assertTrue((source / "discord_messages.json").exists())
            self.assertNotIn(b"private", encrypted.read_bytes())

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
