#!/usr/bin/env python3
"""
CLI entry point for Secret Rotation System
"""
import sys


def main():
    """Main CLI entry point"""
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
