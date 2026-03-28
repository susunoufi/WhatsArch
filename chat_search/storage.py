"""Cloud storage module for WhatsArch.

Handles uploading/downloading chat data to/from Supabase Storage.
Provides transparent fallback: if local file missing, download from cloud.
"""

import io
import os
import json
import tarfile
import tempfile
from pathlib import Path

BUCKET_NAME = "user-chats"

# Storage quota per plan (in bytes)
STORAGE_QUOTAS = {
    "budget": 2 * 1024 * 1024 * 1024,     # 2 GB
    "balanced": 10 * 1024 * 1024 * 1024,   # 10 GB
    "premium": 50 * 1024 * 1024 * 1024,    # 50 GB
}


def _get_storage_path(user_id: str, chat_name: str, filename: str = "") -> str:
    """Build storage path: user_id/chat_name/filename."""
    parts = [user_id, chat_name]
    if filename:
        parts.append(filename)
    return "/".join(parts)


def ensure_bucket(sb) -> bool:
    """Create the storage bucket if it doesn't exist."""
    try:
        sb.storage.get_bucket(BUCKET_NAME)
        return True
    except Exception:
        try:
            sb.storage.create_bucket(BUCKET_NAME, options={
                "public": False,
                "file_size_limit": 500 * 1024 * 1024,  # 500MB per file
            })
            return True
        except Exception as e:
            print(f"[Storage] Failed to create bucket: {e}")
            return False


def upload_file(sb, user_id: str, chat_name: str, local_path: str, remote_name: str = None) -> str:
    """Upload a single file to Supabase Storage.

    Returns the storage path on success, empty string on failure.
    """
    if not sb:
        return ""

    remote_name = remote_name or os.path.basename(local_path)
    storage_path = _get_storage_path(user_id, chat_name, remote_name)

    try:
        with open(local_path, "rb") as f:
            data = f.read()

        # Try to remove existing file first (upsert)
        try:
            sb.storage.from_(BUCKET_NAME).remove([storage_path])
        except Exception:
            pass

        sb.storage.from_(BUCKET_NAME).upload(storage_path, data)
        return storage_path
    except Exception as e:
        print(f"[Storage] Upload failed for {storage_path}: {e}")
        return ""


def download_file(sb, user_id: str, chat_name: str, remote_name: str, local_path: str) -> bool:
    """Download a file from Supabase Storage to local disk.

    Returns True on success.
    """
    if not sb:
        return False

    storage_path = _get_storage_path(user_id, chat_name, remote_name)

    try:
        data = sb.storage.from_(BUCKET_NAME).download(storage_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"[Storage] Download failed for {storage_path}: {e}")
        return False


def upload_chat_data(sb, user_id: str, chat_name: str, chat_dir: str) -> dict:
    """Upload all processed chat data to Supabase Storage.

    Uploads: chat.db, embeddings, caches as individual files.
    Returns dict with storage paths.
    """
    if not sb:
        return {}

    ensure_bucket(sb)
    result = {}
    data_dir = os.path.join(chat_dir, "data")

    # Upload key files
    files_to_upload = [
        ("chat.db", "chat.db"),
        ("chat_chunk_embeddings.npy", "embeddings.npy"),
        ("descriptions.json", "descriptions.json"),
        ("transcriptions.json", "transcriptions.json"),
        ("video_transcriptions.json", "video_transcriptions.json"),
        ("pdf_texts.json", "pdf_texts.json"),
    ]

    total_bytes = 0
    for local_name, remote_name in files_to_upload:
        local_path = os.path.join(data_dir, local_name)
        if os.path.exists(local_path):
            path = upload_file(sb, user_id, chat_name, local_path, f"data/{remote_name}")
            if path:
                result[remote_name] = path
                total_bytes += os.path.getsize(local_path)

    # Upload the chat export file
    for export_file in ["_chat.txt", "result.json"]:
        export_path = os.path.join(chat_dir, export_file)
        if os.path.exists(export_path):
            path = upload_file(sb, user_id, chat_name, export_path, export_file)
            if path:
                result[export_file] = path
                total_bytes += os.path.getsize(export_path)
            break

    result["total_bytes"] = total_bytes
    return result


def download_chat_data(sb, user_id: str, chat_name: str, chat_dir: str) -> bool:
    """Download chat data from Supabase Storage to local disk.

    Downloads the essential files needed for search and AI.
    Returns True if at least chat.db was downloaded.
    """
    if not sb:
        return False

    data_dir = os.path.join(chat_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # Download key files
    files_to_download = [
        ("data/chat.db", os.path.join(data_dir, "chat.db")),
        ("data/embeddings.npy", os.path.join(data_dir, "chat_chunk_embeddings.npy")),
        ("data/descriptions.json", os.path.join(data_dir, "descriptions.json")),
        ("data/transcriptions.json", os.path.join(data_dir, "transcriptions.json")),
        ("data/video_transcriptions.json", os.path.join(data_dir, "video_transcriptions.json")),
        ("data/pdf_texts.json", os.path.join(data_dir, "pdf_texts.json")),
    ]

    db_ok = False
    for remote_name, local_path in files_to_download:
        if not os.path.exists(local_path):
            success = download_file(sb, user_id, chat_name, remote_name, local_path)
            if remote_name == "data/chat.db" and success:
                db_ok = True
        elif remote_name == "data/chat.db":
            db_ok = True  # Already exists locally

    # Download export file
    for export_file in ["_chat.txt", "result.json"]:
        local_path = os.path.join(chat_dir, export_file)
        if not os.path.exists(local_path):
            download_file(sb, user_id, chat_name, export_file, local_path)

    return db_ok


def delete_chat_storage(sb, user_id: str, chat_name: str) -> bool:
    """Delete all storage for a chat."""
    if not sb:
        return False

    prefix = _get_storage_path(user_id, chat_name)

    try:
        # List all files under this chat's prefix
        files = sb.storage.from_(BUCKET_NAME).list(prefix)
        if files:
            paths = [f"{prefix}/{f['name']}" for f in files]
            sb.storage.from_(BUCKET_NAME).remove(paths)

        # Also try to remove nested data/ files
        data_prefix = f"{prefix}/data"
        try:
            data_files = sb.storage.from_(BUCKET_NAME).list(data_prefix)
            if data_files:
                data_paths = [f"{data_prefix}/{f['name']}" for f in data_files]
                sb.storage.from_(BUCKET_NAME).remove(data_paths)
        except Exception:
            pass

        return True
    except Exception as e:
        print(f"[Storage] Delete failed for {prefix}: {e}")
        return False


def get_user_storage_usage(sb, user_id: str) -> dict:
    """Get storage usage stats for a user."""
    if not sb:
        return {"total_bytes": 0, "chats": []}

    try:
        items = sb.storage.from_(BUCKET_NAME).list(user_id)
        chats = []
        total = 0
        for item in (items or []):
            if item.get("id"):
                # This is a folder (chat)
                chat_name = item["name"]
                # List files in the chat folder
                chat_files = sb.storage.from_(BUCKET_NAME).list(f"{user_id}/{chat_name}")
                chat_size = sum(f.get("metadata", {}).get("size", 0) for f in (chat_files or []))
                total += chat_size
                chats.append({
                    "chat_name": chat_name,
                    "size_bytes": chat_size,
                    "created_at": item.get("created_at", ""),
                })

        return {"total_bytes": total, "chats": chats}
    except Exception as e:
        print(f"[Storage] Usage query failed: {e}")
        return {"total_bytes": 0, "chats": []}


def check_storage_quota(sb, user_id: str, plan: str, additional_bytes: int = 0) -> tuple:
    """Check if user has enough storage quota.

    Returns (allowed: bool, usage_bytes: int, quota_bytes: int).
    """
    quota = STORAGE_QUOTAS.get(plan, STORAGE_QUOTAS["budget"])
    usage = get_user_storage_usage(sb, user_id)
    current = usage["total_bytes"]
    allowed = (current + additional_bytes) <= quota
    return allowed, current, quota


def ensure_local_chat(sb, user_id: str, chat_name: str, chats_dir: str) -> str:
    """Ensure chat data is available locally. Download from cloud if needed.

    Returns the local chat directory path, or empty string on failure.
    """
    chat_dir = os.path.join(chats_dir, chat_name)
    db_path = os.path.join(chat_dir, "data", "chat.db")

    # Already available locally
    if os.path.exists(db_path):
        return chat_dir

    # Try to download from cloud
    if sb and user_id:
        print(f"[Storage] Downloading {chat_name} from cloud for user {user_id[:8]}...")
        if download_chat_data(sb, user_id, chat_name, chat_dir):
            return chat_dir

    return "" if not os.path.exists(db_path) else chat_dir
