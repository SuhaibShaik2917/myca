import os
import hashlib
from typing import Tuple, Optional
import uuid

ALLOWED_MIME_TYPES = {
    "application/pdf": b"%PDF",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
    "image/jpg": b"\xff\xd8\xff",
}

MAX_FILE_SIZE_BYTES = 15 * 1024 * 1024  # 15 MB

class StorageError(Exception):
    pass

class DuplicateDocumentError(StorageError):
    def __init__(self, message: str, existing_document_id: str):
        super().__init__(message)
        self.existing_document_id = existing_document_id

def validate_magic_bytes(header: bytes) -> str:
    """
    Validates file format against expected magic byte signatures.
    Returns canonical MIME type.
    """
    if header.startswith(b"%PDF"):
        return "application/pdf"
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise StorageError("Invalid file signature. Only PDF, PNG, and JPEG documents are permitted.")

def compute_stream_sha256_and_save(
    file_stream,
    org_id: str,
    doc_id: str,
    base_dir: str = "storage"
) -> Tuple[str, int, str, str]:
    """
    Streams file from input, validates magic bytes and size, computes SHA-256,
    and writes to isolated path: {base_dir}/{org_id}/{hash_prefix}/{doc_id}.bin.
    Returns: (file_path, file_size_bytes, sha256_hex, mime_type)
    """
    hasher = hashlib.sha256()
    total_bytes = 0
    header_bytes = bytearray()
    mime_type: Optional[str] = None

    # Temporary staging directory to prevent partial uploads on error
    target_dir = os.path.join(base_dir, str(org_id))
    os.makedirs(target_dir, exist_ok=True)
    temp_path = os.path.join(target_dir, f"tmp_{doc_id}.bin")

    try:
        with open(temp_path, "wb") as f_out:
            while True:
                chunk = file_stream.read(64 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_FILE_SIZE_BYTES:
                    raise StorageError(f"File exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES / (1024 * 1024)} MB")
                
                if len(header_bytes) < 16:
                    header_bytes.extend(chunk[:16 - len(header_bytes)])
                    if len(header_bytes) >= 4 and mime_type is None:
                        mime_type = validate_magic_bytes(bytes(header_bytes))

                hasher.update(chunk)
                f_out.write(chunk)

        if total_bytes == 0:
            raise StorageError("Uploaded document is empty (0 bytes)")

        if mime_type is None:
            mime_type = validate_magic_bytes(bytes(header_bytes))

        sha256_hex = hasher.hexdigest()
        hash_prefix = sha256_hex[:2]
        final_dir = os.path.join(base_dir, str(org_id), hash_prefix)
        os.makedirs(final_dir, exist_ok=True)
        final_path = os.path.join(final_dir, f"{doc_id}.bin")

        os.replace(temp_path, final_path)
        return final_path, total_bytes, sha256_hex, mime_type

    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise
