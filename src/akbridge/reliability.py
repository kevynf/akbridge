"""Local reliability primitives for AKBridge provider calls.

All components are synchronous and thread-safe so they can be used from the
MCP server's ``asyncio.to_thread`` boundary and from the acceptance workers.
They deliberately have no network or LLM dependency.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

LOGGER = logging.getLogger("akbridge")
_SECRET_RE = re.compile(
    r"token|secret|password|passwd|api[_-]?key|authorization|cookie|credential", re.I
)


class CircuitOpenError(RuntimeError):
    """Raised when a provider is temporarily blocked by its circuit breaker."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay: float = 0.25
    multiplier: float = 2.0
    max_delay: float = 5.0
    retry_exceptions: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError, OSError)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay < 0 or self.multiplier < 1 or self.max_delay < 0:
            raise ValueError("retry delays and multiplier must be non-negative")

    def should_retry(self, exc: BaseException, attempt: int) -> bool:
        return attempt < self.max_attempts and isinstance(exc, self.retry_exceptions)

    def delay_for(self, attempt: int) -> float:
        # ``attempt`` is one-based for the failed attempt.
        return min(self.max_delay, self.initial_delay * (self.multiplier ** max(0, attempt - 1)))

    @classmethod
    def from_env(cls) -> RetryPolicy:
        def number(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        try:
            attempts = int(os.getenv("AKBRIDGE_MAX_ATTEMPTS", "2"))
        except ValueError:
            attempts = 2
        return cls(
            max_attempts=max(1, attempts),
            initial_delay=max(0.0, number("AKBRIDGE_RETRY_INITIAL_DELAY", 0.25)),
            multiplier=max(1.0, number("AKBRIDGE_RETRY_MULTIPLIER", 2.0)),
            max_delay=max(0.0, number("AKBRIDGE_RETRY_MAX_DELAY", 5.0)),
        )


class RateLimiter:
    """A simple process-local minimum-interval limiter."""

    def __init__(
        self, min_interval: float = 0.0, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if min_interval < 0:
            raise ValueError("min_interval must be non-negative")
        self.min_interval = float(min_interval)
        self._clock = clock
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> float:
        if self.min_interval <= 0:
            return 0.0
        with self._lock:
            now = self._clock()
            sleep_for = max(0.0, self._last + self.min_interval - now)
            if sleep_for:
                time.sleep(sleep_for)
                now = self._clock()
            self._last = now
            return sleep_for


class TTLCache:
    """Bounded, thread-safe TTL cache with deterministic JSON keys."""

    def __init__(
        self, *, maxsize: int = 256, ttl: float = 0.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        if ttl < 0:
            raise ValueError("ttl must be non-negative")
        self.maxsize = int(maxsize)
        self.ttl = float(ttl)
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def key(name: str, arguments: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            arguments, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        )
        return hashlib.sha256(f"{name}\0{encoded}".encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        if self.ttl <= 0:
            return None
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= self._clock():
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return value

    def get_with_hit(self, key: str) -> tuple[bool, Any]:
        """Return a hit flag so cached ``None`` is distinguishable from a miss."""
        if self.ttl <= 0:
            return False, None
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return False, None
            expires, value = item
            if expires <= self._clock():
                self._items.pop(key, None)
                return False, None
            self._items.move_to_end(key)
            return True, value

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            self._items[key] = (self._clock() + self.ttl, value)
            self._items.move_to_end(key)
            while len(self._items) > self.maxsize:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class CircuitBreaker:
    """Failure-count circuit breaker with a half-open recovery probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be non-negative")
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout = float(recovery_timeout)
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if self._clock() - self._opened_at >= self.recovery_timeout:
                return "half_open"
            return "open"

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if self._clock() - self._opened_at < self.recovery_timeout:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._probe_in_flight = False
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = self._clock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            opened_at = self._opened_at
            failures = self._failures
        return {"state": self.state, "failures": failures, "opened_at": opened_at}


@dataclass(slots=True)
class CallMetrics:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    retries: int = 0
    cache_hits: int = 0
    blocked: int = 0
    total_seconds: float = 0.0
    by_api: dict[str, dict[str, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, name: str, *, outcome: str, duration: float, retries: int = 0) -> None:
        with self._lock:
            self.calls += 1
            self.total_seconds += duration
            self.retries += retries
            if outcome == "success":
                self.successes += 1
            elif outcome == "cache_hit":
                self.cache_hits += 1
            elif outcome == "blocked":
                self.blocked += 1
            else:
                self.failures += 1
            bucket = self.by_api.setdefault(name, {"calls": 0, "successes": 0, "failures": 0})
            bucket["calls"] += 1
            bucket["successes" if outcome in {"success", "cache_hit"} else "failures"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "successes": self.successes,
                "failures": self.failures,
                "retries": self.retries,
                "cache_hits": self.cache_hits,
                "blocked": self.blocked,
                "total_seconds": round(self.total_seconds, 6),
                "by_api": {key: dict(value) for key, value in self.by_api.items()},
            }


def redact_secrets(value: Any, *, key_hint: str = "") -> Any:
    """Recursively redact credentials before logging or returning diagnostics."""
    if _SECRET_RE.search(key_hint):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(key): redact_secrets(item, key_hint=str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_secrets(item, key_hint=key_hint) for item in value]
    if isinstance(value, str) and _SECRET_RE.search(value):
        return "[REDACTED]"
    return value


def proxy_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child-process environment with optional AKBridge proxy aliases."""
    result = dict(environ or os.environ)
    aliases = {
        "AKBRIDGE_HTTP_PROXY": "HTTP_PROXY",
        "AKBRIDGE_HTTPS_PROXY": "HTTPS_PROXY",
        "AKBRIDGE_ALL_PROXY": "ALL_PROXY",
        "AKBRIDGE_NO_PROXY": "NO_PROXY",
    }
    for source, target in aliases.items():
        value = result.get(source)
        if value:
            result[target] = value
    return result


def is_read_only_api(name: str, *, side_effect: bool = False) -> bool:
    if side_effect:
        return False
    lowered = name.casefold()
    return not bool(
        re.search(
            r"(^|_)(set|login|logout|delete|create|update|submit|upload|config)(_|$)", lowered
        )
    )


class FallbackRegistry:
    """Explicit API-to-callable fallbacks for semantically compatible sources.

    AKBridge intentionally does not guess that a different AKShare function is
    interchangeable. Deployments may register a reviewed fallback when they
    know two providers return the same contract.
    """

    def __init__(self, fallbacks: Mapping[str, Callable[..., Any]] | None = None) -> None:
        self._fallbacks = dict(fallbacks or {})
        self._lock = threading.RLock()

    def register(self, api_name: str, function: Callable[..., Any]) -> None:
        with self._lock:
            self._fallbacks[api_name] = function

    def get(self, api_name: str) -> Callable[..., Any] | None:
        with self._lock:
            return self._fallbacks.get(api_name)

    def names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._fallbacks))


class CallExecutor:
    """Execute provider functions with retries, cache, rate limiting and fallback."""

    def __init__(
        self,
        *,
        retry_policy: RetryPolicy | None = None,
        cache: TTLCache | None = None,
        rate_limiter: RateLimiter | None = None,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        fallbacks: Mapping[str, Callable[..., Any]] | FallbackRegistry | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        self.retry_policy = retry_policy or RetryPolicy.from_env()
        self.cache = cache
        self.rate_limiter = rate_limiter or RateLimiter()
        self.fallbacks = (
            fallbacks if isinstance(fallbacks, FallbackRegistry) else FallbackRegistry(fallbacks)
        )
        self.sleeper = sleeper
        self.logger = logger or LOGGER
        self.metrics = CallMetrics()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_config = (failure_threshold, recovery_timeout)
        self._lock = threading.Lock()

    def breaker(self, name: str) -> CircuitBreaker:
        with self._lock:
            breaker = self._breakers.get(name)
            if breaker is None:
                breaker = CircuitBreaker(
                    failure_threshold=self._breaker_config[0],
                    recovery_timeout=self._breaker_config[1],
                )
                self._breakers[name] = breaker
            return breaker

    def register_fallback(self, api_name: str, function: Callable[..., Any]) -> None:
        """Register a reviewed, semantically compatible fallback implementation."""
        self.fallbacks.register(api_name, function)

    def call(
        self,
        name: str,
        function: Callable[..., Any],
        arguments: Mapping[str, Any] | None = None,
        *,
        cacheable: bool = True,
        side_effect: bool = False,
    ) -> Any:
        args = dict(arguments or {})
        read_only = cacheable and is_read_only_api(name, side_effect=side_effect)
        cache_key = TTLCache.key(name, args) if self.cache is not None and read_only else None
        started = time.monotonic()
        if cache_key is not None:
            hit, cached = self.cache.get_with_hit(cache_key)
            if hit:
                self.metrics.record(name, outcome="cache_hit", duration=time.monotonic() - started)
                return cached

        breaker = self.breaker(name)
        if not breaker.allow():
            self.metrics.record(name, outcome="blocked", duration=time.monotonic() - started)
            raise CircuitOpenError(f"circuit open for {name}")

        retries = 0
        current = function
        try:
            attempts = self.retry_policy.max_attempts if read_only else 1
            for attempt in range(1, attempts + 1):
                self.rate_limiter.wait()
                try:
                    result = current(**args)
                except Exception as exc:
                    if read_only and self.retry_policy.should_retry(exc, attempt):
                        retries += 1
                        delay = self.retry_policy.delay_for(attempt)
                        self.logger.warning(
                            "akbridge_call_retry api=%s attempt=%s delay=%.3f error=%s",
                            name,
                            attempt,
                            delay,
                            type(exc).__name__,
                        )
                        self.sleeper(delay)
                        continue
                    fallback = self.fallbacks.get(name)
                    if read_only and fallback is not None and current is not fallback:
                        current = fallback
                        retries += 1
                        self.rate_limiter.wait()
                        result = current(**args)
                    else:
                        raise
                breaker.record_success()
                if cache_key is not None:
                    self.cache.set(cache_key, result)
                self.metrics.record(
                    name, outcome="success", duration=time.monotonic() - started, retries=retries
                )
                return result
        except Exception:
            breaker.record_failure()
            self.metrics.record(
                name, outcome="failure", duration=time.monotonic() - started, retries=retries
            )
            raise
        raise RuntimeError("unreachable call executor state")
