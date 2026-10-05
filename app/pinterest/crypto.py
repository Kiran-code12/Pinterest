"""Encrypts OAuth tokens at rest (Fernet = AES-128-CBC + HMAC). Key is derived from SECRET_KEY."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


class TokenDecryptError(Exception):
    pass


class TokenCrypto:
    def __init__(self, secret_key: str):
        key = hashlib.sha256(b"pinterest-token-v1|" + secret_key.encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, blob: str) -> str:
        try:
            return self._fernet.decrypt(blob.encode()).decode()
        except (InvalidToken, ValueError) as ex:
            raise TokenDecryptError("Stored Pinterest authorization could not be read; reconnect Pinterest.") from ex
