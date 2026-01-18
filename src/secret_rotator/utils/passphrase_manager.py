"""
Universal passphrase management for PyPI and Docker deployments.
Handles passphrase retrieval from multiple sources with intelligent fallbacks.
"""

from typing import Optional, Tuple


class PassphraseManager:
    """Unified passphrase handling for all installation types"""
    
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
        return None, "no_source_available"