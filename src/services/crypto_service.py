"""Symmetric encryption helpers for sensitive auth fields."""

from __future__ import annotations

import base64
import os
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoService:
    """Encrypt and decrypt sensitive strings using AES-256-GCM."""

    def __init__(self, key: str):
        self._raw_key = self._normalize_key(key)
        self._aesgcm = AESGCM(self._raw_key)

    @staticmethod
    def _normalize_key(key: str) -> bytes:
        try:
            decoded = base64.urlsafe_b64decode(key.encode("ascii"))
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        return sha256(key.encode("utf-8")).digest()

    def encrypt(self, plaintext: str | None) -> str | None:
        if not plaintext:
            return None
        nonce = os.urandom(12)
        cipher = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + cipher).decode("ascii")

    def decrypt(self, ciphertext: str | None) -> str | None:
        if not ciphertext:
            return None
        data = base64.urlsafe_b64decode(ciphertext.encode("ascii"))
        nonce, cipher = data[:12], data[12:]
        plain = self._aesgcm.decrypt(nonce, cipher, None)
        return plain.decode("utf-8")
