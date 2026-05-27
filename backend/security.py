"""
Security core: database encryption key management + authentication secrets.

Design
------
The database is encrypted with SQLCipher using a random 256-bit **data
encryption key (DEK)**. The DEK is never stored in the clear. Instead it is
stored **wrapped (encrypted) twice** in a keyfile:

  * once with a key derived (Scrypt) from the user's **password**, and
  * once with a key derived from a one-time **recovery code**.

Either secret can therefore unwrap the same DEK, so:

  * logging in unwraps the DEK and opens the DB;
  * the recovery code can unlock if the password is forgotten;
  * changing the password only re-wraps the (unchanged) DEK — the database is
    never re-encrypted, and existing backups stay valid.

Without the password or the recovery code, the keyfile reveals nothing (Scrypt +
AES-GCM), so an attacker holding the disk/SD/backups cannot read the data.

Everything here is pure Python (``cryptography`` + stdlib); it does not import
SQLCipher, so it is unit-testable without native libraries.
"""
import base64
import json
import os
import secrets
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

KEYFILE = "./data/.delfin_keyfile.json"
SESSION_SECRET_FILE = "./data/.delfin_session_secret"

# Scrypt parameters (login is infrequent; these stay snappy on a Raspberry Pi).
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32          # AES-256 wrapping key
_DEK_LEN = 32          # SQLCipher 256-bit raw key
_SALT_LEN = 16
_NONCE_LEN = 12


class InvalidCredential(Exception):
    """Wrong password or recovery code."""


# ---- low-level helpers -------------------------------------------------------

def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s)


def _derive(secret: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return kdf.derive(secret.encode("utf-8"))


def _wrap(dek: bytes, secret: str) -> dict:
    """Encrypt the DEK with a key derived from ``secret``. Returns a JSON-able dict."""
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = _derive(secret, salt)
    ct = AESGCM(key).encrypt(nonce, dek, None)
    return {"salt": _b64e(salt), "nonce": _b64e(nonce), "ct": _b64e(ct)}


def _unwrap(blob: dict, secret: str) -> bytes:
    """Recover the DEK from a wrapped blob. Raises InvalidCredential if ``secret`` is wrong."""
    try:
        key = _derive(secret, _b64d(blob["salt"]))
        return AESGCM(key).decrypt(_b64d(blob["nonce"]), _b64d(blob["ct"]), None)
    except Exception:
        raise InvalidCredential()


def normalize_recovery_code(code: str) -> str:
    """Uppercase and strip separators/spaces so the code can be typed loosely."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def generate_recovery_code() -> str:
    """A high-entropy, human-writable recovery code, e.g. ABCDE-FGHIJ-KLMNO-PQRST-UVWXY."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I
    groups = ["".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(5)]
    return "-".join(groups)  # 25 chars -> ~125 bits


# ---- keyfile / lifecycle -----------------------------------------------------

def is_initialised() -> bool:
    return os.path.exists(KEYFILE)


def _read_keyfile() -> dict:
    with open(KEYFILE) as f:
        return json.load(f)


def _write_keyfile(data: dict) -> None:
    os.makedirs(os.path.dirname(KEYFILE), exist_ok=True)
    tmp = KEYFILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, KEYFILE)


def setup(password: str) -> Tuple[str, str]:
    """Initialise encryption: create a DEK, wrap it by password and a fresh recovery
    code, and persist the keyfile. Returns (dek_hex, recovery_code).

    The recovery code is returned ONCE — it is not recoverable from the keyfile."""
    if is_initialised():
        raise RuntimeError("Already initialised.")
    if not password:
        raise ValueError("Password must not be empty.")
    dek = os.urandom(_DEK_LEN)
    recovery_code = generate_recovery_code()
    _write_keyfile({
        "version": 1,
        "kdf": {"name": "scrypt", "n": _SCRYPT_N, "r": _SCRYPT_R, "p": _SCRYPT_P},
        "password": _wrap(dek, password),
        "recovery": _wrap(dek, normalize_recovery_code(recovery_code)),
    })
    return dek.hex(), recovery_code


def unlock_with_password(password: str) -> str:
    """Return the DEK (hex) for the given password. Raises InvalidCredential."""
    return _unwrap(_read_keyfile()["password"], password).hex()


def unlock_with_recovery(code: str) -> str:
    """Return the DEK (hex) for the given recovery code. Raises InvalidCredential."""
    return _unwrap(_read_keyfile()["recovery"], normalize_recovery_code(code)).hex()


def change_password(old_password: str, new_password: str) -> None:
    """Re-wrap the DEK under a new password. Raises InvalidCredential if old is wrong."""
    if not new_password:
        raise ValueError("New password must not be empty.")
    data = _read_keyfile()
    dek = _unwrap(data["password"], old_password)   # verifies old password
    data["password"] = _wrap(dek, new_password)
    _write_keyfile(data)


def reset_password_with_recovery(code: str, new_password: str) -> str:
    """Set a new password using the recovery code. Returns the DEK (hex) so the
    caller can unlock immediately. Raises InvalidCredential if the code is wrong."""
    if not new_password:
        raise ValueError("New password must not be empty.")
    data = _read_keyfile()
    dek = _unwrap(data["recovery"], normalize_recovery_code(code))  # verifies code
    data["password"] = _wrap(dek, new_password)
    _write_keyfile(data)
    return dek.hex()


def regenerate_recovery_code(password: str) -> str:
    """Issue a fresh recovery code (invalidating the old one). Requires the password."""
    data = _read_keyfile()
    dek = _unwrap(data["password"], password)   # verifies password
    code = generate_recovery_code()
    data["recovery"] = _wrap(dek, normalize_recovery_code(code))
    _write_keyfile(data)
    return code


# ---- session signing secret --------------------------------------------------

def get_session_secret() -> str:
    """Stable random secret for signing session cookies (created on first use)."""
    if os.path.exists(SESSION_SECRET_FILE):
        with open(SESSION_SECRET_FILE) as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    os.makedirs(os.path.dirname(SESSION_SECRET_FILE), exist_ok=True)
    tmp = SESSION_SECRET_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(secret)
    os.replace(tmp, SESSION_SECRET_FILE)
    return secret
