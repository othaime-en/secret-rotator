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

if __name__ == '__main__':
    pass