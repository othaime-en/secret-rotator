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
import getpass
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from key_backup_manager import MasterKeyBackupManager
from utils.logger import logger


def create_encrypted_backup(args):
    """Create an encrypted backup of the master key"""
    manager = MasterKeyBackupManager(
        master_key_file=args.key_file,
        backup_dir=args.backup_dir
    )
    
    print("\n" + "="*70)
    print("CREATE ENCRYPTED MASTER KEY BACKUP")
    print("="*70)
    print("\nThis will create an encrypted backup of your master encryption key.")
    print("You will be prompted to enter a strong passphrase.")
    print("\nIMPORTANT:")
    print("  - Use a passphrase with 20+ characters")
    print("  - Include uppercase, lowercase, numbers, and symbols")
    print("  - Store the passphrase in a secure password manager")
    print("  - Without this passphrase, the backup CANNOT be recovered")
    print()
    
    # Get passphrase
    while True:
        passphrase = getpass.getpass("Enter passphrase: ")
        passphrase_confirm = getpass.getpass("Confirm passphrase: ")
        
        if passphrase != passphrase_confirm:
            print("ERROR: Passphrases do not match. Try again.\n")
            continue
        
        if len(passphrase) < 20:
            print("WARNING: Passphrase should be at least 20 characters.")
            response = input("Continue anyway? (yes/no): ")
            if response.lower() != 'yes':
                continue
        
        break
    
    try:
        backup_file = manager.create_encrypted_key_backup(
            passphrase=passphrase,
            backup_name=args.name
        )
        
        print(f"\n✓ SUCCESS: Encrypted backup created")
        print(f"  Location: {backup_file}")
        print(f"\nNext steps:")
        print(f"  1. Store the passphrase in a secure password manager")
        print(f"  2. Copy the backup file to a secure remote location")
        print(f"  3. Test restoration: python {sys.argv[0]} verify {backup_file}")
        
    except Exception as e:
        print(f"\n✗ ERROR: Failed to create backup: {e}")
        sys.exit(1)


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
    
    # Create encrypted backup
    parser_encrypted = subparsers.add_parser(
        'create-encrypted',
        help='Create encrypted backup with passphrase'
    )
    parser_encrypted.add_argument('--name', help='Optional backup name')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Execute command
    commands = {
        'create-encrypted': create_encrypted_backup,
    }
    
    commands[args.command](args)


if __name__ == '__main__':
    main()