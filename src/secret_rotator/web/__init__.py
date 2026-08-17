"""
Flask-based web interface for Secret Rotation System.
This is the new implementation that will gradually replace web_interface.py.
"""

from threading import Thread
from waitress.server import create_server
from secret_rotator.utils.logger import logger


class FlaskWebServer:
    """
    Flask-based web server for the Secret Rotation dashboard.
    
    This server runs in a background thread and provides:
    - RESTful API endpoints for rotation operations
    - Real-time backup health monitoring
    - Manual rotation triggers
    - Backup management interface

    (S11) Serves the app with Waitress, a WSGI server built for
    production traffic — proper connection handling, a bounded worker
    thread pool, and hardening against slow clients — instead of
    Werkzeug's `make_server`, which Werkzeug's own docs say is not
    designed to be exposed to real traffic.

    Waitress runs in-process (no forked worker processes), which is
    what lets it slot in here as a near drop-in replacement: the
    RotationEngine and APScheduler-based scheduler this app hands to
    Flask are in-memory singletons with no cross-process sharing story
    yet, so a forking server (Gunicorn/uWSGI with worker processes > 1)
    would need that problem solved first. That multi-instance-safe
    design is tracked separately (Phase 3: job queue + distributed
    locking) — see the audit roadmap. `threads` below controls
    Waitress's request-handling thread pool, which is the concurrency
    knob available under this single-process model.
    """
    
    def __init__(self, rotation_engine, port=8081, host='localhost', config=None, threads=8):
        """
        Initialize Flask web server.
        
        Args:
            rotation_engine: RotationEngine instance to manage
            port: Port to listen on (default: 8081 for parallel testing)
            host: Host to bind to (default: localhost)
            config: Optional configuration dictionary
            threads: Size of Waitress's request-handling thread pool
                (default: 8). Comes from web.threads in config.yaml.
        """
        from .app import create_app
        
        self.rotation_engine = rotation_engine
        self.port = port
        self.host = host
        self.config = config or {}
        self.threads = threads
        
        # Create Flask app
        self.app = create_app(rotation_engine, self.config)
        
        # Server instance (created on start)
        self.server = None
        self.thread = None
    
    def start(self):
        """
        Start the Flask server in a background thread.
        
        This allows the server to run without blocking the main application.
        Uses Waitress, a production-grade pure-Python WSGI server (S11).
        """
        if self.server is not None:
            logger.warning("Flask server already running")
            return
        
        # Create Waitress server. `_quiet=False` (default) lets it log
        # its own startup banner; we mirror the key details below via
        # our own logger so they land in structured logs too.
        self.server = create_server(
            self.app,
            host=self.host,
            port=self.port,
            threads=self.threads,
        )
        
        # Start in daemon thread (will stop when main thread exits)
        self.thread = Thread(target=self.server.run, daemon=True, name="FlaskServer")
        self.thread.start()
        
        logger.info(
            f"Flask web server started on http://{self.host}:{self.port} "
            f"(waitress, {self.threads} threads)"
        )
        logger.info(f"Dashboard available at http://{self.host}:{self.port}/")
    
    def stop(self):
        """
        Gracefully shutdown the Flask server.
        
        Stops the Waitress server and waits for the thread to terminate.
        """
        if self.server is None:
            logger.warning("Flask server not running")
            return
        
        logger.info("Shutting down Flask web server...")
        self.server.close()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        self.server = None
        self.thread = None
        logger.info("Flask web server stopped")


__all__ = ['FlaskWebServer']