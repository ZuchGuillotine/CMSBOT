"""Encryption for provider keys stored in the Hub's database.

The key comes from CMSBOT_HUB_SECRET_KEY, or is created once in a file beside the database
(readable only by the Hub's user). This protects keys if the database file alone is copied or
shared. It does not protect against someone who has the whole data folder; restrict access to that.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Vault:
    def __init__(self, db_path: str):
        configured = os.environ.get("CMSBOT_HUB_SECRET_KEY", "").strip()
        if configured:
            key = configured.encode()
        elif db_path == ":memory:":
            key = Fernet.generate_key()
        else:
            key_file = Path(db_path).parent / "secret.key"
            if not key_file.exists():
                key_file.parent.mkdir(parents=True, exist_ok=True)
                key_file.touch(mode=0o600)
                key_file.write_bytes(Fernet.generate_key())
            key = key_file.read_bytes().strip()
        try:
            self._fernet = Fernet(key)
        except ValueError as exc:
            raise RuntimeError("CMSBOT_HUB_SECRET_KEY (or secret.key) is not a valid key. "
                               "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"") from exc

    def seal(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def open(self, sealed: str) -> str | None:
        try:
            return self._fernet.decrypt(sealed.encode("ascii")).decode("utf-8")
        except InvalidToken:  # the encryption key changed; the stored value is unreadable and must be re-entered
            return None
