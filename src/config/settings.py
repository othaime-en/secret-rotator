import yaml
import os
from pathlib import Path
from typing import Optional

class Settings:
    """Configuration management with support for multiple config locations"""
    
    CONFIG_SEARCH_PATHS = [
        # 1. Environment variable (highest priority)
        lambda: os.getenv('SECRET_ROTATOR_CONFIG'),
        
        # 2. Current working directory
        lambda: Path.cwd() / "config" / "config.yaml",
        
        # 3. User home directory (standard for installed packages)
        lambda: Path.home() / ".config" / "secret-rotator" / "config.yaml",
        
        # 4. Windows AppData
        lambda: Path(os.getenv('APPDATA', '')) / "secret-rotator" / "config.yaml" if os.name == 'nt' else None,
        
        # 5. System config directory (Linux/macOS)
        lambda: Path("/etc/secret-rotator/config.yaml"),
        
        # 6. Package installation directory (for pip-installed package)
        lambda: Settings._get_package_config_path(),
        
        # 7. Source directory (for development) - lowest priority
        lambda: Path(__file__).parent.parent.parent / "config" / "config.yaml",
    ]
    
    @staticmethod
    def _get_package_config_path():
        """Get config path from installed package"""
        try:
            import secret_rotator
            package_dir = Path(secret_rotator.__file__).parent
            config_path = package_dir / "config" / "config.yaml"
            if config_path.exists():
                return config_path
        except ImportError:
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
        
        # Fallback: use source directory (will be handled in next major change)
        default_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        print(f"No config found, using default: {default_path}")
        return default_path
    
    def load_config(self):
        """Load configuration from YAML file"""
        try:
            with open(self.config_path, 'r') as file:
                return yaml.safe_load(file)
        except FileNotFoundError:
            print(f"Config file not found at {self.config_path}")
            return {}
        except yaml.YAMLError as e:
            print(f"Error parsing config file: {e}")
            return {}
    
    def get(self, key, default=None):
        """Get configuration value by key (supports dot notation)"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            value = value.get(k, {})
            if not isinstance(value, dict) and k != keys[-1]:
                return default
        return value if value != {} else default
    
    def set(self, key: str, value):
        """Set configuration value by key (supports dot notation)"""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            config = config.setdefault(k, {})
        config[keys[-1]] = value
    
    def save(self):
        """Save configuration back to file"""
        try:
            with open(self.config_path, 'w') as file:
                yaml.dump(self.config, file, default_flow_style=False)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False

# Global settings instance
settings = Settings()