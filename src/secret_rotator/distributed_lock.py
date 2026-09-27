"""
Distributed locking for multi-instance safety.

Everything in this module exists to answer one question: "is anyone
else already doing this?" — across processes, not just threads. Two
operations need that answer today:

  - EncryptionManager.rotate_master_key() — two instances rotating the
    master key at once would race on the same key file and the same
    provider secret files.
  - RotationEngine.rotate_all_secrets() — previously guarded only by
    an in-process threading.Lock (RotationInProgressError), which
    stops two overlapping sweeps *within one process* but does nothing
    once you run more than one instance.

Backend: Redis, via redis-py's own Lock primitive (SET NX PX + a
per-holder token checked by a Lua script before DEL on release, so a
lock can never be released by anyone other than whoever acquired it).
This is the same mechanism redis-py's `Redis.lock()` context manager
uses internally — it isn't hand-rolled here.

Single-instance deployments don't need to run Redis at all:
`distributed.enabled: false` (the default) makes every lock in this
module fall back to a plain in-process threading.Lock, which is
exactly the behavior RotationEngine already had before Phase 3.

`distributed.enabled: true` with an unreachable Redis is a hard error
on acquire, never a silent fallback to "no coordination" — for
something guarding a master encryption key, a coordination backend
being down must surface as "the operation was refused," not as
"the operation ran unprotected."
"""

import os
import threading
from types import TracebackType
from typing import Any, Dict, Optional, Type

from secret_rotator.config.settings import settings
from secret_rotator.utils.logger import logger

try:
    import redis
    from redis.exceptions import LockError as _RedisLockError
    from redis.exceptions import RedisError as _RedisError

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by distributed.enabled=false path
    _REDIS_AVAILABLE = False


class LockAcquisitionError(Exception):
    """Raised when a named lock could not be acquired — either because
    it's currently held (elsewhere in this process, or by another
    instance), or because `distributed.enabled` is true and the Redis
    coordination backend could not be reached at all."""


class DistributedLock:
    """Cross-process mutual exclusion for a single named critical
    section. See module docstring for the backend selection rules.

    Not reentrant: acquiring the same name twice from the same
    thread/instance without releasing in between will block (or, for
    blocking_timeout=0 callers, raise) rather than nest.
    """

    # In-process fallback locks, keyed by name, shared across every
    # DistributedLock instance in this interpreter — so two separate
    # DistributedLock("master-key-rotation") objects still contend on
    # the same underlying lock rather than each getting their own.
    _local_locks: Dict[str, threading.Lock] = {}
    _local_locks_guard = threading.Lock()

    def __init__(self, name: str, timeout: float = 300.0, blocking_timeout: float = 10.0):
        """
        Args:
            name: Lock name; namespaced automatically under
                "secret-rotator:lock:" for Redis so it can't collide
                with unrelated keys if the Redis instance is shared.
            timeout: Seconds the lock is held before Redis expires it
                automatically. This is a safety net for a holder that
                crashes mid-operation without releasing — it should
                comfortably exceed the slowest realistic run of the
                protected operation, not be tuned tight. Ignored in
                local-fallback mode (a crashed process releases a
                threading.Lock immediately; there's nothing to expire).
            blocking_timeout: Seconds to wait trying to acquire before
                giving up. 0 means "try once, fail immediately if
                held" — used where the caller wants RotationEngine's
                existing fail-fast behavior rather than queuing.
        """
        self.name = f"secret-rotator:lock:{name}"
        self.timeout = timeout
        self.blocking_timeout = blocking_timeout
        # Typed Any: redis-py's Lock class isn't reliably importable as a
        # type when the optional `redis` extra isn't installed.
        self._redis_lock: Optional[Any] = None
        self._local_lock: Optional[threading.Lock] = None
        self._acquired = False

    @staticmethod
    def _distributed_enabled() -> bool:
        return bool(settings.get("distributed.enabled", False))

    @staticmethod
    def _get_redis_client():
        if not _REDIS_AVAILABLE:
            raise LockAcquisitionError(
                "distributed.enabled is true but the 'redis' package is not "
                "installed. Install it with: pip install secret-rotator[distributed]"
            )
        url = os.getenv("REDIS_URL") or settings.get("distributed.redis_url")
        if not url:
            raise LockAcquisitionError(
                "distributed.enabled is true but no Redis connection is "
                "configured. Set distributed.redis_url in config.yaml or "
                "the REDIS_URL environment variable."
            )
        # Short connect/socket timeouts so an unreachable Redis fails
        # fast (seconds) instead of hanging a CLI command or an HTTP
        # request indefinitely.
        return redis.Redis.from_url(
            url, socket_connect_timeout=5, socket_timeout=5, decode_responses=False
        )

    def acquire(self) -> None:
        """Acquire the lock, or raise LockAcquisitionError. Safe to
        call at most once per instance before a matching release()."""
        if self._distributed_enabled():
            client = self._get_redis_client()
            self._redis_lock = client.lock(
                self.name,
                timeout=self.timeout,
                blocking_timeout=self.blocking_timeout,
                thread_local=True,
            )
            try:
                acquired = self._redis_lock.acquire(blocking=True)
            except _RedisError as e:
                raise LockAcquisitionError(
                    f"Could not reach the Redis coordination backend for lock "
                    f"'{self.name}': {e}"
                ) from e
            if not acquired:
                raise LockAcquisitionError(
                    f"Lock '{self.name}' is held by another instance; "
                    f"gave up after {self.blocking_timeout}s."
                )
            self._acquired = True
            logger.info(f"Acquired distributed lock: {self.name}")
        else:
            with DistributedLock._local_locks_guard:
                lock = DistributedLock._local_locks.setdefault(self.name, threading.Lock())
            acquired = lock.acquire(timeout=self.blocking_timeout)
            if not acquired:
                raise LockAcquisitionError(
                    f"Lock '{self.name}' is already held in this process; "
                    f"gave up after {self.blocking_timeout}s."
                )
            self._local_lock = lock
            self._acquired = True
            logger.info(f"Acquired local lock: {self.name}")

    def release(self) -> None:
        """Release the lock if held. Safe to call even if acquire()
        was never called or already failed (no-op in that case)."""
        if not self._acquired:
            return
        try:
            if self._redis_lock is not None:
                try:
                    self._redis_lock.release()
                except _RedisLockError:
                    # The lock's `timeout` expired before we got here
                    # (operation ran longer than expected) and Redis
                    # may have already handed it to someone else —
                    # nothing safe to release. Surface it as a warning
                    # so an operator can raise `timeout` if this
                    # happens routinely, rather than let it fail
                    # silently.
                    logger.warning(
                        f"Lock '{self.name}' had already expired before release "
                        "(the protected operation ran longer than its lock "
                        "timeout); consider raising `timeout` for this lock."
                    )
            elif self._local_lock is not None:
                self._local_lock.release()
        finally:
            self._acquired = False

    @classmethod
    def reset_local_locks(cls) -> None:
        """Clear all in-process fallback locks.

        Test-only. The local-fallback registry is intentionally shared
        by name across every DistributedLock instance in this
        interpreter (see `_local_locks` above) — correct for
        production, where RotationEngine is a single long-lived
        singleton, but it means separate test cases that each create
        their own RotationEngine()/EncryptionManager() will otherwise
        contend on the SAME "rotation-sweep" / "master-key-rotation"
        lock across tests, exactly like the Flask-Limiter singleton
        needing `limiter.reset()` in setUp() (see tests/test_rate_limit.py).
        Call this in setUp() of any test that exercises rotate_all_secrets()
        or rotate_master_key().
        """
        with cls._local_locks_guard:
            cls._local_locks.clear()

    def __enter__(self) -> "DistributedLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.release()


def distributed_lock(
    name: str, timeout: float = 300.0, blocking_timeout: float = 10.0
) -> DistributedLock:
    """Convenience factory — see DistributedLock for parameter docs.

    Usage:
        with distributed_lock("master-key-rotation", timeout=600, blocking_timeout=5):
            ... critical section ...
    """
    return DistributedLock(name, timeout=timeout, blocking_timeout=blocking_timeout)
