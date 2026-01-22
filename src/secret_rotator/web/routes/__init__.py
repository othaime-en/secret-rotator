"""
Flask blueprints for Secret Rotation web interface.

Blueprints organize routes into logical groups:
- dashboard: Main UI routes (HTML pages)
- api: RESTful API endpoints for rotation operations
- health: Health check and monitoring endpoints
"""

from .dashboard import bp as dashboard_bp
from .api import bp as api_bp
from .health import bp as health_bp

__all__ = ['dashboard_bp', 'api_bp', 'health_bp']