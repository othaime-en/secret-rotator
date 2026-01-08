import yaml
import os
from pathlib import Path
from typing import Optional

class Settings:
    """Configuration management with support for multiple config locations"""
    
    # Search paths for config file (in order of priority)
    CONFIG_SEARCH_PATHS = [
        # 1. Environment variable
        lambda: os.getenv('SECRET_ROTATOR_CONFIG'),
        # 2. Current directory
        lambda: "config/config.yaml",
        # 3. User home directory
        lambda: Path.home() / ".config" / "secret-rotator" / "config.yaml",
        # 4. System config directory (Linux)
        lambda: "/etc/secret-rotator/config.yaml",
        # 5. Application directory
        lambda: Path(__file__).parent.parent.parent / "config" / "config.yaml",
    ]
    
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
        
        # If no config found, use default location
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