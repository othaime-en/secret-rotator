"""
Flask-based web interface for Secret Rotation System.
This is the new implementation that will gradually replace web_interface.py.
"""

from flask import Flask
from threading import Thread
from werkzeug.serving import make_server
from secret_rotator.utils.logger import logger


class FlaskWebServer:
    """
    Flask-based web server for the Secret Rotation dashboard.
    
    This server runs in a background thread and provides:
    - RESTful API endpoints for rotation operations
    - Real-time backup health monitoring
    - Manual rotation triggers
    - Backup management interface
    
    Runs on a separate port initially (default: 8081) to allow
    parallel testing with the legacy server during migration.
    """
    
    def __init__(self, rotation_engine, port=8081, host='localhost', config=None):
        """
        Initialize Flask web server.
        
        Args:
            rotation_engine: RotationEngine instance to manage
            port: Port to listen on (default: 8081 for parallel testing)
            host: Host to bind to (default: localhost)
            config: Optional configuration dictionary
        """
        from .app import create_app
        
        self.rotation_engine = rotation_engine
        self.port = port
        self.host = host
        self.config = config or {}
        
        # Create Flask app
        self.app = create_app(rotation_engine, self.config)
        
        # Server instance (created on start)
        self.server = None
        self.thread = None
    
    def start(self):
        """
        Start the Flask server in a background thread.
        
        This allows the server to run without blocking the main application.
        Uses Werkzeug's built-in development server.
        """
        if self.server is not None:
            logger.warning("Flask server already running")
            return
        
        # Create Werkzeug server
        self.server = make_server(self.host, self.port, self.app, threaded=True)
        
        # Start in daemon thread (will stop when main thread exits)
        self.thread = Thread(target=self.server.serve_forever, daemon=True, name="FlaskServer")
        self.thread.start()
        
        logger.info(f"Flask web server started on http://{self.host}:{self.port}")
        logger.info(f"Dashboard available at http://{self.host}:{self.port}/")
    
    def stop(self):
        """
        Gracefully shutdown the Flask server.
        
        Stops the Werkzeug server and waits for the thread to terminate.
        """
        if self.server is None:
            logger.warning("Flask server not running")
            return
        
        logger.info("Shutting down Flask web server...")
        self.server.shutdown()
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        
        self.server = None
        self.thread = None
        logger.info("Flask web server stopped")


__all__ = ['FlaskWebServer']