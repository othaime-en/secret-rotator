import unittest
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent / "src"))

class TestRetryDecorator(unittest.TestCase):
    
    def test_retry_success_first_attempt(self):
        """Test function succeeds on first attempt"""
        from utils.retry import retry_with_backoff
        
        call_count = 0
        
        @retry_with_backoff(max_attempts=3)
        def successful_function():
            nonlocal call_count
            call_count += 1
            return "success"
        
        result = successful_function()
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 1)
    
    def test_retry_success_after_failures(self):
        """Test function succeeds after some failures"""
        from utils.retry import retry_with_backoff
        
        call_count = 0
        
        @retry_with_backoff(max_attempts=3, initial_delay=0.1)
        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"
        
        result = flaky_function()
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)
    
    def test_retry_max_attempts_exceeded(self):
        """Test function fails after max attempts"""
        from utils.retry import retry_with_backoff
        
        call_count = 0
        
        @retry_with_backoff(max_attempts=3, initial_delay=0.1)
        def failing_function():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")
        
        with self.assertRaises(ValueError):
            failing_function()
        
        self.assertEqual(call_count, 3)
    
    def test_retry_specific_exceptions(self):
        """Test retry only catches specified exceptions"""
        from utils.retry import retry_with_backoff
        
        @retry_with_backoff(max_attempts=3, exceptions=(ConnectionError,))
        def wrong_exception_function():
            raise ValueError("Wrong exception type")
        
        with self.assertRaises(ValueError):
            wrong_exception_function()

