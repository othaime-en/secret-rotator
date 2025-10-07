import sys
import time
import signal
from pathlib import Path

# Add src to path so we can import our modules
sys.path.append(str(Path(__file__).parent))

from config.settings import settings
from providers.file_provider import FileSecretProvider
from rotators.password_rotator import PasswordRotator
from rotation_engine import RotationEngine
from scheduler import RotationScheduler
from web_interface import WebServer
from utils.logger import logger


class SecretRotationApp:
    """This is the main application class"""

    def __init__(self):
        self.engine = None
        self.scheduler = None
        self.web_server = None
        self.running = False

    def setup(self):
        """Set up the application components"""
        logger.info("Setting up Secret Rotation System")
        
        # Initialize rotation engine
        self.engine = RotationEngine()
        
        # Set up providers
        file_provider = FileSecretProvider(
            name="file_storage",
            config={"file_path": "data/secrets.json"}
        )
        self.engine.register_provider(file_provider)
        
        # Set up rotators
        password_rotator = PasswordRotator(
            name="password_gen",
            config={
                "length": 16,
                "use_symbols": True,
                "use_numbers": True,
                "use_uppercase": True,
                "use_lowercase": True
            }
        )
        self.engine.register_rotator(password_rotator)
        
        # Add rotation jobs from config or hardcoded
        jobs = [
            {
                "name": "database_password",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "db_password"
            },
            {
                "name": "api_key",
                "provider": "file_storage",
                "rotator": "password_gen",
                "secret_id": "api_key"
            },
            {
                "name": "service_token",
                "provider": "file_storage",
                "rotator": "password_gen", 
                "secret_id": "service_token"
            }
        ]
        
        for job in jobs:
            self.engine.add_rotation_job(job)
        
        # Set up scheduler
        self.scheduler = RotationScheduler(self.engine.rotate_all_secrets)
        schedule_config = settings.get('rotation.schedule', 'daily')
        self.scheduler.setup_schedule(schedule_config)
        
        # Set up web server
        self.web_server = WebServer(self.engine, port=8080)
        
        logger.info("Setup complete")

    def start(self):
        """Start all components"""
        if not self.engine:
            self.setup()
        
        self.running = True
        
        # Start scheduler
        self.scheduler.start()
        
        # Start web server
        self.web_server.start()
        
        logger.info("Secret Rotation System started")
        logger.info("Web interface available at: http://localhost:8080")
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Keep the main thread alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop all components"""
        logger.info("Shutting down Secret Rotation System")
        
        self.running = False
        
        if self.scheduler:
            self.scheduler.stop()
        
        if self.web_server:
            self.web_server.stop()
        
        logger.info("Shutdown complete")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
    
    def run_once(self):
        """Run rotation once (for testing or manual execution)"""
        if not self.engine:
            self.setup()
        
        logger.info("Running one-time secret rotation")
        results = self.engine.rotate_all_secrets()
        
        print("\nRotation Results:")
        for job_name, success in results.items():
            status = "SUCCESS" if success else "FAILED"
            print(f"  {job_name}: {status}")
        
        return results


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Secret Rotation System')
    parser.add_argument('--mode', choices=['daemon', 'once'], default='daemon',
                      help='Run mode: daemon (with scheduler and web interface) or once (single rotation)')
    
    args = parser.parse_args()
    
    app = SecretRotationApp()
    
    if args.mode == 'once':
        app.run_once()
    else:
        app.start()

if __name__ == "__main__":
    main()
    
