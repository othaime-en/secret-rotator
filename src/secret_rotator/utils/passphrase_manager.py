"""
Universal passphrase management for PyPI and Docker deployments.
Handles passphrase retrieval from multiple sources with intelligent fallbacks.
"""

import os
import sys
import getpass
from pathlib import Path
from typing import Optional, Tuple


class PassphraseManager:
    """Unified passphrase handling for all installation types"""
    
    # Standard locations checked in order (works for both PyPI and Docker)
    STANDARD_LOCATIONS = [
        '/run/secrets/backup_passphrase',  # Docker secret (Swarm/Compose)
        '~/.local/share/secret-rotator/.backup-passphrase',  # XDG data dir (PyPI)
        '~/.config/secret-rotator/.backup-passphrase',  # XDG config dir (PyPI)
        '/app/data/.backup-passphrase',  # Docker data volume
    ]
    
    def __init__(self, config_manager=None):
        """
        Initialize passphrase manager.
        
        Args:
            config_manager: Optional settings object for config-based sources
        """
        self.config_manager = config_manager
    
    def get_passphrase(self, 
                       cli_file: Optional[str] = None,
                       allow_interactive: bool = True,
                       purpose: str = "backup encryption") -> Tuple[Optional[str], str]:
        """
        Get passphrase from various sources with priority order.
        
        Priority:
        1. CLI argument (--passphrase-file)
        2. Config file setting
        3. Standard file locations
        4. Environment variable
        5. Stdin (non-interactive)
        6. Interactive prompt (if allowed)
        
        Args:
            cli_file: Explicit passphrase file from CLI argument
            allow_interactive: Whether to allow interactive prompts
            purpose: Description of what the passphrase is for (for prompts)
        
        Returns:
            Tuple of (passphrase, source_description) or (None, reason)
        """
        
        # Priority 1: CLI argument (explicit override)
        if cli_file:
            passphrase = self._read_from_file(cli_file)
            if passphrase:
                return passphrase, f"CLI argument: {cli_file}"
            else:
                return None, f"CLI file not found or empty: {cli_file}"
        
        # Priority 2: Config file setting
        if self.config_manager:
            passphrase, source = self._get_from_config()
            if passphrase:
                return passphrase, source
        
        # Priority 3: Standard locations (convention over configuration)
        for location in self.STANDARD_LOCATIONS:
            expanded_path = os.path.expanduser(location)
            passphrase = self._read_from_file(expanded_path)
            if passphrase:
                return passphrase, f"standard location: {location}"
        
        # Priority 4: Environment variable
        env_passphrase = os.getenv('BACKUP_PASSPHRASE')
        if env_passphrase:
            return env_passphrase, "BACKUP_PASSPHRASE environment variable"
        
        return None, "no_source_available"
    
    def _read_from_file(self, file_path: str) -> Optional[str]:
        """Safely read passphrase from file"""
        try:
            path = Path(file_path)
            if path.exists() and path.is_file():
                with open(path, 'r') as f:
                    content = f.read().strip()
                    return content if content else None
        except (IOError, PermissionError, OSError):
            pass
        return None
    
    def _get_from_config(self) -> Tuple[Optional[str], str]:
        """Get passphrase based on config file settings"""
        return None, ""