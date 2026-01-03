import logging
import logging.handlers
import os
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
    
    # Parse file size
    max_bytes = parse_size(max_file_size)
    
    # Configure handlers list
    handlers = [
        logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
    ]
    
    # Add console handler only if enabled
    if console_enabled:
        handlers.append(logging.StreamHandler())
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.get('logging.level', 'INFO')),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    
    return logging.getLogger('secret-rotator')

# Global logger instance
logger = setup_logger()