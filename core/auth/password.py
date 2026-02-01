"""
 Password Utilities
 ------------------
 Secure hashing and verification using PBKDF2.
 """
import hashlib
import os
import base64

def hash_password(password: str) -> str:
    """Hashes a password with a random salt."""
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return f"{base64.b64encode(salt).decode()}:{base64.b64encode(pw_hash).decode()}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a password against a stored hash."""
    try:
        salt_b64, hash_b64 = stored_hash.split(":")
        salt = base64.b64decode(salt_b64)
        target_hash = base64.b64decode(hash_b64)
        new_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
        return new_hash == target_hash
    except Exception:
        return False
