"""Local secure storage adapter for persisting raw upload files."""

import hashlib
import os
import uuid
from app.core.config import settings


class LocalStorageAdapter:
    """Manages raw file persistence with cryptographic checksums."""

    def __init__(self, storage_dir: str = settings.STORAGE_DIR):
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

    def save_file(self, content_bytes: bytes, original_filename: str) -> dict:
        """
        Store uploaded bytes and return storage metadata.
        Returns: { 'storage_key', 'sha256', 'size_bytes' }
        """
        sha256 = hashlib.sha256(content_bytes).hexdigest()
        ext = os.path.splitext(original_filename)[1]
        unique_name = f"{uuid.uuid4().hex}{ext}"
        storage_path = os.path.join(self.storage_dir, unique_name)

        with open(storage_path, "wb") as f:
            f.write(content_bytes)

        return {
            "storage_key": storage_path,
            "sha256": sha256,
            "size_bytes": len(content_bytes),
        }

    def read_file(self, storage_key: str) -> str:
        """Read and decode stored text content."""
        with open(storage_key, "r", encoding="utf-8", errors="replace") as f:
            return f.read()


storage_adapter = LocalStorageAdapter()
