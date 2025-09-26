import sys
from pathlib import Path

# Add src to path so we can import our modules
sys.path.append(str(Path(__file__).parent))

from config.settings import settings
from providers.file_provider import FileSecretProvider
from rotators.password_rotator import PasswordRotator
from rotation_engine import RotationEngine
from utils.logger import logger

def main():
    """This is the main application entry point"""
    logger.info("Starting Secret Rotation System")
    
    # Initialize the rotation engine
    engine = RotationEngine()

    # Set up providers and rotators
    file_provider = FileSecretProvider(
        name="file_storage",
        config={"file_path": "data/secrets.json"}
    )
    engine.register_provider(file_provider)

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
    engine.register_rotator(password_rotator)

    # Add rotation jobs
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
        }
    ]

    for job in jobs:
        engine.add_rotation_job(job)

    # perform the rotation
    results = engine.rotate_all_secrets()  


    # Print the results
    print("\n Rotation Results")
    for job_name, success in results.items():
        status="SUCCESS" if success else "FAILED"
        print(f" {job_name}:{status}")
    
    logger.info("Secret Rotation System finished")

if __name__ == "__main__":
    main()