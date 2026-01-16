#!/usr/bin/env python3
"""
Master Key Backup Management CLI Tool

Usage:
    secret-rotator-backup create-encrypted
    secret-rotator-backup create-split --shares 5 --threshold 3
    secret-rotator-backup list
    secret-rotator-backup verify backup.enc
    secret-rotator-backup restore backup.enc
    secret-rotator-backup restore-split share1.share share2.share share3.share
    secret-rotator-backup export-instructions --output KEY_BACKUP_INSTRUCTIONS.txt
"""
import sys
import argparse
import getpass
from pathlib import Path

from secret_rotator.key_backup_manager import MasterKeyBackupManager


def create_encrypted_backup(args):
    """Create an encrypted backup of the master key"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("CREATE ENCRYPTED MASTER KEY BACKUP")
    print("=" * 70)
    print("\nThis will create an encrypted backup of your master encryption key.")
    print("You will be prompted to enter a strong passphrase.")
    print("\nIMPORTANT:")
    print("  - Use a passphrase with 20+ characters")
    print("  - Include uppercase, lowercase, numbers, and symbols")
    print("  - Store the passphrase in a secure password manager")
    print("  - Without this passphrase, the backup CANNOT be recovered")
    print("\nBEST PRACTICE:")
    print("  - After creating the backup, copy the .enc file to external storage")
    print("  - Examples: AWS S3, Azure Blob, Google Drive, encrypted USB drive")
    print("  - The encrypted file is safe to store in cloud storage")
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
            if response.lower() != "yes":
                continue

        break

    try:
        backup_file = manager.create_encrypted_key_backup(
            passphrase=passphrase, backup_name=args.name
        )

        print("\n✓ SUCCESS: Encrypted backup created")
        print(f"  Location: {backup_file}")
        print("\nNext steps:")
        print("  1. Store the passphrase in a secure password manager")
        print("  2. Copy the backup file to external storage:")
        print(f"     - For Docker: docker cp secret-rotator:{backup_file} ./external-backup/")
        print(f"     - For local: cp {backup_file} /path/to/external/storage/")
        print("  3. Test restoration in non-production environment:")
        print(f"     secret-rotator-backup verify {backup_file}")
        print("\n  Recommended external storage options:")
        print("     - AWS S3 bucket with encryption")
        print("     - Azure Blob Storage with encryption")
        print("     - Encrypted USB drive in physical safe")
        print("     - Password manager with file attachments (1Password, LastPass)")

    except Exception as e:
        print(f"\n✗ ERROR: Failed to create backup: {e}")
        sys.exit(1)


def create_split_backup(args):
    """Create a split key backup using Shamir's Secret Sharing"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("CREATE SPLIT KEY BACKUP (Shamir's Secret Sharing)")
    print("=" * 70)
    print(f"\nThis will split your master key into {args.shares} shares.")
    print(f"Any {args.threshold} of these shares can reconstruct the key.")
    print("\nIMPORTANT:")
    print(f"  - Distribute the {args.shares} shares to different secure locations")
    print("  - No single location will have the complete key")
    print(f"  - You need {args.threshold} shares to recover the key")
    print("\nRECOMMENDED DISTRIBUTION:")
    print("  - Different physical safes in different buildings")
    print("  - Different cloud storage accounts (different providers)")
    print("  - With trusted individuals in different geographic locations")
    print("  - Different AWS regions / Azure regions")
    print()

    response = input(f"Create {args.shares} shares (threshold {args.threshold})? (yes/no): ")
    if response.lower() != "yes":
        print("Cancelled.")
        return

    try:
        share_files = manager.create_split_key_backup(
            num_shares=args.shares, threshold=args.threshold
        )

        print(f"\n✓ SUCCESS: Created {len(share_files)} key shares")
        print("\nShare files:")
        for i, share_file in enumerate(share_files, 1):
            print(f"  {i}. {share_file}")

        print("\nNext steps:")
        print(f"  1. Distribute shares to {args.shares} different secure locations:")
        print("     Example distribution strategy:")
        print("       • Share 1: Company safe (headquarters)")
        print("       • Share 2: Backup facility (different city)")
        print("       • Share 3: CEO's personal safe")
        print("       • Share 4: CTO's personal safe")
        print("       • Share 5: Cloud storage (AWS S3, encrypted)")
        print("  2. Document who has each share (but keep this document secure)")
        print(f"  3. Test restoration with {args.threshold} shares:")
        print(f"     secret-rotator-backup restore-split share1.share share2.share share3.share")
        print("\n  For Docker deployments, copy shares out of container:")
        for i, share_file in enumerate(share_files, 1):
            print(f"     docker cp secret-rotator:{share_file} ./share{i}/")

    except Exception as e:
        print(f"\n✗ ERROR: Failed to create split backup: {e}")
        sys.exit(1)


def create_plaintext_backup(args):
    """Create a plaintext backup (for immediate physical storage)"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("CREATE PLAINTEXT MASTER KEY BACKUP")
    print("=" * 70)
    print("\n⚠️  WARNING: This creates an UNENCRYPTED backup!")
    print("\nThis backup type should ONLY be used if:")
    print("  - You will immediately store it in a physical safe/vault")
    print("  - You cannot use encrypted or split-key backups")
    print("\nConsider using encrypted or split-key backups instead.")
    print()

    response = input("Are you sure you want to create an unencrypted backup? (yes/no): ")
    if response.lower() != "yes":
        print("Cancelled. Consider using: create-encrypted or create-split")
        return

    try:
        backup_file = manager.create_plaintext_backup(backup_name=args.name)

        print("\n✓ SUCCESS: Plaintext backup created")
        print(f"  Location: {backup_file}")
        print("\n⚠️  CRITICAL: This file is UNENCRYPTED!")
        print("  Store it in a physically secure location immediately!")
        print("  Examples: Bank safe deposit box, home safe, secure vault")

    except Exception as e:
        print(f"\n✗ ERROR: Failed to create backup: {e}")
        sys.exit(1)


def list_backups(args):
    """List all available backups"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("AVAILABLE MASTER KEY BACKUPS")
    print("=" * 70)
    print(f"Backup directory: {args.backup_dir}")

    backups = manager.list_backups()

    if not backups:
        print("\nNo backups found.")
        print("\nCreate a backup with one of these commands:")
        print("  secret-rotator-backup create-encrypted")
        print("  secret-rotator-backup create-split --shares 5 --threshold 3")
        return

    for i, backup in enumerate(backups, 1):
        print(f"\n{i}. {backup['type'].upper()} BACKUP")
        print(f"   Created: {backup.get('created_at', 'unknown')}")

        if backup["type"] == "encrypted":
            print(f"   File: {backup['file']}")
            print(f"   Key ID: {backup.get('key_id', 'unknown')}")
            print(f"   Status: {backup['status']}")
            print("   ✓ Safe to copy to external storage")

        elif backup["type"] == "split_key":
            print(f"   Threshold: {backup['threshold']} of {backup['total_shares']} shares")
            print(f"   Available shares: {backup['available_shares']}")
            print(f"   Key ID: {backup.get('key_id', 'unknown')}")
            print(f"   Status: {backup['status']}")
            if backup["status"] == "incomplete":
                print(
                    f"   ⚠️  WARNING: Need {backup['threshold']} shares to restore, only have {backup['available_shares']}"
                )
            print("   Files:")
            for share in backup.get("shares", []):
                print(f"     - {share}")

        elif backup["type"] == "plaintext":
            print(f"   File: {backup['file']}")
            print(f"   ⚠️  WARNING: {backup.get('warning', 'unencrypted')}")


def verify_backup(args):
    """Verify a backup can be restored"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("VERIFY BACKUP")
    print("=" * 70)
    print(f"\nVerifying: {args.backup_file}")

    # Check if encrypted backup
    if args.backup_file.endswith(".enc"):
        passphrase = getpass.getpass("\nEnter passphrase: ")

        try:
            success = manager.verify_backup(args.backup_file, passphrase)

            if success:
                print("\n✓ SUCCESS: Backup is valid and can be restored")
                print("\nThis backup has been verified:")
                print("  - File integrity: OK")
                print("  - Passphrase: Correct")
                print("  - Decryption: Successful")
                print("  - Key structure: Valid")
            else:
                print("\n✗ ERROR: Backup verification failed")
                sys.exit(1)

        except Exception as e:
            print(f"\n✗ ERROR: Verification failed: {e}")
            sys.exit(1)
    else:
        try:
            success = manager.verify_backup(args.backup_file)

            if success:
                print("\n✓ SUCCESS: Backup is valid")
            else:
                print("\n✗ ERROR: Backup verification failed")
                sys.exit(1)

        except Exception as e:
            print(f"\n✗ ERROR: Verification failed: {e}")
            sys.exit(1)


def restore_backup(args):
    """Restore master key from backup"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("RESTORE MASTER KEY FROM BACKUP")
    print("=" * 70)
    print(f"\nBackup file: {args.backup_file}")
    print("\n⚠️  WARNING: This will replace your current master key!")
    print("The current key will be backed up before restoration.")

    response = input("\nContinue with restoration? (yes/no): ")
    if response.lower() != "yes":
        print("Cancelled.")
        return

    # Check if encrypted backup
    if args.backup_file.endswith(".enc"):
        passphrase = getpass.getpass("\nEnter passphrase: ")

        try:
            success = manager.restore_from_encrypted_backup(args.backup_file, passphrase)

            if success:
                print("\n✓ SUCCESS: Master key restored from backup")
                print("\nNext steps:")
                print("  1. Restart the secret rotation application")
                print("  2. Verify the application can decrypt existing secrets")
                print("  3. Check application logs for any issues")
                print("\nFor Docker:")
                print("  docker-compose restart secret-rotator")
            else:
                print("\n✗ ERROR: Restoration failed")
                sys.exit(1)

        except Exception as e:
            print(f"\n✗ ERROR: Restoration failed: {e}")
            sys.exit(1)
    else:
        print("\n✗ ERROR: Only encrypted backups supported for restoration via CLI")
        print("For plaintext backups, manually copy the file to replace the master key.")
        sys.exit(1)


def restore_split_backup(args):
    """Restore master key from split key shares"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    print("\n" + "=" * 70)
    print("RESTORE FROM SPLIT KEY SHARES")
    print("=" * 70)
    print(f"\nShare files provided: {len(args.share_files)}")

    # Verify all share files exist
    for share_file in args.share_files:
        if not Path(share_file).exists():
            print(f"\n✗ ERROR: Share file not found: {share_file}")
            sys.exit(1)

    print("\n⚠️  WARNING: This will replace your current master key!")

    response = input("\nContinue with restoration? (yes/no): ")
    if response.lower() != "yes":
        print("Cancelled.")
        return

    try:
        success = manager.restore_from_split_key(args.share_files)

        if success:
            print("\n✓ SUCCESS: Master key restored from shares")
            print("\nNext steps:")
            print("  1. Restart the secret rotation application")
            print("  2. Verify the application can decrypt existing secrets")
            print("\nFor Docker:")
            print("  docker-compose restart secret-rotator")
        else:
            print("\n✗ ERROR: Restoration failed")
            sys.exit(1)

    except Exception as e:
        print(f"\n✗ ERROR: Restoration failed: {e}")
        sys.exit(1)


def export_instructions(args):
    """Export backup instructions document"""
    manager = MasterKeyBackupManager(master_key_file=args.key_file, backup_dir=args.backup_dir)

    try:
        output_file = manager.export_backup_instructions(args.output)
        print(f"\n✓ SUCCESS: Backup instructions exported to: {output_file}")
        print("\nReview this document and update with your specific details:")
        print("  - Emergency contact information")
        print("  - Backup custodian names")
        print("  - Storage locations")
        print("  - Cloud storage configuration")

    except Exception as e:
        print(f"\n✗ ERROR: Failed to export instructions: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Manage master encryption key backups",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Create encrypted backup (recommended)
  secret-rotator-backup create-encrypted
  
  # Create split key backup (5 shares, need 3 to restore)
  secret-rotator-backup create-split --shares 5 --threshold 3
  
  # List all backups
  secret-rotator-backup list
  
  # Verify an encrypted backup
  secret-rotator-backup verify backup.enc
  
  # Restore from encrypted backup
  secret-rotator-backup restore backup.enc
  
  # Restore from split key shares
  secret-rotator-backup restore-split share1.share share2.share share3.share

Architecture Note:
  Master key: data/.master.key (read-only)
  Backups:    data/key_backups/ (writable)
  This separation maintains security isolation.
        """,
    )

    parser.add_argument(
        "--key-file",
        default="data/.master.key",
        help="Path to master key file (default: data/.master.key)",
    )

    parser.add_argument(
        "--backup-dir",
        default="data/key_backups",  # CHANGED: From config/key_backups
        help="Backup directory (default: data/key_backups)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Create encrypted backup
    parser_encrypted = subparsers.add_parser(
        "create-encrypted", help="Create encrypted backup with passphrase (RECOMMENDED)"
    )
    parser_encrypted.add_argument("--name", help="Optional backup name")

    # Create split key backup
    parser_split = subparsers.add_parser(
        "create-split", help="Create split key backup (Shamir Secret Sharing)"
    )
    parser_split.add_argument("--shares", type=int, default=5, help="Number of shares (default: 5)")
    parser_split.add_argument(
        "--threshold", type=int, default=3, help="Threshold to reconstruct (default: 3)"
    )

    # Create plaintext backup
    parser_plain = subparsers.add_parser(
        "create-plaintext", help="Create unencrypted backup (NOT RECOMMENDED)"
    )
    parser_plain.add_argument("--name", help="Optional backup name")

    # List backups
    subparsers.add_parser("list", help="List all available backups")

    # Verify backup
    parser_verify = subparsers.add_parser("verify", help="Verify a backup")
    parser_verify.add_argument("backup_file", help="Path to backup file")

    # Restore from backup
    parser_restore = subparsers.add_parser("restore", help="Restore from encrypted backup")
    parser_restore.add_argument("backup_file", help="Path to backup file")

    # Restore from split
    parser_restore_split = subparsers.add_parser(
        "restore-split", help="Restore from split key shares"
    )
    parser_restore_split.add_argument("share_files", nargs="+", help="Paths to share files")

    # Export instructions
    parser_export = subparsers.add_parser(
        "export-instructions", help="Export backup and recovery instructions"
    )
    parser_export.add_argument(
        "--output", default="KEY_BACKUP_INSTRUCTIONS.txt", help="Output file path"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    commands = {
        "create-encrypted": create_encrypted_backup,
        "create-split": create_split_backup,
        "create-plaintext": create_plaintext_backup,
        "list": list_backups,
        "verify": verify_backup,
        "restore": restore_backup,
        "restore-split": restore_split_backup,
        "export-instructions": export_instructions,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
