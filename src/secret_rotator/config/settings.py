import yaml
import os
from pathlib import Path
from typing import Optional


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

        self.config = self.load_config()

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
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, "r") as file:
                return yaml.safe_load(file)
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
        """Set configuration value by key (supports dot notation)"""
        keys = key.split(".")
        config = self.config
        for k in keys[:-1]:
            config = config.setdefault(k, {})
        config[keys[-1]] = value

    def save(self):
        """Save configuration back to file"""
        try:
            with open(self.config_path, "w") as file:
                yaml.dump(self.config, file, default_flow_style=False)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False


# Global settings instance
settings = Settings()
