"""Where image and video files live. The Hub stores links; the bytes go to a storage backend.

  local   a folder on the Hub's disk, served at /media/... (default; fine for evaluation)
  s3      any S3-compatible bucket: Cloudflare R2, AWS S3, Backblaze B2 (set CMSBOT_MEDIA_BUCKET)

Agents upload through the Hub, so only the Hub holds bucket credentials. File names include a random
part: links are unguessable but not private, because posting APIs must be able to fetch them.
"""

from __future__ import annotations

import mimetypes
import os
import re
import secrets
from pathlib import Path

MAX_UPLOAD_BYTES = int(os.environ.get("CMSBOT_MEDIA_MAX_MB", "200")) * 1024 * 1024
ALLOWED_TYPES = ("image/", "video/", "audio/")


class MediaError(Exception):
    pass


def media_key(project: str, item_id: int, name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem or ext).strip("-")[:60] or "file"
    suffix = re.sub(r"[^a-z0-9]", "", ext.lower())[:5] if dot else ""
    return f"{project}/{item_id}/{secrets.token_hex(6)}-{safe}" + (f".{suffix}" if suffix else "")


def content_type_for(name: str, declared: str = "") -> str:
    if name.lower().endswith(".json"):  # word timings for a voiceover
        return "application/json"
    guessed = mimetypes.guess_type(name)[0] or declared.split(";")[0].strip() or "application/octet-stream"
    if not guessed.startswith(ALLOWED_TYPES):
        raise MediaError(f"'{name}' is {guessed}; only image, video and audio files can be attached.")
    return guessed


class LocalStorage:
    kind = "local"

    def __init__(self, root: str | Path, public_base: str = ""):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base = public_base.rstrip("/")

    def put(self, key: str, data: bytes, content_type: str) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"/media/{key}"

    def path_for(self, key: str) -> Path | None:
        path = (self.root / key).resolve()
        return path if path.is_file() and self.root.resolve() in path.parents else None


class S3Storage:
    kind = "s3"
    root = None

    def __init__(self, bucket: str, public_base: str, *, endpoint: str = "", access_key: str = "", secret_key: str = "",
                 region: str = "auto", client=None):
        if not public_base:
            raise MediaError("Set CMSBOT_MEDIA_PUBLIC_URL to the bucket's public address.")
        self.bucket, self.public_base = bucket, public_base.rstrip("/")
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise MediaError('Bucket storage needs boto3: pip install "cmsbot[s3]"') from exc
            client = boto3.client("s3", endpoint_url=endpoint or None, aws_access_key_id=access_key or None,
                                  aws_secret_access_key=secret_key or None, region_name=region)
        self.client = client

    def put(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return f"{self.public_base}/{key}"

    def path_for(self, key: str) -> None:
        return None


def storage_from_env(db_path: str):
    bucket = os.environ.get("CMSBOT_MEDIA_BUCKET", "")
    if bucket:
        return S3Storage(bucket, os.environ.get("CMSBOT_MEDIA_PUBLIC_URL", ""),
                         endpoint=os.environ.get("CMSBOT_MEDIA_ENDPOINT", ""),
                         access_key=os.environ.get("CMSBOT_MEDIA_ACCESS_KEY_ID", ""),
                         secret_key=os.environ.get("CMSBOT_MEDIA_SECRET_ACCESS_KEY", ""),
                         region=os.environ.get("CMSBOT_MEDIA_REGION", "auto"))
    default_dir = Path(db_path).parent / "media" if db_path != ":memory:" else Path("hub-data/media")
    return LocalStorage(os.environ.get("CMSBOT_MEDIA_DIR", str(default_dir)), os.environ.get("CMSBOT_HUB_PUBLIC_URL", ""))
