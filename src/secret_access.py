"""
Secret access control and distribution system.
Provides secure ways for applications to consume rotated secrets.
"""
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from utils.logger import logger
from encryption_manager import EncryptionManager, SecretMasker