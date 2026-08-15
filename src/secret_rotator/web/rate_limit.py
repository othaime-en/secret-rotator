"""
Rate limiting for the Secret Rotator web dashboard.
"""

from flask import session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


def rate_limit_key() -> str:
    """
    Key requests by authenticated username when there is one, falling
    back to remote IP address for unauthenticated requests.
    """
    username = session.get("username")
    if username:
        return f"user:{username}"
    return get_remote_address()


limiter = Limiter(key_func=rate_limit_key)