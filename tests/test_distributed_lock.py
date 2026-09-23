import unittest

from secret_rotator.config.settings import settings
from secret_rotator.distributed_lock import (
    DistributedLock,
    LockAcquisitionError,
    distributed_lock,
)


class TestDistributedLockLocalFallback(unittest.TestCase):
    """`distributed.enabled` unset/false is the default and the case
    every existing single-instance deployment runs under — these tests
    cover that path, which requires no Redis at all."""

    def setUp(self):
        # Make sure we're exercising the local-fallback path regardless
        # of what a previous test (or the developer's machine) left in
        # config, and clean up any lock left held by a prior failed run
        # so tests don't leak state into each other.
        self._original_enabled = settings.get("distributed.enabled", False)
        settings.set("distributed.enabled", False)
        DistributedLock._local_locks.pop("secret-rotator:lock:test-lock", None)

    def tearDown(self):
        settings.set("distributed.enabled", self._original_enabled)
        DistributedLock._local_locks.pop("secret-rotator:lock:test-lock", None)

    def test_acquire_and_release(self):
        lock = distributed_lock("test-lock", blocking_timeout=1)
        lock.acquire()
        lock.release()  # should not raise

    def test_second_acquire_while_held_fails_fast(self):
        first = distributed_lock("test-lock", blocking_timeout=0)
        second = distributed_lock("test-lock", blocking_timeout=0)

        first.acquire()
        try:
            with self.assertRaises(LockAcquisitionError):
                second.acquire()
        finally:
            first.release()

    def test_lock_is_released_allowing_a_new_acquire(self):
        first = distributed_lock("test-lock", blocking_timeout=0)
        first.acquire()
        first.release()

        second = distributed_lock("test-lock", blocking_timeout=0)
        second.acquire()  # should not raise now that first released
        second.release()

    def test_context_manager_releases_on_exception(self):
        with self.assertRaises(ValueError):
            with distributed_lock("test-lock", blocking_timeout=0):
                raise ValueError("boom")

        # Lock must have been released despite the exception.
        probe = distributed_lock("test-lock", blocking_timeout=0)
        probe.acquire()
        probe.release()

    def test_release_without_acquire_is_a_safe_noop(self):
        lock = distributed_lock("test-lock", blocking_timeout=0)
        lock.release()  # should not raise

    def test_different_names_do_not_contend(self):
        a = distributed_lock("test-lock-a", blocking_timeout=0)
        b = distributed_lock("test-lock-b", blocking_timeout=0)
        a.acquire()
        b.acquire()  # different name, must not be blocked by `a`
        a.release()
        b.release()


class TestDistributedLockRedisConfig(unittest.TestCase):
    """`distributed.enabled: true` must fail loudly, not silently fall
    back to unprotected operation, when Redis isn't reachable/configured."""

    def setUp(self):
        self._original_enabled = settings.get("distributed.enabled", False)
        self._original_url = settings.get("distributed.redis_url", None)
        settings.set("distributed.enabled", True)

    def tearDown(self):
        settings.set("distributed.enabled", self._original_enabled)
        if self._original_url is not None:
            settings.set("distributed.redis_url", self._original_url)

    def test_missing_redis_url_raises_lock_acquisition_error(self):
        settings.set("distributed.redis_url", None)
        lock = distributed_lock("test-lock", blocking_timeout=0)
        with self.assertRaises(LockAcquisitionError):
            lock.acquire()

    def test_unreachable_redis_raises_lock_acquisition_error(self):
        # Port 1 is not a Redis server (and requires no real network
        # access), so this exercises the "backend configured but
        # unreachable" path deterministically and without touching
        # any live infrastructure.
        settings.set("distributed.redis_url", "redis://localhost:1/0")
        lock = distributed_lock("test-lock", blocking_timeout=0)
        with self.assertRaises(LockAcquisitionError):
            lock.acquire()


if __name__ == "__main__":
    unittest.main()
