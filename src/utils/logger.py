import logging
import os
from pathlib import Path
from config.settings import settings

def setup_logger():
    """Set up logging configuration"""
    
    # Create logs directory if it doesn't exist
    log_file = settings.get('logging.file', 'logs/rotation.log')
    log_dir = Path(log_file).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get console configuration
    console_enabled = settings.get('logging.console_enabled', True)
    
    # Configure handlers list
    handlers = [logging.FileHandler(log_file)]
    
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