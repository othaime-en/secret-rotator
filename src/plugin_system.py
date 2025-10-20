"""
Plugin system for extensible secret rotation.
Allows users to easily add custom providers, rotators, and notifiers.
"""
import importlib
import inspect
from pathlib import Path
from typing import Dict, Type, List, Any
from abc import ABC
from utils.logger import logger


class PluginRegistry:
    """Central registry for all plugins"""
    
    def __init__(self):
        self.providers: Dict[str, Type] = {}
        self.rotators: Dict[str, Type] = {}
        self.notifiers: Dict[str, Type] = {}
        self.validators: Dict[str, Type] = {}
    
    def register_provider(self, name: str, provider_class: Type):
        """Register a secret provider plugin"""
        self.providers[name] = provider_class
        logger.info(f"Registered provider plugin: {name}")
    
    def register_rotator(self, name: str, rotator_class: Type):
        """Register a secret rotator plugin"""
        self.rotators[name] = rotator_class
        logger.info(f"Registered rotator plugin: {name}")
    
    def register_notifier(self, name: str, notifier_class: Type):
        """Register a notifier plugin"""
        self.notifiers[name] = notifier_class
        logger.info(f"Registered notifier plugin: {name}")
    
    def register_validator(self, name: str, validator_class: Type):
        """Register a secret validator plugin"""
        self.validators[name] = validator_class
        logger.info(f"Registered validator plugin: {name}")
    
    def get_provider(self, name: str) -> Type:
        """Get provider class by name"""
        return self.providers.get(name)
    
    def get_rotator(self, name: str) -> Type:
        """Get rotator class by name"""
        return self.rotators.get(name)
    
    def get_notifier(self, name: str) -> Type:
        """Get notifier class by name"""
        return self.notifiers.get(name)
    
    def list_available_plugins(self) -> Dict[str, List[str]]:
        """List all available plugins"""
        return {
            "providers": list(self.providers.keys()),
            "rotators": list(self.rotators.keys()),
            "notifiers": list(self.notifiers.keys()),
            "validators": list(self.validators.keys())
        }