"""
Shared engine construction.

Both the main daemon process (SecretRotationApp.setup(), in main.py)
and an RQ worker process (job_queue.run_rotation_job()) need an
identically-wired RotationEngine built from the same config - the
daemon/web process to serve the dashboard and enqueue jobs, a worker
process to actually execute a rotation sweep. A worker runs in its own
OS process (frequently its own container) with no access to the
enqueuing process's in-memory objects, so it has to build its own
engine from scratch for every job it picks up.

Before this existed, that construction logic (providers, rotators,
jobs, encryption/backup managers) lived only inside
SecretRotationApp.setup(). Duplicating a hand-copied version of it for
workers would drift over time - a new provider type added to one copy
and forgotten in the other, the job list built subtly differently - so
it's factored out here once, and both call sites use it.
"""

from typing import Any, Dict, Optional, Tuple

from secret_rotator.backup_manager import BackupManager
from secret_rotator.config.settings import settings
from secret_rotator.encryption_manager import EncryptionManager
from secret_rotator.providers.file_provider import FileSecretProvider
from secret_rotator.providers.base import SecretProvider
from secret_rotator.rotation_engine import RotationEngine
from secret_rotator.rotators.base import SecretRotator
from secret_rotator.rotators.password_rotator import PasswordRotator
from secret_rotator.utils.logger import logger


def build_encryption_manager() -> Optional[EncryptionManager]:
    """Returns None if encryption is explicitly disabled in config
    (security.encryption.enabled: false) - callers must handle that
    case rather than assume a manager is always present."""
    encryption_enabled = settings.get("security.encryption.enabled", True)
    if not encryption_enabled:
        logger.warning("Encryption is DISABLED - secrets will be stored in plaintext!")
        return None

    key_file = settings.get("security.encryption.master_key_file", "data/.master.key")
    manager = EncryptionManager(key_file=key_file)
    logger.info("Encryption initialized")
    return manager


def build_backup_manager() -> BackupManager:
    encrypt_backups = settings.get("backup.encrypt_backups", True)
    backup_dir = settings.get("backup.storage_path", "data/backup")

    # RemoteBackupClient.from_config() returns None when
    # backup.remote_backup.enabled is false (the default) or
    # misconfigured — BackupManager treats that exactly like "remote
    # backup isn't a thing" everywhere else in this codebase, so
    # nothing downstream needs its own None-check for this.
    from secret_rotator.remote_backup import RemoteBackupClient

    remote_backup_client = RemoteBackupClient.from_config()

    return BackupManager(
        backup_dir=backup_dir,
        encrypt_backups=encrypt_backups,
        remote_backup_client=remote_backup_client,
    )


def build_provider(name: str, config: Dict[str, Any]) -> Optional[SecretProvider]:
    """Build one provider from its config, or None if its type is not
    available (not implemented yet, or unknown).

    Used for every provider at startup and by the dashboard when it adds or
    changes one, so both build them identically.
    """
    provider_type = config.get("type") if isinstance(config, dict) else None

    if provider_type == "file":
        encrypt_secrets = settings.get("security.encryption.enabled", True)
        return FileSecretProvider(
            name=name,
            config={
                "file_path": config.get("file_path", "data/secrets.json"),
                "encrypt_secrets": encrypt_secrets,
                "encryption_key_file": settings.get(
                    "security.encryption.master_key_file", "data/.master.key"
                ),
            },
        )

    # Add support for other provider types here (AWS, Azure, etc.)
    if provider_type == "aws":
        logger.warning(f"AWS provider '{name}' not yet implemented")
    else:
        logger.warning(f"Unknown provider type '{provider_type}' for '{name}'")
    return None


def build_rotator(name: str, config: Dict[str, Any]) -> Optional[SecretRotator]:
    """Build one rotator from its config, or None for an unknown type."""
    rotator_type = config.get("type") if isinstance(config, dict) else None

    if rotator_type == "password":
        return PasswordRotator(name=name, config=config)

    if rotator_type == "api_key":
        from secret_rotator.rotators.advanced_rotators import APIKeyRotator

        return APIKeyRotator(name=name, config=config)

    if rotator_type == "jwt_secret":
        from secret_rotator.rotators.advanced_rotators import JWTSecretRotator

        return JWTSecretRotator(name=name, config=config)

    logger.warning(f"Unknown rotator type '{rotator_type}' for '{name}'")
    return None


def _setup_providers(engine: RotationEngine) -> None:
    providers_config = settings.get("providers", {})

    for provider_name, provider_config in providers_config.items():
        provider = build_provider(provider_name, provider_config)
        if provider is None:
            continue

        engine.register_provider(provider)
        if provider.validate_connection():
            logger.info(f"Provider '{provider_name}' validated successfully")
        else:
            logger.error(f"Provider '{provider_name}' validation failed!")


def _setup_rotators(engine: RotationEngine) -> None:
    rotators_config = settings.get("rotators", {})

    for rotator_name, rotator_config in rotators_config.items():
        rotator = build_rotator(rotator_name, rotator_config)
        if rotator is not None:
            engine.register_rotator(rotator)


def _setup_rotation_jobs(engine: RotationEngine) -> None:
    jobs = settings.get("jobs", [])

    if jobs:
        for job in jobs:
            if engine.add_rotation_job(job):
                logger.debug(f"Added job: {job['name']}")
        logger.info(f"Loaded {len(jobs)} rotation jobs from config")
    else:
        logger.warning("No rotation jobs configured. Add jobs to config/config.yaml")


def build_rotation_engine() -> Tuple[RotationEngine, Optional[EncryptionManager], BackupManager]:
    """Build a fully-wired RotationEngine (providers, rotators, jobs,
    backup manager) from the current config.

    Used by both the main daemon process and RQ worker processes, so
    they always agree on what "rotate everything" means — see module
    docstring.
    """
    encryption_manager = build_encryption_manager()
    backup_manager = build_backup_manager()

    engine = RotationEngine()
    engine.backup_manager = backup_manager

    _setup_providers(engine)
    _setup_rotators(engine)
    _setup_rotation_jobs(engine)

    return engine, encryption_manager, backup_manager
