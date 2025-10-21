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


class PluginLoader:
    """Load plugins from the plugins directory"""
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.registry = PluginRegistry()
    
    def discover_and_load_plugins(self):
        """Automatically discover and load all plugins"""
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
            self._create_example_plugin()
            return
        
        # Load providers
        self._load_plugins_from_dir(self.plugins_dir / "providers", "providers")
        
        # Load rotators
        self._load_plugins_from_dir(self.plugins_dir / "rotators", "rotators")
        
        # Load notifiers
        self._load_plugins_from_dir(self.plugins_dir / "notifiers", "notifiers")
        
        # Load validators
        self._load_plugins_from_dir(self.plugins_dir / "validators", "validators")
        
        logger.info("Plugin discovery complete")
    
    def _load_plugins_from_dir(self, plugin_dir: Path, plugin_type: str):
        """Load all plugins from a specific directory"""
        if not plugin_dir.exists():
            plugin_dir.mkdir(parents=True, exist_ok=True)
            return
        
        for plugin_file in plugin_dir.glob("*.py"):
            if plugin_file.name.startswith("_"):
                continue
            
            try:
                module_name = f"plugins.{plugin_type}.{plugin_file.stem}"
                module = importlib.import_module(module_name)
                
                # Find all classes in the module that inherit from the base class
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if self._is_valid_plugin(obj, plugin_type):
                        plugin_name = getattr(obj, 'plugin_name', name.lower())
                        
                        if plugin_type == "providers":
                            self.registry.register_provider(plugin_name, obj)
                        elif plugin_type == "rotators":
                            self.registry.register_rotator(plugin_name, obj)
                        elif plugin_type == "notifiers":
                            self.registry.register_notifier(plugin_name, obj)
                        elif plugin_type == "validators":
                            self.registry.register_validator(plugin_name, obj)
                
            except Exception as e:
                logger.error(f"Failed to load plugin {plugin_file}: {e}")