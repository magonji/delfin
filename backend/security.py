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
import threading
import time
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


class LoginThrottle:
    """Slows a guesser down without ever locking the owner out.

    There is one password here and no second factor, so the only thing between
    a guesser and the data is how many guesses a minute they get. Scrypt already
    makes each attempt cost something; this makes the tenth attempt cost a great
    deal more than the first.

    The first few failures pass free -- typing a password wrong is normal. After
    that each failure doubles the wait before the next attempt is even looked
    at, up to a ceiling. The ceiling matters: the wait must never grow into a
    lockout, because the person most likely to be waiting is the owner, and
    there is nobody to appeal to. Five minutes is long enough to make a
    dictionary hopeless -- roughly twelve tries an hour -- and short enough to
    wait out with a cup of tea.

    Attempts made during a wait are refused without checking the credential and
    without extending the wait, so a page left retrying in a loop cannot lock
    its own owner out for ever. A success clears everything.

    Kept in memory: a restart forgets it, which is fine, because nothing an
    attacker can reach from outside restarts the process.
    """

    def __init__(self, free_attempts: int = 5, base_delay: float = 5.0,
                 max_delay: float = 300.0):
        self._free = free_attempts
        self._base = base_delay
        self._cap = max_delay
        self._failures = 0
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def blocked_for(self) -> float:
        """Seconds still to wait before another attempt is looked at; 0 if now."""
        with self._lock:
            return max(0.0, self._next_allowed - time.monotonic())

    def failure(self) -> None:
        with self._lock:
            # A guess made while the door is shut was never looked at, so it does
            # not count against the next opening. The promise is kept here rather
            # than left to every caller to remember.
            if time.monotonic() < self._next_allowed:
                return
            self._failures += 1
            over = self._failures - self._free
            if over > 0:
                delay = min(self._cap, self._base * (2 ** (over - 1)))
                self._next_allowed = time.monotonic() + delay

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._next_allowed = 0.0


# One counter for the password and the recovery code together: they are two
# doors to the same key, and a guesser turned away from one would otherwise
# simply try the other.
login_throttle = LoginThrottle()


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


def _own_read_only(path: str) -> None:
    """Make a secret file readable by its owner and nobody else.

    Both of these are worth having: whoever can read the session secret can sign
    a cookie and walk in without the password, and whoever can read the keyfile
    can attack the password offline at their leisure. They were being written
    with the default 0644 -- readable by every account on the machine, which on a
    Pi shared with other services is not a theoretical distinction.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows and some network filesystems have no say in this; the file is
        # written either way.
        pass


def _read_keyfile() -> dict:
    _own_read_only(KEYFILE)
    with open(KEYFILE) as f:
        return json.load(f)


def _write_keyfile(data: dict) -> None:
    os.makedirs(os.path.dirname(KEYFILE), exist_ok=True)
    tmp = KEYFILE + ".tmp"
    # Created 0600 before anything is written into it, so the secret is never on
    # disk under a wider mode, not even for an instant.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, KEYFILE)
    _own_read_only(KEYFILE)


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
        # An installation that predates the mode above still has a world-readable
        # secret sitting there; narrow it on the way past.
        _own_read_only(SESSION_SECRET_FILE)
        with open(SESSION_SECRET_FILE) as f:
            return f.read().strip()
    secret = secrets.token_hex(32)
    os.makedirs(os.path.dirname(SESSION_SECRET_FILE), exist_ok=True)
    tmp = SESSION_SECRET_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(secret)
    os.replace(tmp, SESSION_SECRET_FILE)
    _own_read_only(SESSION_SECRET_FILE)
    return secret
