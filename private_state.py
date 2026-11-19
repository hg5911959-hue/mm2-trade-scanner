"""Authenticated encryption for private state stored in this public repository.

Python code uses only the standard library. The GitHub Actions Ubuntu runner
provides the OpenSSL command-line binary used for AES-256-CTR encryption.
The existing Telegram bot token is the key-encryption input; no new secret is
required. Ciphertext is safe to retain on the public state branch.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

MAGIC = b"MM2PSTATE1"
SALT_BYTES = 16
IV_BYTES = 16
TAG_BYTES = 32
PBKDF2_ITERATIONS = 250_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
PRIVATE_FILENAMES = frozenset({
    "cash_market_offers.json",
    "inventory.json",
    "telegram_update_state.json",
    "telegram_last_run.json",
    "market_book_history.json",
    "market_alert_state.json",
})


class PrivateStateError(RuntimeError):
    """Raised when private state cannot be safely encrypted or decrypted."""


def _derive_keys(secret: str, salt: bytes) -> tuple[bytes, bytes]:
    if not isinstance(secret, str) or len(secret) < 32:
        raise PrivateStateError("private-state encryption key is missing or too short")
    material = hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=64
    )
    return material[:32], material[32:]


def _openssl_crypt(data: bytes, *, key: bytes, iv: bytes, decrypt: bool) -> bytes:
    executable = shutil.which("openssl")
    if not executable:
        raise PrivateStateError("OpenSSL executable is unavailable on this runner")
    command = [
        executable, "enc", "-aes-256-ctr", "-K", key.hex(), "-iv", iv.hex(),
    ]
    if decrypt:
        command.insert(2, "-d")
    try:
        result = subprocess.run(
            command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrivateStateError("OpenSSL private-state operation failed") from exc
    return result.stdout


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encrypt_private_state(directory: str | Path, output: str | Path, secret: str) -> int:
    """Encrypt the allowlisted private files, then remove plaintext only on success."""
    root, target = Path(directory), Path(output)
    files: dict[str, str] = {}
    total = 0
    for name in sorted(PRIVATE_FILENAMES):
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise PrivateStateError("private-state path is not a regular file")
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise PrivateStateError("private-state file exceeds the configured size limit")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise PrivateStateError("private-state bundle exceeds the configured size limit")
        files[name] = base64.b64encode(raw).decode("ascii")

    plaintext = json.dumps(
        {"schema_version": 1, "files": files},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    salt, iv = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(IV_BYTES)
    encryption_key, authentication_key = _derive_keys(secret, salt)
    ciphertext = _openssl_crypt(plaintext, key=encryption_key, iv=iv, decrypt=False)
    authenticated = MAGIC + salt + iv + ciphertext
    tag = hmac.new(authentication_key, authenticated, hashlib.sha256).digest()
    _atomic_write(target, authenticated + tag)

    for name in files:
        (root / name).unlink()
    return len(files)


def decrypt_private_state(encrypted_path: str | Path, directory: str | Path, secret: str) -> int:
    """Verify the HMAC before decrypting; validate all names before writing anything."""
    source, root = Path(encrypted_path), Path(directory)
    try:
        blob = source.read_bytes()
    except OSError as exc:
        raise PrivateStateError("encrypted private-state bundle is unavailable") from exc
    minimum = len(MAGIC) + SALT_BYTES + IV_BYTES + TAG_BYTES
    if len(blob) < minimum or not blob.startswith(MAGIC):
        raise PrivateStateError("encrypted private-state bundle has an invalid header")
    content, supplied_tag = blob[:-TAG_BYTES], blob[-TAG_BYTES:]
    cursor = len(MAGIC)
    salt = content[cursor:cursor + SALT_BYTES]
    cursor += SALT_BYTES
    iv = content[cursor:cursor + IV_BYTES]
    cursor += IV_BYTES
    ciphertext = content[cursor:]
    encryption_key, authentication_key = _derive_keys(secret, salt)
    expected_tag = hmac.new(authentication_key, content, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise PrivateStateError("encrypted private-state authentication failed")
    plaintext = _openssl_crypt(ciphertext, key=encryption_key, iv=iv, decrypt=True)
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateStateError("decrypted private-state payload is invalid") from exc
    if (
        not isinstance(payload, dict) or payload.get("schema_version") != 1
        or not isinstance(payload.get("files"), dict)
    ):
        raise PrivateStateError("decrypted private-state schema is invalid")

    decoded: dict[str, bytes] = {}
    total = 0
    for name, encoded in payload["files"].items():
        if name not in PRIVATE_FILENAMES or not isinstance(encoded, str):
            raise PrivateStateError("decrypted private-state contains an unexpected file")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise PrivateStateError("decrypted private-state file encoding is invalid") from exc
        if len(raw) > MAX_FILE_BYTES:
            raise PrivateStateError("decrypted private-state file exceeds the configured size limit")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise PrivateStateError("decrypted private-state bundle exceeds the configured size limit")
        decoded[name] = raw

    # Validate every payload entry before writing the first file.
    for name, raw in decoded.items():
        _atomic_write(root / name, raw)
    return len(decoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    encrypt = subparsers.add_parser("encrypt")
    encrypt.add_argument("--directory", default="data")
    encrypt.add_argument("--output", default="data/private_state.enc")
    decrypt = subparsers.add_parser("decrypt")
    decrypt.add_argument("--input", default="data/private_state.enc")
    decrypt.add_argument("--directory", default="data")
    args = parser.parse_args()
    secret = os.environ.get("MM2_PRIVATE_STATE_KEY", "")
    if args.operation == "encrypt":
        count = encrypt_private_state(args.directory, args.output, secret)
        print(f"[private-state] Encrypted {count} allowlisted file(s); plaintext removed.")
    else:
        count = decrypt_private_state(args.input, args.directory, secret)
        print(f"[private-state] Decrypted {count} allowlisted file(s) into ephemeral runner storage.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PrivateStateError as exc:
        print(f"[private-state] ERROR: {exc}")
        raise SystemExit(1)
