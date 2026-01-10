import time
import functools
from typing import Callable, Any, Type, Tuple
from secret_rotator.utils.logger import logger


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_multiplier: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """
    Decorator that retries a function with exponential backoff

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay in seconds
        backoff_multiplier: Multiplier for delay between retries
        exceptions: Tuple of exceptions to catch and retry on
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    result = func(*args, **kwargs)
                    if attempt > 0:
                        logger.info(f"Function {func.__name__} succeeded on attempt {attempt + 1}")
                    return result

                except exceptions as e:
                    last_exception = e
                    if attempt == max_attempts - 1:
                        logger.error(
                            f"Function {func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise e

                    logger.warning(f"Function {func.__name__} failed on attempt {attempt + 1}: {e}")
                    logger.info(f"Retrying in {delay} seconds...")

                    time.sleep(delay)
                    delay = min(delay * backoff_multiplier, max_delay)

            # This should never be reached, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


# Convenience decorators for common retry patterns
def retry_on_network_error(max_attempts: int = 3):
    """Retry on common network-related errors"""
    return retry_with_backoff(
        max_attempts=max_attempts, exceptions=(ConnectionError, TimeoutError, OSError)
    )


def retry_on_any_error(max_attempts: int = 3):
    """Retry on any other exceptions"""
    return retry_with_backoff(max_attempts=max_attempts, exceptions=(Exception,))
