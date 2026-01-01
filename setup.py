#!/usr/bin/env python3
"""Interactive setup wizard for Secret Rotation System"""
import yaml
from pathlib import Path
from getpass import getpass

def setup_wizard():
    print("="*60)
    print("SECRET ROTATION SYSTEM - SETUP WIZARD")
    print("="*60)
    
    config = {}
    
    # Rotation schedule
    print("\n1. Rotation Schedule")
    schedule = input("  How often? (daily/weekly/every_30_minutes): ") or "daily"
    config['rotation'] = {
        'schedule': schedule,
        'retry_attempts': 3,
        'timeout': 30,
        'backup_old_secrets': True
    }
    
    # Logging
    config['logging'] = {
        'level': 'INFO',
        'file': 'logs/rotation.log',
        'max_file_size': '10MB',
        'backup_count': 5
    }
    
    # Web interface
    print("\n2. Web Interface")
    web_enabled = input("  Enable web interface? (yes/no): ").lower() == 'yes'
    port = input("  Port (8080): ") or "8080"
    config['web'] = {
        'enabled': web_enabled,
        'port': int(port),
        'host': 'localhost'
    }
    
    # Providers
    print("\n3. Secret Storage")
    config['providers'] = {
        'file_storage': {
            'type': 'file',
            'file_path': 'data/secrets.json',
            'backup_path': 'data/backup/'
        }
    }
    
    # Security
    print("\n4. Security")
    enable_encryption = input("  Enable encryption? (yes/no): ").lower() == 'yes'
    config['security'] = {
        'encryption': {
            'enabled': enable_encryption,
            'master_key_file': 'config/.master.key',
            'rotate_master_key_days': 90
        }
    }
    
    # Backup
    config['backup'] = {
        'enabled': True,
        'storage_path': 'data/backup/',
        'encrypt_backups': enable_encryption,
        'verification_time': '04:00',
        'verify_integrity': True,
        'retention': {
            'days': 90,
            'max_backups_per_secret': 10
        }
    }
    
    # Save config
    config_path = Path('config/config2.yaml')
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print("\n" + "="*60)
    print("✓ Configuration saved to config/config.yaml")
    print("="*60)
    print("\nNext steps:")
    print("  1. Review and edit config/config.yaml")
    print("  2. Add rotation jobs to the 'jobs' section")
    print("  3. Run: python src/main.py")
    
if __name__ == '__main__':
    setup_wizard()