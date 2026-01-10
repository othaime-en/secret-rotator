from abc import ABC, abstractmethod
from typing import Dict, Any


class SecretProvider(ABC):
    """Base class for all secret providers (where secrets are stored)"""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config

    @abstractmethod
    def get_secret(self, secret_id: str) -> str:
        """Retrieve a secret value"""
        pass

    @abstractmethod
    def update_secret(self, secret_id: str, new_value: str) -> bool:
        """Update a secret with new value"""
        pass

    @abstractmethod
    def validate_connection(self) -> bool:
        """Test if connection to provider is working"""
        pass
