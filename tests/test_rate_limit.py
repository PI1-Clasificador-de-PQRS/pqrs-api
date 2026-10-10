from datetime import timedelta

from app.infrastructure.security.rate_limit import InMemoryLoginRateLimiter
from tests.fakes import FakeClock


def make(clock: FakeClock, max_attempts: int = 3) -> InMemoryLoginRateLimiter:
    return InMemoryLoginRateLimiter(
        max_attempts=max_attempts, window=timedelta(minutes=15), clock=clock
    )


def test_blocks_after_max_failures_for_the_same_email() -> None:
    limiter = make(FakeClock())
    for _ in range(3):
        assert not limiter.is_blocked(email="a@x.com", ip="1.1.1.1")
        limiter.record_failure(email="a@x.com", ip="1.1.1.1")
    assert limiter.is_blocked(email="A@X.com", ip="2.2.2.2")  # email normalizado


def test_blocks_by_ip_across_different_emails() -> None:
    limiter = make(FakeClock())
    for i in range(3):
        limiter.record_failure(email=f"u{i}@x.com", ip="9.9.9.9")
    assert limiter.is_blocked(email="nuevo@x.com", ip="9.9.9.9")
    assert not limiter.is_blocked(email="nuevo@x.com", ip="8.8.8.8")


def test_failures_expire_after_the_window() -> None:
    clock = FakeClock()
    limiter = make(clock)
    for _ in range(3):
        limiter.record_failure(email="a@x.com", ip=None)
    assert limiter.is_blocked(email="a@x.com", ip=None)
    clock.advance(minutes=15, seconds=1)
    assert not limiter.is_blocked(email="a@x.com", ip=None)


def test_success_clears_email_counter_but_not_ip() -> None:
    limiter = make(FakeClock())
    for _ in range(2):
        limiter.record_failure(email="a@x.com", ip="1.1.1.1")
    limiter.record_success(email="a@x.com")
    limiter.record_failure(email="a@x.com", ip="1.1.1.1")
    assert not limiter.is_blocked(email="a@x.com", ip=None)  # email reiniciado: 1 fallo
    assert limiter.is_blocked(email="otro@x.com", ip="1.1.1.1")  # la IP sigue con 3


def test_old_entries_are_swept() -> None:
    clock = FakeClock()
    limiter = make(clock)
    limiter.record_failure(email="a@x.com", ip="1.1.1.1")
    clock.advance(minutes=20)
    limiter.is_blocked(email="b@x.com", ip=None)  # dispara el barrido
    assert limiter._failures == {}
