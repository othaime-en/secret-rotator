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


class SecretAccessPolicy:
    """Define who/what can access which secrets"""

    def __init__(self):
        self.policies: Dict[str, Dict[str, Any]] = {}

    def add_policy(self, secret_id: str, allowed_services: List[str],
                   allowed_ips: Optional[List[str]] = None,
                   expiry_hours: Optional[int] = None):
        """Add an access policy for a secret"""
        self.policies[secret_id] = {
            "allowed_services": allowed_services,
            "allowed_ips": allowed_ips or [],
            "expiry_hours": expiry_hours,
            "created_at": datetime.now().isoformat()
        }

    def can_access(self, secret_id: str, service_name: str,
                   ip_address: Optional[str] = None) -> bool:
        """Check if a service can access a secret"""
        policy = self.policies.get(secret_id)
        if not policy:
            return False

        # Check service name
        if service_name not in policy["allowed_services"]:
            return False

        # Check IP if specified
        if policy["allowed_ips"] and ip_address:
            if ip_address not in policy["allowed_ips"]:
                return False

        # Check expiry
        if policy["expiry_hours"]:
            created = datetime.fromisoformat(policy["created_at"])
            expiry = created + timedelta(hours=policy["expiry_hours"])
            if datetime.now() > expiry:
                return False

        return True