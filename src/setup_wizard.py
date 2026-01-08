#!/usr/bin/env python3
"""
Interactive setup wizard for Secret Rotation System
This creates all necessary directories and configuration
"""
import os
import sys
import yaml
import shutil
from pathlib import Path
from getpass import getpass

def get_config_dir():
    """Get platform-specific config directory"""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', os.path.expanduser('~'))
        return Path(base) / 'secret-rotator'
    else:
        # Unix-like: use XDG Base Directory spec
        xdg_config = os.environ.get('XDG_CONFIG_HOME')
        if xdg_config:
            return Path(xdg_config) / 'secret-rotator'
        return Path.home() / '.config' / 'secret-rotator'

def get_data_dir():
    """Get platform-specific data directory"""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        return Path(base) / 'secret-rotator' / 'data'
    else:
        xdg_data = os.environ.get('XDG_DATA_HOME')
        if xdg_data:
            return Path(xdg_data) / 'secret-rotator'
        return Path.home() / '.local' / 'share' / 'secret-rotator'

def get_log_dir():
    """Get platform-specific log directory"""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
        return Path(base) / 'secret-rotator' / 'logs'
    else:
        xdg_state = os.environ.get('XDG_STATE_HOME')
        if xdg_state:
            return Path(xdg_state) / 'secret-rotator' / 'logs'
        return Path.home() / '.local' / 'state' / 'secret-rotator' / 'logs'

def create_directories(config_dir, data_dir, log_dir):
    """Create necessary directories"""
    print("\n📁 Creating directories...")
    
    directories = [
        config_dir,
        data_dir,
        data_dir / 'backup',
        log_dir
    ]
    
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)  # Restrictive permissions
        print(f"  ✓ {directory}")

def create_config(config_dir, data_dir, log_dir):
    """Create default configuration"""
    config_file = config_dir / 'config.yaml'
    
    if config_file.exists():
        response = input(f"\n⚠️  Configuration already exists at {config_file}\n   Overwrite? (yes/no): ")
        if response.lower() != 'yes':
            print("Keeping existing configuration")
            return config_file
    
    print("\n📝 Creating configuration...")
    
    # Interactive configuration
    print("\nRotation Schedule:")
    print("  1. Daily (recommended)")
    print("  2. Weekly")
    print("  3. Every 12 hours")
    print("  4. Custom")
    
    choice = input("Select schedule [1]: ").strip() or "1"
    schedule_map = {
        "1": "daily",
        "2": "weekly",
        "3": "every_12_hours",
        "4": input("  Enter custom schedule (e.g., every_30_minutes): ")
    }
    schedule = schedule_map.get(choice, "daily")
    
    # Create and write configuration
    config = {}
    
    with open(config_file, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    os.chmod(config_file, 0o600)  # Restrictive permissions
    print(f"  ✓ Configuration saved to {config_file}")
    
    return config_file

if __name__ == '__main__':
    pass