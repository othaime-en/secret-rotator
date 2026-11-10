import copy
import yaml
import os
from pathlib import Path
from typing import Any, Dict, Optional

from secret_rotator.config.overrides import OverridesStore

DEFAULT_OVERRIDES_FILE = "data/dashboard-settings.json"


class Settings:
    """Configuration management with support for multiple config locations"""

    CONFIG_SEARCH_PATHS = [
        # 1. Environment variable (highest priority)
        lambda: os.getenv("SECRET_ROTATOR_CONFIG"),
        # 2. Current working directory
        lambda: Path.cwd() / "config" / "config.yaml",
        # 3. User home directory (standard for installed packages)
        lambda: Path.home() / ".config" / "secret-rotator" / "config.yaml",
        # 4. Windows AppData
        lambda: (
            Path(os.getenv("APPDATA", "")) / "secret-rotator" / "config.yaml"
            if os.name == "nt"
            else None
        ),
        # 5. System config directory (Linux/macOS)
        lambda: Path("/etc/secret-rotator/config.yaml"),
        # 6. Package installation directory (for pip-installed package)
        lambda: Settings._get_package_config_path(),
        # 7. Source directory (for development)
        lambda: Path(__file__).parent.parent.parent / "config" / "config.yaml",
    ]

    @staticmethod
    def _get_package_config_path():
        """Get config path from installed package"""
        try:
            # When installed via pip, the package will be in site-packages
            import secret_rotator

            package_dir = Path(secret_rotator.__file__).parent

            # Check if there's a config in the package data
            config_path = package_dir / "config" / "config.yaml"
            if config_path.exists():
                return config_path
        except ImportError:
            # Not installed as package, running from source
            pass
        return None

    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            self.config_path = self._find_config()

        self.base_config: Dict[str, Any] = {}
        self.overrides = OverridesStore(
            path=Path(DEFAULT_OVERRIDES_FILE), base_provider=lambda: self.base_config
        )
        self.config = self.load_config()

    def _overrides_path(self) -> Path:
        """Where the dashboard keeps its own settings (see config/overrides.py):
        $SECRET_ROTATOR_DASHBOARD_SETTINGS, else ``dashboard.settings_file``
        in config.yaml, else data/dashboard-settings.json."""
        configured = os.getenv("SECRET_ROTATOR_DASHBOARD_SETTINGS")
        if not configured:
            section = (
                self.base_config.get("dashboard") if isinstance(self.base_config, dict) else None
            )
            configured = section.get("settings_file") if isinstance(section, dict) else None
        return Path(configured or DEFAULT_OVERRIDES_FILE)

    def _find_config(self) -> Path:
        """Search for config file in standard locations"""
        for path_func in self.CONFIG_SEARCH_PATHS:
            try:
                path = path_func()
                if path and Path(path).exists():
                    print(f"Using config: {path}")
                    return Path(path)
            except Exception:
                continue

        # If no config found, create default in user directory
        user_config = Path.home() / ".config" / "secret-rotator" / "config.yaml"
        user_config.parent.mkdir(parents=True, exist_ok=True)

        # Try to copy example config
        example_locations = [
            Path(__file__).parent.parent.parent / "config" / "config.example.yaml",  # From source
            Path.cwd() / "config" / "config.example.yaml",  # Current dir
        ]

        for example_config in example_locations:
            if example_config.exists():
                import shutil

                shutil.copy(example_config, user_config)
                print(f"Created default config: {user_config}")
                break
        else:
            # Create minimal config if example not found
            self._create_minimal_config(user_config)

        return user_config

    def _create_minimal_config(self, config_path: Path):
        """Create a minimal working config"""
        minimal_config = {
            "rotation": {
                "schedule": "daily",
                "retry_attempts": 3,
                "timeout": 30,
                "backup_old_secrets": True,
            },
            "logging": {"level": "INFO", "file": "logs/rotation.log", "console_enabled": True},
            "web": {"enabled": True, "port": 8080, "host": "localhost"},
            "providers": {
                "file_storage": {
                    "type": "file",
                    "file_path": str(
                        Path.home() / ".local" / "share" / "secret-rotator" / "secrets.json"
                    ),
                }
            },
            "rotators": {
                "password_gen": {
                    "type": "password",
                    "length": 16,
                    "use_symbols": True,
                    "use_numbers": True,
                    "use_uppercase": True,
                    "use_lowercase": True,
                }
            },
            "security": {
                "encryption": {
                    "enabled": True,
                    "master_key_file": str(
                        Path.home() / ".config" / "secret-rotator" / ".master.key"
                    ),
                }
            },
            "backup": {
                "enabled": True,
                "storage_path": str(Path.home() / ".local" / "share" / "secret-rotator" / "backup"),
                "encrypt_backups": True,
            },
            "jobs": [],
        }

        with open(config_path, "w") as f:
            yaml.dump(minimal_config, f, default_flow_style=False)

        print(f"Created minimal config: {config_path}")

    def load_config(self):
        """Load the effective configuration: config.yaml with the
        dashboard's own settings file (config/overrides.py) applied over it.

        ``base_config`` keeps config.yaml's contents exactly as read.
        """
        self.base_config = self._read_yaml()
        self.overrides.path = self._overrides_path()
        self.overrides.load()
        return self.overrides.apply(self.base_config)

    def reload(self):
        """Re-read config.yaml and the dashboard's settings file."""
        self.config = self.load_config()

    def reload_overrides(self):
        """Re-read only the dashboard's settings file and re-apply it over
        the config.yaml contents already in memory.

        Long-lived processes (queue workers) call this before each piece of
        work so they see changes made in the dashboard after they started.
        config.yaml itself is deliberately not re-read: like the web
        process, a worker picks up edits to it on restart.
        """
        self.overrides.load()
        self.config = self.overrides.apply(self.base_config)

    def _read_yaml(self):
        try:
            with open(self.config_path, "r") as file:
                return yaml.safe_load(file) or {}
        except FileNotFoundError:
            print(f"Config file not found at {self.config_path}")
            return {}
        except yaml.YAMLError as e:
            print(f"Error parsing config file: {e}")
            return {}

    def get(self, key, default=None):
        """Get configuration value by key (supports dot notation)"""
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, {})
            else:
                return default
        return value if value != {} else default

    def set(self, key: str, value):
        """Set a value in config.yaml's contents (supports dot notation).

        Used by the command line tools that edit config.yaml, together with
        save(). The dashboard never goes through here; it uses
        ``overrides`` and leaves config.yaml alone.
        """
        keys = key.split(".")
        for target in (self.base_config, self.config):
            node = target
            for k in keys[:-1]:
                node = node.setdefault(k, {})
            node[keys[-1]] = copy.deepcopy(value)

    def save(self):
        """Write config.yaml. Only config.yaml's own contents are written,
        never the dashboard's overrides merged over it."""
        try:
            with open(self.config_path, "w") as file:
                yaml.dump(self.base_config, file, default_flow_style=False)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False


# Global settings instance
settings = Settings()
