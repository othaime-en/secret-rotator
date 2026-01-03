import logging
import logging.handlers
import os
import sys
import re
from pathlib import Path
from config.settings import settings

def parse_size(size_str: str) -> int:
    """Parse size string like '10MB' to bytes"""
    size_str = size_str.upper().strip()
    
    if size_str.endswith('GB'):
        return int(float(size_str[:-2]) * 1024 * 1024 * 1024)
    elif size_str.endswith('MB'):
        return int(float(size_str[:-2]) * 1024 * 1024)
    elif size_str.endswith('KB'):
        return int(float(size_str[:-2]) * 1024)
    else:
        return int(size_str)


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


def setup_logger():
    """Set up logging configuration"""
    
    # Create logs directory if it doesn't exist
    log_file = settings.get('logging.file', 'logs/rotation.log')
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get configuration
    console_enabled = settings.get('logging.console_enabled', True)
    max_file_size = settings.get('logging.max_file_size', '10MB')
    backup_count = settings.get('logging.backup_count', 5)
    log_level = settings.get('logging.level', 'INFO')
    
    # Parse file size
    max_bytes = parse_size(max_file_size)
    
    # Create formatters
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - '
        '[%(module)s:%(funcName)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Create sensitive data filter
    sensitive_filter = SensitiveDataFilter()
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # File handler with detailed format
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(sensitive_filter)
    root_logger.addHandler(file_handler)
    
    # Console handler with simple format (if enabled)
    if console_enabled:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(sensitive_filter)
        root_logger.addHandler(console_handler)
    
    return logging.getLogger('secret-rotator')

# Global logger instance
logger = setup_logger()