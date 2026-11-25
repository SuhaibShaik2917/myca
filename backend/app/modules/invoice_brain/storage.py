import os
import time
import hashlib
from datetime import datetime, timezone
from typing import Tuple, Optional, Dict, Any, List
import uuid
from urllib.parse import urlparse
from sqlalchemy import text
from sqlalchemy.orm import Session

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


def is_s3_uri(file_path: str) -> bool:
    return bool(file_path and str(file_path).strip().lower().startswith("s3://"))


def parse_s3_uri(file_path: str) -> Tuple[str, str]:
    parsed = urlparse(str(file_path).strip())
    if parsed.scheme.lower() != "s3" or not parsed.netloc or not parsed.path:
        raise StorageError(f"INVALID_S3_URI: '{file_path}' is not a valid s3://bucket/key URI.")
    key = parsed.path.lstrip("/")
    if not key or ".." in key.split("/"):
        raise StorageError(f"INVALID_S3_KEY: Unsafe or empty object key in '{file_path}'.")
    return parsed.netloc, key


def _get_s3_client():
    from app.config import settings

    region = (os.getenv("AWS_REGION") or getattr(settings, "AWS_REGION", "ap-south-1") or "ap-south-1").strip()
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise StorageError("S3_STORAGE_UNAVAILABLE: boto3 dependency is required for S3 storage operations.") from exc
    return boto3.client("s3", region_name=region)


def _assert_org_isolation(file_path: str, expected_org_id: Optional[str]) -> None:
    if not expected_org_id:
        return
    org_clean = str(expected_org_id).strip()
    if is_s3_uri(file_path):
        _, key = parse_s3_uri(file_path)
        if not key.startswith(f"{org_clean}/"):
            raise StorageError(
                f"TENANT_STORAGE_ISOLATION_VIOLATION: Object key '{key}' does not belong to org '{org_clean}'."
            )
    else:
        norm = os.path.normpath(os.path.abspath(file_path))
        expected_segment = f"{os.sep}{org_clean}{os.sep}"
        if expected_segment not in norm:
            raise StorageError(
                f"TENANT_STORAGE_ISOLATION_VIOLATION: Path '{file_path}' does not belong to org '{org_clean}'."
            )


def read_document_bytes(file_path: str, expected_org_id: Optional[str] = None) -> bytes:
    """Retrieves stored document bytes safely for either local filesystem or s3:// URI."""
    _assert_org_isolation(file_path, expected_org_id)
    if is_s3_uri(file_path):
        bucket, key = parse_s3_uri(file_path)
        s3_client = _get_s3_client()
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()
    with open(file_path, "rb") as f:
        return f.read()


def stat_document_size(file_path: str, expected_org_id: Optional[str] = None) -> int:
    """Returns stored document byte size for either local filesystem or s3:// URI."""
    _assert_org_isolation(file_path, expected_org_id)
    if is_s3_uri(file_path):
        bucket, key = parse_s3_uri(file_path)
        s3_client = _get_s3_client()
        head = s3_client.head_object(Bucket=bucket, Key=key)
        return int(head.get("ContentLength", 0))
    return int(os.stat(file_path).st_size)


def delete_stored_document(file_path: str, expected_org_id: Optional[str] = None) -> bool:
    """Deletes stored document from local filesystem or s3:// URI without passing URIs to os.remove."""
    if not file_path:
        return False
    _assert_org_isolation(file_path, expected_org_id)
    if is_s3_uri(file_path):
        bucket, key = parse_s3_uri(file_path)
        s3_client = _get_s3_client()
        s3_client.delete_object(Bucket=bucket, Key=key)
        return True
    if os.path.exists(file_path):
        os.remove(file_path)
        return True
    return False

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
    base_dir: Optional[str] = None
) -> Tuple[str, int, str, str]:
    """
    Streams file from input, validates magic bytes and size, computes SHA-256,
    and writes to configured STORAGE_BACKEND ('local' or fail-closed 's3').
    Returns: (storage_path_or_uri, file_size_bytes, sha256_hex, mime_type)
    """
    from app.config import settings

    backend_mode = (os.getenv("STORAGE_BACKEND") or getattr(settings, "STORAGE_BACKEND", "local") or "local").strip().lower()
    if backend_mode == "s3":
        bucket = (os.getenv("AWS_S3_BUCKET_NAME") or getattr(settings, "AWS_S3_BUCKET_NAME", None) or "").strip()
        if not bucket:
            raise StorageError("S3_STORAGE_UNAVAILABLE: AWS_S3_BUCKET_NAME must be configured when STORAGE_BACKEND=s3.")
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise StorageError("S3_STORAGE_UNAVAILABLE: boto3 dependency is required for STORAGE_BACKEND=s3.") from exc
    elif backend_mode != "local":
        raise StorageError(f"UNSUPPORTED_STORAGE_BACKEND: '{backend_mode}'")

    effective_base_dir = base_dir or (os.getenv("STORAGE_LOCAL_ROOT") or getattr(settings, "STORAGE_LOCAL_ROOT", "storage") or "storage")

    hasher = hashlib.sha256()
    total_bytes = 0
    header_bytes = bytearray()
    mime_type: Optional[str] = None

    # Temporary staging directory to prevent partial uploads on error
    target_dir = os.path.join(effective_base_dir, str(org_id))
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

        if backend_mode == "s3":
            s3_key = f"{org_id}/{hash_prefix}/{doc_id}.bin"
            region = (os.getenv("AWS_REGION") or getattr(settings, "AWS_REGION", "ap-south-1") or "ap-south-1").strip()
            kms_key = (os.getenv("AWS_S3_KMS_KEY_ID") or getattr(settings, "AWS_S3_KMS_KEY_ID", None) or "").strip()
            put_kwargs: Dict[str, Any] = {
                "Bucket": bucket,
                "Key": s3_key,
                "ContentType": mime_type,
                "ServerSideEncryption": "aws:kms" if kms_key else "AES256",
            }
            if kms_key:
                put_kwargs["SSEKMSKeyId"] = kms_key
            s3_client = boto3.client("s3", region_name=region)
            with open(temp_path, "rb") as f_in:
                put_kwargs["Body"] = f_in
                s3_client.put_object(**put_kwargs)
            os.remove(temp_path)
            return f"s3://{bucket}/{s3_key}", total_bytes, sha256_hex, mime_type

        final_dir = os.path.join(effective_base_dir, str(org_id), hash_prefix)
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


def reconcile_storage_orphans(
    session: Session,
    base_dir: Optional[str] = None,
    grace_period_seconds: int = 1800
) -> Dict[str, Any]:
    """
    Periodic storage reconciliation mechanism across configured backend ('local' or 's3'):
    1. Queries all active document file paths from PostgreSQL public.documents.
    2. Scans storage root (directory tree for local, list_objects_v2 for S3) ignoring cross-backend URIs.
    3. Reclaims space by purging unreferenced orphan objects older than grace_period_seconds.
    4. Safely preserves in-flight uploads younger than grace_period_seconds.
    """
    from app.config import settings

    backend_mode = (os.getenv("STORAGE_BACKEND") or getattr(settings, "STORAGE_BACKEND", "local") or "local").strip().lower()
    doc_stmt = text("SELECT file_path FROM public.documents WHERE file_path IS NOT NULL;")
    rows = session.execute(doc_stmt).fetchall()

    now = time.time()
    scanned_count = 0
    in_flight_count = 0
    orphan_count = 0
    reclaimed_bytes = 0
    deleted_files: List[str] = []

    if backend_mode == "s3":
        bucket = (os.getenv("AWS_S3_BUCKET_NAME") or getattr(settings, "AWS_S3_BUCKET_NAME", None) or "").strip()
        if not bucket:
            raise StorageError("S3_STORAGE_UNAVAILABLE: AWS_S3_BUCKET_NAME must be configured for S3 orphan reconciliation.")
        active_uris = {str(r[0]).strip() for r in rows if r[0] and is_s3_uri(r[0])}
        s3_client = _get_s3_client()
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                uri = f"s3://{bucket}/{key}"
                scanned_count += 1
                if uri in active_uris:
                    continue
                last_mod = obj.get("LastModified")
                mod_ts = last_mod.timestamp() if isinstance(last_mod, datetime) else now
                if (now - mod_ts) < grace_period_seconds:
                    in_flight_count += 1
                    continue
                s3_client.delete_object(Bucket=bucket, Key=key)
                orphan_count += 1
                reclaimed_bytes += int(obj.get("Size", 0))
                deleted_files.append(uri)
        return {
            "scanned_files": scanned_count,
            "active_db_files": len(active_uris),
            "in_flight_files": in_flight_count,
            "orphaned_files_deleted": orphan_count,
            "reclaimed_bytes": reclaimed_bytes,
            "deleted_file_paths": deleted_files,
        }

    effective_base_dir = base_dir or (os.getenv("STORAGE_LOCAL_ROOT") or getattr(settings, "STORAGE_LOCAL_ROOT", "storage") or "storage")
    if not os.path.exists(effective_base_dir):
        return {
            "scanned_files": 0,
            "active_db_files": 0,
            "in_flight_files": 0,
            "orphaned_files_deleted": 0,
            "reclaimed_bytes": 0,
            "deleted_file_paths": []
        }

    active_paths = {
        os.path.normcase(os.path.abspath(r[0]))
        for r in rows
        if r[0] and not is_s3_uri(r[0])
    }

    for root, dirs, files in os.walk(effective_base_dir):
        for f in files:
            if f == ".gitkeep":
                continue
            scanned_count += 1
            full_path = os.path.join(root, f)
            norm_path = os.path.normcase(os.path.abspath(full_path))

            if norm_path in active_paths:
                continue

            try:
                st = os.stat(full_path)
                file_age = now - st.st_mtime
                if file_age >= grace_period_seconds:
                    file_size = st.st_size
                    os.remove(full_path)
                    orphan_count += 1
                    reclaimed_bytes += file_size
                    deleted_files.append(full_path)
                else:
                    in_flight_count += 1
            except OSError:
                pass

    return {
        "scanned_files": scanned_count,
        "active_db_files": len(active_paths),
        "in_flight_files": in_flight_count,
        "orphaned_files_deleted": orphan_count,
        "reclaimed_bytes": reclaimed_bytes,
        "deleted_file_paths": deleted_files
    }
