"""
Secret providers package.
Providers handle where secrets are stored (file, AWS, etc.)
"""

from secret_rotator.providers.base import SecretProvider
from .file_provider import FileSecretProvider

__all__ = ["SecretProvider", "FileSecretProvider"]
