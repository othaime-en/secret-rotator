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
        
        # Priority 5: Stdin (for piping)
        if not sys.stdin.isatty():
            try:
                passphrase = sys.stdin.readline().strip()
                if passphrase:
                    return passphrase, "stdin"
            except Exception:
                pass
            
            # If we're non-interactive and nothing worked, fail with helpful message
            return None, "non_interactive_no_source"
        
        # Priority 6: Interactive prompt
        if allow_interactive:
            return None, "interactive_required"
        
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
        source = self.config_manager.get('backup.key_backup.passphrase_source')
        if not source:
            return None, ""
        
        # Config specifies a file
        if source.startswith('file:'):
            file_path = source.replace('file:', '', 1)
            file_path = os.path.expanduser(file_path)
            passphrase = self._read_from_file(file_path)
            if passphrase:
                return passphrase, f"config file source: {file_path}"
        
        # Config specifies an environment variable
        elif source.startswith('env:'):
            env_var = source.replace('env:', '', 1)
            passphrase = os.getenv(env_var)
            if passphrase:
                return passphrase, f"config env variable: {env_var}"
        
        return None, ""
    
    def prompt_interactive(self, 
                          purpose: str = "backup encryption",
                          min_length: int = 20,
                          require_confirmation: bool = True) -> str:
        """
        Interactively prompt for passphrase with validation.
        
        Args:
            purpose: What the passphrase is for
            min_length: Minimum required length
            require_confirmation: Whether to ask for confirmation
        
        Returns:
            The entered passphrase
        
        Raises:
            KeyboardInterrupt: If user cancels
        """
        print(f"\nEnter passphrase for {purpose}")
        print(f"Minimum length: {min_length} characters")
        print()
        
        while True:
            passphrase = getpass.getpass("Enter passphrase: ")
            
            if not passphrase:
                print("ERROR: Passphrase cannot be empty\n")
                continue
            
            if len(passphrase) < min_length:
                print(f"⚠️  WARNING: Passphrase is only {len(passphrase)} characters "
                      f"(recommended: {min_length}+)")
                response = input("Continue anyway? (yes/no): ")
                if response.lower() != "yes":
                    continue
            
            if require_confirmation:
                passphrase_confirm = getpass.getpass("Confirm passphrase: ")
                if passphrase != passphrase_confirm:
                    print("ERROR: Passphrases do not match. Try again.\n")
                    continue
            
            return passphrase
    
    def print_help_message(self, is_docker: Optional[bool] = None):
        """
        Print helpful error message with platform-specific instructions.
        
        Args:
            is_docker: True for Docker, False for PyPI, None for auto-detect
        """
        if is_docker is None:
            # Auto-detect: check if we're in Docker
            is_docker = os.path.exists('/.dockerenv') or os.path.exists('/run/secrets')
        
        print("\n" + "=" * 70, file=sys.stderr)
        print("ERROR: No passphrase available (non-interactive mode)", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        print("\nProvide passphrase using one of these methods:\n", file=sys.stderr)
        
        print("1. Create passphrase file (RECOMMENDED):", file=sys.stderr)
        if is_docker:
            print("   # On host machine:", file=sys.stderr)
            print("   mkdir -p secrets", file=sys.stderr)
            print("   echo 'your-passphrase' > secrets/backup_passphrase.txt", file=sys.stderr)
            print("   chmod 600 secrets/backup_passphrase.txt", file=sys.stderr)
            print("   # Then configure docker-compose.yml to mount as secret", file=sys.stderr)
            print("   # OR:", file=sys.stderr)
            print("   docker exec secret-rotator bash -c \\", file=sys.stderr)
            print("     'echo \"passphrase\" > /app/data/.backup-passphrase'", file=sys.stderr)
            print("   docker exec secret-rotator chmod 600 /app/data/.backup-passphrase", file=sys.stderr)
        else:
            print("   mkdir -p ~/.config/secret-rotator", file=sys.stderr)
            print("   echo 'your-passphrase' > ~/.config/secret-rotator/.backup-passphrase", file=sys.stderr)
            print("   chmod 600 ~/.config/secret-rotator/.backup-passphrase", file=sys.stderr)
        print()
        
        print("2. Use CLI argument:", file=sys.stderr)
        print("   secret-rotator-backup create-encrypted --passphrase-file /path/to/file", file=sys.stderr)
        print()
        
        print("3. Use environment variable:", file=sys.stderr)
        print("   export BACKUP_PASSPHRASE='your-passphrase'", file=sys.stderr)
        print("   secret-rotator-backup create-encrypted", file=sys.stderr)
        print()
        
        print("4. Pipe from stdin:", file=sys.stderr)
        print("   echo 'your-passphrase' | secret-rotator-backup create-encrypted", file=sys.stderr)
        print()
        
        print("5. Interactive mode (requires TTY):", file=sys.stderr)
        if is_docker:
            print("   docker exec -it secret-rotator secret-rotator-backup create-encrypted", file=sys.stderr)
        else:
            print("   secret-rotator-backup create-encrypted", file=sys.stderr)
        
        print("=" * 70, file=sys.stderr)
    
    def create_passphrase_file(self, 
                               file_path: str,
                               passphrase: Optional[str] = None,
                               interactive: bool = True) -> bool:
        """
        Create a passphrase file with proper permissions.
        
        Args:
            file_path: Where to create the file
            passphrase: Passphrase to write (if None, will prompt if interactive)
            interactive: Whether to prompt if passphrase not provided
        
        Returns:
            True if successful, False otherwise
        """
        if not passphrase and interactive:
            try:
                passphrase = self.prompt_interactive(
                    purpose="storage in file",
                    require_confirmation=True
                )
            except KeyboardInterrupt:
                print("\nCancelled by user")
                return False
        
        if not passphrase:
            return False
        
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write passphrase
            with open(path, 'w') as f:
                f.write(passphrase)
            
            # Set restrictive permissions
            os.chmod(path, 0o600)
            
            print(f"✓ Passphrase saved to: {file_path}")
            print(f"  Permissions: 600 (owner read/write only)")
            return True
            
        except (IOError, OSError) as e:
            print(f"ERROR: Failed to create passphrase file: {e}", file=sys.stderr)
            return False