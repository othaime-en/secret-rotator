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


def get_config_dir():
    """Get platform-specific config directory"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return Path(base) / "secret-rotator"
    else:
        # Unix-like: use XDG Base Directory spec
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config:
            return Path(xdg_config) / "secret-rotator"
        return Path.home() / ".config" / "secret-rotator"


def get_data_dir():
    """Get platform-specific data directory"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return Path(base) / "secret-rotator" / "data"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            return Path(xdg_data) / "secret-rotator"
        return Path.home() / ".local" / "share" / "secret-rotator"


def get_log_dir():
    """Get platform-specific log directory"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return Path(base) / "secret-rotator" / "logs"
    else:
        xdg_state = os.environ.get("XDG_STATE_HOME")
        if xdg_state:
            return Path(xdg_state) / "secret-rotator" / "logs"
        return Path.home() / ".local" / "state" / "secret-rotator" / "logs"


def create_directories(config_dir, data_dir, log_dir):
    """Create necessary directories"""
    print("\n📁 Creating directories...")

    directories = [config_dir, data_dir, data_dir / "backup", log_dir]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)  # Restrictive permissions
        print(f"  ✓ {directory}")


def create_config(config_dir, data_dir, log_dir):
    """Create default configuration"""
    config_file = config_dir / "config.yaml"

    if config_file.exists():
        response = input(
            f"\n⚠️  Configuration already exists at {config_file}\n   Overwrite? (yes/no): "
        )
        if response.lower() != "yes":
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
        "4": input("  Enter custom schedule (e.g., every_30_minutes): "),
    }
    schedule = schedule_map.get(choice, "daily")

    # Create configuration
    config = {
        "rotation": {
            "schedule": schedule,
            "retry_attempts": 3,
            "timeout": 30,
            "backup_old_secrets": True,
        },
        "logging": {
            "level": "INFO",
            "file": str(log_dir / "rotation.log"),
            "console_enabled": True,
            "structured": False,
            "max_file_size": "10MB",
            "backup_count": 5,
            "separate_error_log": True,
        },
        "web": {"enabled": True, "port": 8080, "host": "localhost"},
        "providers": {
            "file_storage": {
                "type": "file",
                "file_path": str(data_dir / "secrets.json"),
                "backup_path": str(data_dir / "backup"),
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
                "exclude_ambiguous": True,
            }
        },
        "security": {
            "encryption": {
                "enabled": True,
                "master_key_file": str(config_dir / ".master.key"),
                "rotate_master_key_days": 90,
            }
        },
        "backup": {
            "enabled": True,
            "storage_path": str(data_dir / "backup"),
            "encrypt_backups": True,
            "cleanup_time": "03:00",
            "verification_time": "04:00",
            "verify_integrity": True,
            "retention": {"days": 90, "max_backups_per_secret": 10},
        },
        "jobs": [
            {
                "name": "example_password",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "example_secret",
                "schedule": "weekly",
                "notification": False,
            }
        ],
    }

    # Write configuration
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    os.chmod(config_file, 0o600)  # Restrictive permissions
    print(f"  ✓ Configuration saved to {config_file}")

    return config_file


def setup_encryption(config_dir):
    """Set up encryption and generate master key"""
    print("\n🔐 Setting up encryption...")

    master_key_file = config_dir / ".master.key"

    if master_key_file.exists():
        response = input("\n⚠️  Master key already exists\n   Generate new key? (yes/no): ")
        if response.lower() != "yes":
            print("Keeping existing master key")
            return

        # Backup existing key
        backup_file = master_key_file.with_suffix(".key.backup")
        shutil.copy2(master_key_file, backup_file)
        print(f"  ✓ Backed up existing key to {backup_file}")

    # Import encryption manager to generate key
    try:
        from secret_rotator.encryption_manager import EncryptionManager

        # This will automatically generate a new key if it doesn't exist
        em = EncryptionManager(key_file=str(master_key_file))

        os.chmod(master_key_file, 0o600)
        print(f"  ✓ Master encryption key generated: {master_key_file}")
        print("\n  ⚠️  CRITICAL: Backup this key immediately!")
        print("     Run: secret-rotator-backup create-encrypted")

    except Exception as e:
        print(f"  ✗ Error generating master key: {e}")
        sys.exit(1)


def print_summary(config_dir, data_dir, log_dir, config_file):
    """Print setup summary and next steps"""
    print("\n" + "=" * 70)
    print("✓ SETUP COMPLETE")
    print("=" * 70)

    print("\nDirectories created:")
    print(f"  Config:  {config_dir}")
    print(f"  Data:    {data_dir}")
    print(f"  Logs:    {log_dir}")

    print(f"\nConfiguration: {config_file}")

    print("\n" + "=" * 70)
    print("NEXT STEPS")
    print("=" * 70)

    print("\n1. BACKUP YOUR MASTER KEY (Critical!)")
    print("   Run: secret-rotator-backup create-encrypted")
    print("   Store the passphrase in a password manager")

    print("\n2. Edit configuration if needed:")
    print(f"   {config_file}")

    print("\n3. Add your rotation jobs to the 'jobs' section")

    print("\n4. Verify setup:")
    print("   secret-rotator --mode verify")

    print("\n5. Start the application:")
    print("   secret-rotator")

    print("\n6. Access web interface:")
    print("   http://localhost:8080")

    print("\n" + "=" * 70)


def main():
    """Main setup wizard"""
    print("=" * 70)
    print("SECRET ROTATION SYSTEM - SETUP WIZARD")
    print("=" * 70)
    print("\nThis wizard will set up Secret Rotation System on your machine.")
    print("It will create configuration files and necessary directories.")

    # Determine directories
    config_dir = get_config_dir()
    data_dir = get_data_dir()
    log_dir = get_log_dir()

    print("\nInstallation locations:")
    print(f"  Config: {config_dir}")
    print(f"  Data:   {data_dir}")
    print(f"  Logs:   {log_dir}")

    response = input("\nContinue? (yes/no): ")
    if response.lower() != "yes":
        print("Setup cancelled")
        sys.exit(0)

    try:
        # Create directories
        create_directories(config_dir, data_dir, log_dir)

        # Create configuration
        config_file = create_config(config_dir, data_dir, log_dir)

        # Setup encryption
        setup_encryption(config_dir)

        # Print summary
        print_summary(config_dir, data_dir, log_dir, config_file)

    except KeyboardInterrupt:
        print("\n\nSetup interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Setup failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
