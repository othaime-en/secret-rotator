#!/usr/bin/env python3
"""
CLI entry point for Secret Rotation System
This is the main command that users run after installation
"""
import sys
import os
from pathlib import Path

# Add src to path for development/source installations
src_dir = Path(__file__).parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

def main():
    """Main CLI entry point"""
    # Import here to avoid issues during setup
    from secret_rotator.main import main as app_main
    
    try:
        app_main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()