"""
Advanced secret rotators for different secret types.
"""
from typing import Dict, Any
from .base import SecretRotator
from utils.logger import logger

class DatabasePasswordRotator(SecretRotator):
    """
    Rotate database passwords with connection testing.
    Supports MySQL, PostgreSQL, MongoDB, etc.
    """

    plugin_name = "database_password"

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(name, config)
        self.db_type = config.get('db_type', 'postgresql')
        self.host = config.get('host', 'localhost')
        self.port = config.get('port')
        self.database = config.get('database')
        self.username = config.get('username')
        self.length = config.get('length', 32)
        self.test_connection = config.get('test_connection', True)