import http.server
from utils.logger import logger


class WebServer:
    # This is the main web server manager class
    
    def __init__(self, rotation_engine, port=8080):
        self.rotation_engine = rotation_engine
        self.port = port
        self.server = None
    
    def start(self):
        # I'll implement the starting logic 
        logger.info(f"Web server started on http://localhost:{self.port}")
    
    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        logger.info("Web server stopped")


