import schedule
from typing import Callable
from src.utils.logger import logger

class RotationScheduler:
    """Handle scheduled secret rotations"""
    
    def __init__(self, rotation_function: Callable):
        self.rotation_function = rotation_function
        self.running = False
        self.thread = None
    
    def setup_schedule(self, schedule_config: str):
        # Set up rotation schedule
        schedule.clear()  # Clear any existing schedules
        
        if schedule_config == "daily":
            schedule.every().day.at("02:00").do(self._run_rotation)
        elif schedule_config == "weekly":
            schedule.every().week.do(self._run_rotation)
        elif schedule_config.startswith("every_"):
            # Format: "every_30_minutes" or "every_2_hours"
            parts = schedule_config.split("_")
            if len(parts) >= 3:
                interval = int(parts[1])
                unit = parts[2]
                if unit == "minutes":
                    schedule.every(interval).minutes.do(self._run_rotation)
                elif unit == "hours":
                    schedule.every(interval).hours.do(self._run_rotation)
        
        logger.info(f"Scheduled rotation: {schedule_config}")
    
    def _run_rotation(self):
        #Internal method to run rotation with error handling
        try:
            logger.info("Scheduled rotation starting")
            results = self.rotation_function()
            successful = sum(1 for result in results.values() if result)
            logger.info(f"Scheduled rotation complete: {successful}/{len(results)} successful")
        except Exception as e:
            logger.error(f"Error in scheduled rotation: {e}")
    