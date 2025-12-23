#!/usr/bin/env python3
"""
Master Key Backup Management CLI Tool

Usage:
    python tools/manage_key_backups.py create-encrypted
    python tools/manage_key_backups.py create-split --shares 5 --threshold 3
    python tools/manage_key_backups.py list
    python tools/manage_key_backups.py verify backup.enc
    python tools/manage_key_backups.py restore backup.enc
"""
import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from key_backup_manager import MasterKeyBackupManager
from utils.logger import logger


def main():
    parser = argparse.ArgumentParser(
        description="Manage master encryption key backups",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        '--key-file',
        default='config/.master.key',
        help='Path to master key file (default: config/.master.key)'
    )
    
    parser.add_argument(
        '--backup-dir',
        default='config/key_backups',
        help='Backup directory (default: config/key_backups)'
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()