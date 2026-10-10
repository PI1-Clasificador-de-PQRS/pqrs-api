"""Circuito de protección del LLM: apertura, semiabierto y cierre, con reloj controlado."""

from pqrs_classifier.engines.base import EngineError, EngineUnavailableError

from app.application.circuit_breaker import CircuitBreaker
from tests.fakes import FakeClock


def breaker(clock: FakeClock) -> CircuitBreaker:
    return CircuitBreaker(clock, failures=3, open_seconds=60)


def test_opens_after_three_consecutive_failures(clock: FakeClock) -> None:
    cb = breaker(clock)
    for _ in range(2):
        assert cb.acquire()
        cb.record_failure(EngineError())
    assert cb.allows()
    assert cb.acquire()
    cb.record_failure(EngineError())
    assert not cb.allows()
    assert not cb.acquire()
    assert cb.state == "open"
    assert cb.retry_after() == 60


def test_a_success_resets_the_failure_count(clock: FakeClock) -> None:
    cb = breaker(clock)
    for _ in range(2):
        cb.record_failure(EngineError())
    cb.record_success()
    for _ in range(2):
        cb.record_failure(EngineError())
    assert cb.allows()


def test_rate_limited_opens_immediately_and_respects_retry_after(clock: FakeClock) -> None:
    cb = breaker(clock)
    cb.record_failure(EngineUnavailableError(reason="rate_limited", retry_after=300))
    assert not cb.allows()
    assert cb.retry_after() == 300
    clock.advance(seconds=299)
    assert not cb.allows()
    clock.advance(seconds=2)
    assert cb.allows()


def test_open_for_at_least_sixty_seconds_even_with_a_small_retry_after(clock: FakeClock) -> None:
    cb = breaker(clock)
    cb.record_failure(EngineUnavailableError(reason="rate_limited", retry_after=5))
    assert cb.retry_after() == 60


def test_rate_limited_without_retry_after_uses_the_default(clock: FakeClock) -> None:
    cb = breaker(clock)
    cb.record_failure(EngineUnavailableError(reason="rate_limited"))
    assert cb.retry_after() == 60


def test_half_open_lets_a_single_probe_through(clock: FakeClock) -> None:
    cb = breaker(clock)
    cb.record_failure(EngineUnavailableError(reason="rate_limited"))
    clock.advance(seconds=61)
    assert cb.state == "half_open"
    assert cb.allows()  # consultar no consume el sondeo
    assert cb.allows()
    assert cb.acquire()
    assert not cb.acquire()  # el sondeo está en curso
    assert not cb.allows()


def test_half_open_probe_success_closes_the_circuit(clock: FakeClock) -> None:
    cb = breaker(clock)
    cb.record_failure(EngineUnavailableError(reason="rate_limited"))
    clock.advance(seconds=61)
    assert cb.acquire()
    cb.record_success()
    assert cb.state == "closed"
    assert cb.acquire()
    assert cb.retry_after() is None


def test_half_open_probe_failure_reopens_the_circuit(clock: FakeClock) -> None:
    cb = breaker(clock)
    cb.record_failure(EngineUnavailableError(reason="rate_limited"))
    clock.advance(seconds=61)
    assert cb.acquire()
    cb.record_failure(EngineError())  # un solo fallo basta en el sondeo
    assert not cb.allows()
    assert cb.retry_after() == 60
    clock.advance(seconds=61)
    assert cb.allows()


def test_other_unavailable_reasons_count_as_ordinary_failures(clock: FakeClock) -> None:
    cb = breaker(clock)
    for _ in range(2):
        cb.record_failure(EngineUnavailableError(reason="missing_dependency"))
    assert cb.allows()
    cb.record_failure(EngineUnavailableError(reason="missing_dependency"))
    assert not cb.allows()
