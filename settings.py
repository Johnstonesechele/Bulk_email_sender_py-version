# settings.py
import os
from typing import Optional, Dict
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.fernet import Fernet, InvalidToken
import base64
import json
import secrets

SALT_PATH = "smtp_salt.bin"
STORE_PATH = "smtp_settings.bin"

def _ensure_salt():
    if not os.path.exists(SALT_PATH):
        salt = secrets.token_bytes(16)
        with open(SALT_PATH, "wb") as f:
            f.write(salt)
        return salt
    else:
        with open(SALT_PATH, "rb") as f:
            return f.read()

def derive_key(password: str, salt: bytes) -> bytes:
    """
    Derive a 32-byte key suitable for Fernet from a password and salt.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
        backend=default_backend()
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return key

def save_smtp_settings(password: str, settings: Dict[str, str]) -> None:
    """
    Encrypt and store SMTP settings dict (host, port, username, password, use_tls).
    """
    salt = _ensure_salt()
    key = derive_key(password, salt)
    f = Fernet(key)
    raw = json.dumps(settings).encode("utf-8")
    token = f.encrypt(raw)
    with open(STORE_PATH, "wb") as fh:
        fh.write(token)

def load_smtp_settings(password: str) -> Optional[Dict[str, str]]:
    """
    Decrypt and return settings. Raises ValueError if password wrong / corrupted.
    """
    if not os.path.exists(STORE_PATH):
        return None
    salt = _ensure_salt()
    key = derive_key(password, salt)
    f = Fernet(key)
    with open(STORE_PATH, "rb") as fh:
        token = fh.read()
    try:
        raw = f.decrypt(token)
    except InvalidToken as e:
        raise ValueError("Invalid master password or corrupted settings") from e
    settings = json.loads(raw.decode("utf-8"))
    return settings

def settings_exist() -> bool:
    return os.path.exists(STORE_PATH)
