"""
Dashboard routes for serving HTML pages.

This blueprint handles the main user interface, rendering
Jinja2 templates for the dashboard and related pages.
"""

from flask import Blueprint, render_template, current_app
from secret_rotator.utils.logger import logger

bp = Blueprint('dashboard', __name__)


@bp.route('/')
def index():
    """
    Main dashboard page.
    
    Serves the primary UI with tabs for:
    - Rotation Jobs
    - Backups
    - Backup Health
    - Activity Logs
    """
    logger.info("Dashboard page accessed")
    
    # Get basic stats for initial page load
    engine = current_app.rotation_engine
    stats = {
        'total_jobs': len(engine.rotation_jobs),
        'total_providers': len(engine.providers),
        'total_rotators': len(engine.rotators),
    }
    
    return render_template('dashboard.html', stats=stats)


@bp.route('/health')
def health_page():
    """
    Dedicated backup health monitoring page.
    
    Provides detailed view of backup system health,
    verification history, and integrity status.
    """
    logger.info("Health page accessed")
    return render_template('health.html')