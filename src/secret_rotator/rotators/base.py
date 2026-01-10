from abc import ABC, abstractmethod
from typing import Dict, Any


class SecretRotator(ABC):
    """Base class for all secret rotators (how to generate new secrets)"""

    def __init__(self, name: str, config: Dict[str, Any]):
        self.name = name
        self.config = config

    @abstractmethod
    def generate_new_secret(self) -> str:
        """Generate a new secret value"""
        pass

    @abstractmethod
    def validate_secret(self, secret: str) -> bool:
        """Validate if generated secret meets requirements"""
        pass
