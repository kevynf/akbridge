from __future__ import annotations

import pytest

from akbridge.catalog import ApiFunction, signature_to_schema
from akbridge.reliability import (
    CallExecutor,
    CircuitBreaker,
    RetryPolicy,
    TTLCache,
    redact_secrets,
)
from akbridge.server import invoke_api


def test_executor_retries_and_caches_read_only_calls() -> None:
    calls = {"count": 0}

    def provider(**_: object) -> dict[str, int]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("temporary")
        return {"value": 42}

    executor = CallExecutor(
        retry_policy=RetryPolicy(max_attempts=2, initial_delay=0),
        cache=TTLCache(ttl=60),
        sleeper=lambda _: None,
    )

    assert executor.call("sample", provider, {"symbol": "000001"}) == {"value": 42}
    assert executor.call("sample", provider, {"symbol": "000001"}) == {"value": 42}
    assert calls["count"] == 2
    assert executor.metrics.snapshot()["cache_hits"] == 1


def test_cache_can_store_none_results() -> None:
    calls = {"count": 0}

    def provider(**_: object) -> None:
        calls["count"] += 1
        return None

    executor = CallExecutor(cache=TTLCache(ttl=60), retry_policy=RetryPolicy(max_attempts=1))

    assert executor.call("sample", provider, {}) is None
    assert executor.call("sample", provider, {}) is None
    assert calls["count"] == 1


def test_side_effect_calls_are_not_cached() -> None:
    calls = {"count": 0}

    def provider(**_: object) -> int:
        calls["count"] += 1
        return calls["count"]

    executor = CallExecutor(cache=TTLCache(ttl=60), retry_policy=RetryPolicy(max_attempts=1))

    assert executor.call("set_token", provider, {"token": "secret"}, side_effect=True) == 1
    assert executor.call("set_token", provider, {"token": "secret"}, side_effect=True) == 2
    assert calls["count"] == 2


def test_side_effect_failures_are_never_retried_or_fallen_back() -> None:
    calls = {"count": 0}

    def provider(**_: object) -> None:
        calls["count"] += 1
        raise TimeoutError("do not replay")

    executor = CallExecutor(
        retry_policy=RetryPolicy(max_attempts=3, initial_delay=0), sleeper=lambda _: None
    )
    executor.register_fallback("set_token", lambda **_: None)

    with pytest.raises(TimeoutError):
        executor.call("set_token", provider, {"token": "secret"}, side_effect=True)
    assert calls["count"] == 1


def test_explicit_fallback_runs_only_after_primary_failure() -> None:
    def primary(**_: object) -> int:
        raise RuntimeError("provider unavailable")

    executor = CallExecutor(retry_policy=RetryPolicy(max_attempts=1))
    executor.register_fallback("sample", lambda **_: 7)

    assert executor.call("sample", primary, {}) == 7


def test_circuit_breaker_blocks_until_recovery() -> None:
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
    assert breaker.allow()
    breaker.record_failure()
    assert not breaker.allow()
    assert breaker.state == "open"


def test_redaction_never_leaks_secret_values() -> None:
    payload = redact_secrets({"token": "abc", "nested": {"password": "xyz"}, "value": "plain"})

    assert payload == {
        "token": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
        "value": "plain",
    }


def test_sensitive_helper_result_is_redacted_at_mcp_boundary() -> None:
    def get_token() -> str:
        return "actual-secret"

    api = ApiFunction(
        name="get_token",
        function=get_token,
        description="token helper",
        input_schema=signature_to_schema(get_token),
        signature="() -> str",
        side_effect=True,
    )

    result = invoke_api(
        api, {}, executor=CallExecutor(retry_policy=RetryPolicy(max_attempts=1)), row_limit=10
    )

    assert result == "[REDACTED]"
