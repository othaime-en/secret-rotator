"""
Flask application factory for Secret Rotation dashboard.

This module creates and configures the Flask application instance,
registers blueprints, and sets up error handlers.
"""

from flask import Flask, jsonify, request, render_template
from pathlib import Path
from datetime import timedelta
from flask_wtf.csrf import CSRFProtect, CSRFError
from secret_rotator.utils.logger import logger
from secret_rotator.web.secret_key import resolve_secret_key
from secret_rotator.web.rate_limit import limiter, rate_limit_key


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
    
    app.config.update(
        SECRET_KEY=None,
        JSON_SORT_KEYS=False,  # Preserve order in API responses
        TESTING=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=False,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,  # 1 MB
        RATELIMIT_DEFAULT="200 per hour",
        RATELIMIT_HEADERS_ENABLED=True,
        # Explicit in-memory backend (Flask-Limiter's default, but set
        # here so the choice is documented rather than implicit — see
        # web/rate_limit.py for why this is the right call today and
        # what changes it if/when multi-instance deployment lands).
        RATELIMIT_STORAGE_URI="memory://",
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
    from .auth import bp as auth_bp, require_login, credentials_configured
    
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(health_bp, url_prefix='/api')
    app.register_blueprint(auth_bp)
    
    # Enforce login on every request except the small EXEMPT_ENDPOINTS
    # allow-list defined in web/auth.py (login page, static assets, the
    # unauthenticated /api/healthz liveness probe).
    app.before_request(require_login)
    
    # CSRF protection on all state-changing requests
    csrf = CSRFProtect()
    csrf.init_app(app)

    # Rate limiting (S10). See web/rate_limit.py for the key function
    # and per-route limits applied in routes/api.py, routes/health.py,
    # and web/auth.py.
    limiter.init_app(app)
    
    if not credentials_configured():
        import os
        env = os.getenv("SECRET_ROTATOR_ENV", "development").strip().lower()
        message = (
            "No web admin password is configured (web.auth.password_hash "
            "in config.yaml, or SECRET_ROTATOR_ADMIN_PASSWORD_HASH env "
            "var). The dashboard will be unreachable until you run: "
            "secret-rotator --mode set-web-password"
        )
        if env == "production":
            raise RuntimeError(message)
        logger.warning(message)
    
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
    
    @app.errorhandler(429)
    def rate_limit_exceeded(error):
        """
        Handle rate limit breaches.

        Flask-Limiter raises RateLimitExceeded (a 429 TooManyRequests
        subclass) when a request exceeds its configured limit — either
        the app-wide RATELIMIT_DEFAULT or a stricter per-route
        @limiter.limit(...). Flask-Limiter already sets Retry-After /
        X-RateLimit-* response headers (RATELIMIT_HEADERS_ENABLED);
        this just gives API callers a clean JSON body instead of the
        default HTML error page, matching the other handlers here.
        """
        logger.warning(f"Rate limit exceeded for {request.path} ({rate_limit_key()})")

        if request.path.startswith('/api/'):
            return jsonify({
                'error': 'Too Many Requests',
                'message': str(error.description) if error.description else
                           'Rate limit exceeded. Please slow down and try again shortly.'
            }), 429

        return render_template(
            'login.html',
            error="Too many attempts. Please wait a moment and try again.",
            next=request.args.get('next', ''),
        ), 429

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        """
        Handle CSRF validation failures.
        """
        logger.warning(f"CSRF validation failed for {request.path}: {error.description}")
        
        if request.path.startswith('/api/'):
            return jsonify({
                'error': 'CSRF validation failed',
                'message': 'Missing or invalid CSRF token. Reload the page and try again.'
            }), 400
        
        return render_template(
            'login.html',
            error="Your session expired or the form was tampered with. Please sign in again.",
            next=request.args.get('next', ''),
        ), 400