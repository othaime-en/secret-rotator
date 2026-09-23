"""
S3-compatible off-host backup sync.

Local backups — both secret rotation backups (BackupManager) and
master-key backups (tools/manage_key_backups.py's
MasterKeyBackupManager) — live in the same data volume as the master
key itself. A single volume or host loss takes out backups and the
key that protects them together, which defeats the point of having
backups at all (see the Phase 1 audit, "Scalability & Infrastructure":
"a single volume/host loss can be unrecoverable despite 'backup' being
in the product's name"). This module pushes copies of local backup
files to an S3-compatible object store (AWS S3, MinIO, Cloudflare R2,
etc. — anything reachable via `endpoint_url`) so they survive that
failure independently.

Deliberately EXCLUDED from remote sync, even though this module could
technically upload them — see tools/manage_key_backups.py for where
these decisions are enforced:

  - Plaintext master-key backups (`.key` files). A plaintext master
    key has no business leaving the host it's already on, encrypted
    or not, however access-controlled the destination bucket is.
  - Shamir secret-sharing key shares (`.share` files). The entire
    point of splitting the master key into shares is that no single
    location holds enough of them to reconstruct it. Uploading every
    share to the same bucket recreates exactly the single point of
    compromise Shamir splitting exists to avoid — shares are meant to
    go to different custodians/locations by design, not to one shared
    remote sync target. Off-host share storage is a legitimate thing
    to want, but it has to be a deliberate per-share, per-custodian
    decision an operator makes; automating it here would quietly
    undermine the reason shares exist at all.

Upload failures here are logged, never raised past this module: a
flaky or unreachable S3 endpoint must never block a rotation, a local
backup, or a master-key operation. The local copy is always the
primary safety net; remote sync is defense-in-depth on top of it, not
a dependency the app should ever come to rely on being reachable.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from secret_rotator.config.settings import settings
from secret_rotator.utils.logger import logger


class RemoteBackupClient:
    """Thin wrapper around an S3-compatible client, built from the
    `backup.remote_backup` config block (see config.example.yaml).

    Works against AWS S3 and any S3-compatible endpoint (MinIO,
    Cloudflare R2, etc.) by setting `endpoint_url`.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
    ):
        import boto3

        self.bucket = bucket
        # Always end in exactly one "/" (when non-empty) so joining
        # with a filename never produces a double slash or a missing
        # separator.
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""

        client_kwargs: Dict[str, Any] = {}
        if region:
            client_kwargs["region_name"] = region
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        # Only pass explicit credentials if both are actually set —
        # otherwise fall through to boto3's standard credential chain
        # (env vars, ~/.aws/credentials, instance/task role), which is
        # the recommended path per config.example.yaml's own comments.
        if access_key_id and secret_access_key:
            client_kwargs["aws_access_key_id"] = access_key_id
            client_kwargs["aws_secret_access_key"] = secret_access_key

        self._client = boto3.client("s3", **client_kwargs)

    @classmethod
    def from_config(cls) -> Optional["RemoteBackupClient"]:
        """Build a client from `backup.remote_backup` config, or
        return None if remote backup isn't enabled or is misconfigured.
        This is the normal way to obtain an instance — see
        bootstrap.py, which does this once and injects the result into
        BackupManager."""
        if not settings.get("backup.remote_backup.enabled", False):
            return None

        backend_type = settings.get("backup.remote_backup.type", "s3")
        if backend_type != "s3":
            logger.error(
                f"backup.remote_backup.type '{backend_type}' is not supported "
                f"(only 's3' is implemented — that also covers any "
                f"S3-compatible endpoint, like MinIO or R2, via "
                f"backup.remote_backup.endpoint_url). Remote backup sync is disabled."
            )
            return None

        bucket = settings.get("backup.remote_backup.bucket")
        if not bucket:
            logger.error(
                "backup.remote_backup.enabled is true but "
                "backup.remote_backup.bucket is not set. Remote backup sync "
                "is disabled."
            )
            return None

        return cls(
            bucket=bucket,
            prefix=settings.get("backup.remote_backup.prefix", "rotator/backups/"),
            region=settings.get("backup.remote_backup.region"),
            endpoint_url=settings.get("backup.remote_backup.endpoint_url") or None,
            access_key_id=(
                os.getenv("AWS_ACCESS_KEY_ID") or settings.get("backup.remote_backup.access_key_id")
            ),
            secret_access_key=(
                os.getenv("AWS_SECRET_ACCESS_KEY")
                or settings.get("backup.remote_backup.secret_access_key")
            ),
        )

    def _remote_key(self, subpath: str, filename: str) -> str:
        subpath = subpath.strip("/")
        if subpath:
            return f"{self.prefix}{subpath}/{filename}"
        return f"{self.prefix}{filename}"

    def upload_file(self, local_path: Path, subpath: str = "") -> bool:
        """Upload one file. Returns True on success, False on failure —
        never raises. A False return has already been logged; callers
        shouldn't retry inline, since the next sync_directory() sweep
        will pick up anything missed here."""
        remote_key = self._remote_key(subpath, local_path.name)
        try:
            self._client.upload_file(str(local_path), self.bucket, remote_key)
            logger.debug(f"Uploaded {local_path.name} to s3://{self.bucket}/{remote_key}")
            return True
        except Exception as e:
            logger.warning(
                f"Failed to upload {local_path.name} to remote backup storage "
                f"(s3://{self.bucket}/{remote_key}): {e}. The local backup is "
                f"unaffected; this will be retried on the next sync."
            )
            return False

    def list_remote_filenames(self, subpath: str = "") -> List[str]:
        """Return the bare filenames currently present under this
        client's prefix (+ optional subpath) in the bucket. Used to
        work out what still needs uploading without re-uploading
        everything on every sync."""
        remote_prefix = self._remote_key(subpath, "")
        filenames: List[str] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=remote_prefix):
                for obj in page.get("Contents", []):
                    filenames.append(Path(obj["Key"]).name)
        except Exception as e:
            logger.error(
                f"Failed to list remote backups at s3://{self.bucket}/{remote_prefix}: {e}"
            )
        return filenames

    def sync_directory(
        self, local_dir: Path, subpath: str = "", pattern: str = "*"
    ) -> Dict[str, Any]:
        """Upload every local file matching `pattern` that isn't
        already present remotely.

        "Already present" is checked by filename only, not content —
        local backup filenames are timestamped at creation and never
        rewritten afterward (see BackupManager.create_backup /
        MasterKeyBackupManager), so filename presence is a reliable
        "already uploaded" signal without needing to diff contents on
        every sync.

        Best-effort throughout: one failed upload doesn't stop the
        rest of the sync. Returns a report dict.
        """
        report: Dict[str, Any] = {
            "checked": 0,
            "uploaded": 0,
            "already_synced": 0,
            "failed": 0,
            "failed_files": [],
        }

        remote_filenames = set(self.list_remote_filenames(subpath))

        for local_file in sorted(local_dir.glob(pattern)):
            if not local_file.is_file():
                continue
            report["checked"] += 1

            if local_file.name in remote_filenames:
                report["already_synced"] += 1
                continue

            if self.upload_file(local_file, subpath=subpath):
                report["uploaded"] += 1
            else:
                report["failed"] += 1
                report["failed_files"].append(local_file.name)

        logger.info(
            f"Remote backup sync ({local_dir}): {report['uploaded']} uploaded, "
            f"{report['already_synced']} already synced, {report['failed']} failed"
        )
        return report

    def download_file(self, filename: str, local_path: Path, subpath: str = "") -> bool:
        """Download one backup by filename — for restoring from
        remote when local backups are unavailable (e.g. after the
        exact volume/host loss this module exists to protect against).
        Returns True on success."""
        remote_key = self._remote_key(subpath, filename)
        try:
            self._client.download_file(self.bucket, remote_key, str(local_path))
            logger.info(f"Downloaded s3://{self.bucket}/{remote_key} to {local_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to download s3://{self.bucket}/{remote_key}: {e}")
            return False
