"""
Flask application factory for Secret Rotation dashboard.

This module creates and configures the Flask application instance,
registers blueprints, and sets up error handlers.
"""

from flask import Flask, jsonify
from pathlib import Path
from secret_rotator.utils.logger import logger
from secret_rotator.web.secret_key import resolve_secret_key


def create_app(rotation_engine, config=None):
    """
    Application factory for creating Flask app instances.
    
    This pattern allows multiple app instances with different configurations
    (useful for testing) and defers configuration until runtime.
    
    Args:
        rotation_engine: RotationEngine instance to attach to app
        config: Optional dictionary of Flask configuration overrides
    
    Returns:
        Configured Flask application instance
    """
    # Determine paths relative to this file
    web_dir = Path(__file__).parent
    template_dir = web_dir / 'templates'
    static_dir = web_dir / 'static'
    
    app = Flask(
        __name__,
        template_folder=str(template_dir),
        static_folder=str(static_dir),
        static_url_path='/static'
    )
    
    # Default configuration. SECRET_KEY is intentionally left unset here
    # (rather than a hardcoded placeholder) — it's resolved explicitly
    # below via resolve_secret_key(), which is the single source of
    # truth for where a real key comes from and what happens if one
    # isn't configured.
    app.config.update(
        SECRET_KEY=None,
        JSON_SORT_KEYS=False,  # Preserve order in API responses
        TESTING=False,
    )
    
    # Apply custom configuration (e.g. web.secret_key plumbed through
    # from main.py, or overrides passed in by tests)
    if config:
        app.config.update(config)
    
    # Resolve the real SECRET_KEY now that any explicit config has been
    # applied. This will raise RuntimeError and abort startup if
    # SECRET_ROTATOR_ENV=production and no real key is configured.
    app.config['SECRET_KEY'] = resolve_secret_key(app.config.get('SECRET_KEY'))
    
    # Store rotation engine reference for access in routes
    app.rotation_engine = rotation_engine
    
    from .routes import dashboard_bp, api_bp, health_bp
    
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(health_bp, url_prefix='/api')
    
    register_error_handlers(app)
    
    logger.info("Flask application created successfully")
    logger.debug(f"Template folder: {template_dir}")
    logger.debug(f"Static folder: {static_dir}")
    
    return app


def register_error_handlers(app):
    """
    Register custom error handlers for common HTTP errors.
    
    These provide JSON responses for API endpoints and HTML for
    page requests, making the application more user-friendly.
    """
    
    @app.errorhandler(404)
    def not_found(error):
        """Handle 404 Not Found errors"""
        return jsonify({
            'error': 'Not Found',
            'message': 'The requested resource does not exist'
        }), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        """Handle 500 Internal Server Error"""
        logger.error(f"Internal server error: {error}")
        return jsonify({
            'error': 'Internal Server Error',
            'message': 'An unexpected error occurred'
        }), 500
    
    @app.errorhandler(405)
    def method_not_allowed(error):
        """Handle 405 Method Not Allowed"""
        return jsonify({
            'error': 'Method Not Allowed',
            'message': 'The method is not allowed for the requested URL'
        }), 405