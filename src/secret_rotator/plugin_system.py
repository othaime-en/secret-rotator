"""
Plugin system for extensible secret rotation.
Allows users to easily add custom providers, rotators, and notifiers.
"""

import importlib
import inspect
from pathlib import Path
from typing import Dict, Type, List, Any
from secret_rotator.utils.logger import logger


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
            "validators": list(self.validators.keys()),
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
                        plugin_name = getattr(obj, "plugin_name", name.lower())

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

    def _is_valid_plugin(self, obj: Type, plugin_type: str) -> bool:
        """Check if a class is a valid plugin"""
        # Avoid registering base classes
        if inspect.isabstract(obj):
            return False

        # Check if it inherits from the appropriate base class
        from secret_rotator.providers.base import SecretProvider
        from secret_rotator.rotators.base import SecretRotator

        if plugin_type == "providers":
            return issubclass(obj, SecretProvider) and obj != SecretProvider
        elif plugin_type == "rotators":
            return issubclass(obj, SecretRotator) and obj != SecretRotator

        # For notifiers and validators, just check if they have required methods
        return True

    def _create_example_plugin(self):
        """Create an example plugin file to help users get started"""
        example_provider = '''"""
    Example custom secret provider plugin.
    Copy this file and modify it to create your own provider.
    """
    from secret_rotator.providers.base import SecretProvider
    from typing import Dict, Any

    class CustomDatabaseProvider(SecretProvider):
        """Example: Store secrets in a custom database"""

        plugin_name = "custom_db"  # This is how you'll reference it in config

        def __init__(self, name: str, config: Dict[str, Any]):
            super().__init__(name, config)
            self.db_host = config.get('host', 'localhost')
            self.db_port = config.get('port', 5432)
            # Initialize your database connection here

        def get_secret(self, secret_id: str) -> str:
            """Retrieve secret from your database"""
            # Implement your logic here
            pass

        def update_secret(self, secret_id: str, new_value: str) -> bool:
            """Update secret in your database"""
            # Implement your logic here
            pass

        def validate_connection(self) -> bool:
            """Test database connection"""
            # Implement your logic here
            pass
    '''

        example_file = self.plugins_dir / "providers" / "example_custom_provider.py.example"
        example_file.parent.mkdir(parents=True, exist_ok=True)

        with open(example_file, "w") as f:
            f.write(example_provider)

        logger.info(f"Created example plugin at {example_file}")


class PluginMetadata:
    """Metadata for plugin documentation and validation"""

    def __init__(
        self,
        name: str,
        version: str,
        author: str,
        description: str,
        required_config: List[str],
        optional_config: Dict[str, Any],
    ):
        self.name = name
        self.version = version
        self.author = author
        self.description = description
        self.required_config = required_config
        self.optional_config = optional_config

    def validate_config(self, config: Dict[str, Any]) -> tuple[bool, List[str]]:
        """Validate plugin configuration"""
        missing = []
        for key in self.required_config:
            if key not in config:
                missing.append(key)

        if missing:
            return False, missing
        return True, []


# Decorators for easy plugin registration
def register_provider(name: str):
    """Decorator to register a provider plugin"""

    def decorator(cls):
        cls.plugin_name = name
        return cls

    return decorator


def register_rotator(name: str):
    """Decorator to register a rotator plugin"""

    def decorator(cls):
        cls.plugin_name = name
        return cls

    return decorator


# Global plugin registry instance
plugin_registry = PluginRegistry()
