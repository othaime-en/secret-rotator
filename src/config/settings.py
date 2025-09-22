import yaml
import os
from pathlib import Path

class Settings:
    def __init__(self):
        self.config_path = Path(__file__).parent.parent.parent / "config" / "config.yaml"
        self.config = self.load_config()
    
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
        """Get configuration value by key"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            value = value.get(k, {})
            if not isinstance(value, dict) and k != keys[-1]:
                return default
        return value if value != {} else default

# Global settings instance
settings = Settings()