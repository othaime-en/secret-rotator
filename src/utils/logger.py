"""
Enhanced logging system with configurable output, rotation, and structured logging.
"""
import logging
import logging.handlers
import json
import os
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from config.settings import settings


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.
    Makes logs easily parseable by log aggregation tools (ELK, Splunk, etc.)
    """
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add custom fields from extra parameter
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)
        
        return json.dumps(log_data)


class SensitiveDataFilter(logging.Filter):
    """
    Filter to mask sensitive data in logs.
    Prevents accidental logging of passwords, API keys, etc.
    """
    
    SENSITIVE_PATTERNS = [
        'password', 'passwd', 'pwd',
        'api_key', 'apikey', 'token',
        'secret', 'credential',
        'authorization', 'auth'
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
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern in msg.lower():
                # Pattern: key=value or key: value
                regex = re.compile(
                    f'{pattern}["\']?\\s*[:=]\\s*["\']?([^\\s,"\']+)',
                    re.IGNORECASE
                )
                msg = regex.sub(f'{pattern}=****', msg)
        
        return msg


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
        if hasattr(self, '_initialized'):
            return
        
        self._initialized = True
        self._configure_root_logger()
    
    def _configure_root_logger(self):
        """Configure the root logger with all handlers"""
        # Get configuration
        log_level = settings.get('logging.level', 'INFO')
        log_file = settings.get('logging.file', 'logs/rotation.log')
        console_enabled = settings.get('logging.console_enabled', True)
        structured_logging = settings.get('logging.structured', False)
        max_file_size = settings.get('logging.max_file_size', '10MB')
        backup_count = settings.get('logging.backup_count', 5)
        
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
            log_file, 
            max_file_size, 
            backup_count,
            structured_logging
        )
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)
        
        # CONSOLE HANDLER - configurable
        if console_enabled:
            console_handler = self._create_console_handler(structured_logging)
            console_handler.addFilter(sensitive_filter)
            root_logger.addHandler(console_handler)
        
        # ERROR FILE HANDLER - separate file for errors
        if settings.get('logging.separate_error_log', True):
            error_file = log_file.replace('.log', '_errors.log')
            error_handler = self._create_error_handler(error_file, structured_logging)
            error_handler.addFilter(sensitive_filter)
            root_logger.addHandler(error_handler)
    
    def _create_file_handler(
        self, 
        log_file: str, 
        max_size: str, 
        backup_count: int,
        structured: bool
    ) -> logging.Handler:
        """Create rotating file handler"""
        # Parse max_size (e.g., "10MB" -> 10485760 bytes)
        size_bytes = self._parse_size(max_size)
        
        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=size_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        
        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - '
                '[%(module)s:%(funcName)s:%(lineno)d] - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
        
        return handler
    
    def _create_console_handler(self, structured: bool) -> logging.Handler:
        """Create console handler"""
        handler = logging.StreamHandler(sys.stdout)
        
        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            # Simpler format for console (more readable)
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s',
                datefmt='%H:%M:%S'
            )
            handler.setFormatter(formatter)
        
        return handler
    
    def _create_error_handler(self, error_file: str, structured: bool) -> logging.Handler:
        """Create handler for ERROR and CRITICAL logs only"""
        handler = logging.FileHandler(error_file, encoding='utf-8')
        handler.setLevel(logging.ERROR)
        
        if structured:
            handler.setFormatter(StructuredFormatter())
        else:
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - '
                '[%(module)s:%(funcName)s:%(lineno)d] - %(message)s\n'
                'Exception: %(exc_info)s\n',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
        
        return handler
    
    def _parse_size(self, size_str: str) -> int:
        """Parse size string like '10MB' to bytes"""
        size_str = size_str.upper().strip()
        
        # Units must be checked in order from longest to shortest
        # to avoid 'MB' being matched by 'B'
        units = {
            'GB': 1024 * 1024 * 1024,
            'MB': 1024 * 1024,
            'KB': 1024,
            'B': 1
        }
        
        for unit, multiplier in units.items():
            if size_str.endswith(unit):
                try:
                    number_str = size_str[:-len(unit)].strip()
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
    
    def get_logger(self, name: str) -> logging.Logger:
        """
        Get or create a logger with the given name.
        """
        if name not in self._loggers:
            self._loggers[name] = logging.getLogger(name)
        
        return self._loggers[name]


# Initialize logger manager and get default logger
_manager = LoggerManager()
logger = _manager.get_logger('secret-rotator')