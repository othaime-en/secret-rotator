"""
Enhanced logging system with configurable output, rotation, and structured logging.
"""

import logging
import logging.handlers
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any
from contextvars import ContextVar
from secret_rotator.config.settings import settings

# Context variable for tracking request/operation IDs
_request_context: ContextVar[Dict[str, Any]] = ContextVar("request_context", default={})


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    Makes logs easily parseable by log aggregation tools (ELK, Splunk, etc.)
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add custom fields from extra parameter
        if hasattr(record, "extra_fields"):
            log_data.update(record.extra_fields)

        # Add context (request ID, user ID, etc.)
        context = _request_context.get()
        if context:
            log_data["context"] = context

        return json.dumps(log_data)


class SensitiveDataFilter(logging.Filter):
    """
    Filter to mask sensitive data in logs.
    Prevents accidental logging of passwords, API keys, etc.
    """

    SENSITIVE_PATTERNS = [
        "password",
        "passwd",
        "pwd",
        "api_key",
        "apikey",
        "token",
        "secret",
        "credential",
        "authorization",
        "auth",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        # Mask sensitive data in message
        message = record.getMessage().lower()

        for pattern in self.SENSITIVE_PATTERNS:
            if pattern in message:
                # Replace with masked version
                record.msg = self._mask_sensitive_data(record.msg)

        return True

    def _mask_sensitive_data(self, msg: str) -> str:
        """Mask sensitive data patterns in message"""
        # Simple masking - in production, use regex for more sophisticated masking
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern in msg.lower():
                # Find and mask the value after the pattern
                import re

                # Pattern: key=value or key: value
                regex = re.compile(f"{pattern}[\"']?\\s*[:=]\\s*[\"']?([^\\s,\"']+)", re.IGNORECASE)
                msg = regex.sub(f"{pattern}=****", msg)

        return msg


class LoggerAdapter(logging.LoggerAdapter):
    """
    Custom adapter that adds context to all log messages.
    Useful for tracking requests, operations, or user sessions.
    """

    def process(self, msg, kwargs):
        # Add context to extra fields
        context = _request_context.get()
        if context:
            if "extra" not in kwargs:
                kwargs["extra"] = {}
            kwargs["extra"]["extra_fields"] = context

        return msg, kwargs


class LoggerManager:
    """
    Centralized logger management with configuration support.
    """

    _instance = None
    _loggers: Dict[str, logging.Logger] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self._configure_root_logger()

    def _configure_root_logger(self):
        """Configure the root logger with all handlers"""
        # Get configuration
        log_level = settings.get("logging.level", "INFO")
        log_file = settings.get("logging.file", "logs/rotation.log")
        console_enabled = settings.get("logging.console_enabled", True)
        structured_logging = settings.get("logging.structured", False)
        max_file_size = settings.get("logging.max_file_size", "10MB")
        backup_count = settings.get("logging.backup_count", 5)

        # Create logs directory
        log_dir = Path(log_file).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, log_level.upper()))

        # Remove existing handlers
        root_logger.handlers.clear()

        # Add sensitive data filter
        sensitive_filter = SensitiveDataFilter()

        # FILE HANDLER - with rotation
        file_handler = self._create_file_handler(
            log_file, max_file_size, backup_count, structured_logging
        )
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)

        # CONSOLE HANDLER - configurable
        if console_enabled:
            console_handler = self._create_console_handler(structured_logging)
            console_handler.addFilter(sensitive_filter)
            root_logger.addHandler(console_handler)

        # ERROR FILE HANDLER - separate file for errors
        if settings.get("logging.separate_error_log", True):
            error_file = log_file.replace(".log", "_errors.log")
            error_handler = self._create_error_handler(error_file, structured_logging)
            error_handler.addFilter(sensitive_filter)
            root_logger.addHandler(error_handler)

    def _create_file_handler(
        self, log_file: str, max_size: str, backup_count: int, structured: bool
    ) -> logging.Handler:
        """Create rotating file handler"""
        # Parse max_size (e.g., "10MB" -> 10485760 bytes)
        size_bytes = self._parse_size(max_size)

        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=size_bytes, backupCount=backup_count, encoding="utf-8"
        )

        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - "
                "[%(module)s:%(funcName)s:%(lineno)d] - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)

        return handler

    def _create_console_handler(self, structured: bool) -> logging.Handler:
        """Create console handler with color support"""
        handler = logging.StreamHandler(sys.stdout)

        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            # Simpler format for console (more readable)
            if self._supports_color():
                formatter = ColoredFormatter(
                    "%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"
                )
            else:
                formatter = logging.Formatter(
                    "%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"
                )
            handler.setFormatter(formatter)

        return handler

    def _create_error_handler(self, error_file: str, structured: bool) -> logging.Handler:
        """Create handler for ERROR and CRITICAL logs only"""
        handler = logging.FileHandler(error_file, encoding="utf-8")
        handler.setLevel(logging.ERROR)

        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - "
                "[%(module)s:%(funcName)s:%(lineno)d] - %(message)s\n"
                "Exception: %(exc_info)s\n",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)

        return handler

    def _parse_size(self, size_str: str) -> int:
        """Parse size string like '10MB' to bytes"""
        size_str = size_str.upper().strip()

        # Units must be checked in order from longest to shortest
        # to avoid 'MB' being matched by 'B'
        units = {"GB": 1024 * 1024 * 1024, "MB": 1024 * 1024, "KB": 1024, "B": 1}

        for unit, multiplier in units.items():
            if size_str.endswith(unit):
                try:
                    number_str = size_str[: -len(unit)].strip()
                    number = float(number_str)
                    return int(number * multiplier)
                except ValueError:
                    # If parsing fails, continue to next unit
                    continue

        # Try to parse as plain number (bytes)
        try:
            return int(float(size_str))
        except ValueError:
            # Default to 10MB if parsing fails completely
            return 10 * 1024 * 1024

    def _supports_color(self) -> bool:
        """Check if terminal supports colors"""
        return (
            hasattr(sys.stdout, "isatty")
            and sys.stdout.isatty()
            and os.environ.get("TERM") != "dumb"
        )

    def get_logger(self, name: str) -> LoggerAdapter:
        """
        Get or create a logger with the given name.
        Returns a LoggerAdapter for context support.
        """
        if name not in self._loggers:
            self._loggers[name] = logging.getLogger(name)

        return LoggerAdapter(self._loggers[name], {})


class ColoredFormatter(logging.Formatter):
    """Formatter with color support for console output"""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"

        return super().format(record)


# Context manager for operation tracking
class LogContext:
    """
    Context manager for adding context to logs within a scope.

    Example:
        with LogContext(request_id="req-123", user_id="user-456"):
            logger.info("Processing request")  # Will include context
    """

    def __init__(self, **context):
        self.context = context
        self.token = None

    def __enter__(self):
        current = _request_context.get().copy()
        current.update(self.context)
        self.token = _request_context.set(current)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.token:
            _request_context.reset(self.token)


# Convenience functions
def set_log_level(level: str):
    """Dynamically change log level at runtime"""
    logging.getLogger().setLevel(getattr(logging, level.upper()))


def add_context(**kwargs):
    """Add context that will be included in all subsequent logs"""
    current = _request_context.get().copy()
    current.update(kwargs)
    _request_context.set(current)


def clear_context():
    """Clear all context"""
    _request_context.set({})


# Initialize logger manager and get default logger
_manager = LoggerManager()
logger = _manager.get_logger("secret-rotator")
