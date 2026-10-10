from datetime import timedelta

from app.infrastructure.security.rate_limit import InMemoryLoginRateLimiter
from tests.fakes import FakeClock

WINDOW = timedelta(minutes=15)


def make(
    clock: FakeClock,
    *,
    max_attempts: int = 3,
    per_ip: int = 6,
    per_email: int = 1000,
    max_keys: int = 100,
) -> InMemoryLoginRateLimiter:
    return InMemoryLoginRateLimiter(
        max_attempts=max_attempts,
        max_attempts_per_ip=per_ip,
        max_attempts_per_email=per_email,
        window=WINDOW,
        max_keys=max_keys,
        clock=clock,
    )


def fail(limiter: InMemoryLoginRateLimiter, email: str, ip: str | None, times: int = 1) -> None:
    for _ in range(times):
        limiter.record_failure(email=email, ip=ip)


def test_pair_is_blocked_after_max_failures() -> None:
    limiter = make(FakeClock())
    for _ in range(3):
        assert not limiter.is_blocked(email="a@x.com", ip="1.1.1.1")
        limiter.record_failure(email="a@x.com", ip="1.1.1.1")
    assert limiter.is_blocked(email=" A@X.com ", ip="1.1.1.1")  # email normalizado


def test_attacker_from_another_ip_cannot_lock_out_the_real_user() -> None:
    limiter = make(FakeClock())
    fail(limiter, "victima@x.com", "6.6.6.6", times=3)

    assert limiter.is_blocked(email="victima@x.com", ip="6.6.6.6")  # el atacante, bloqueado
    assert not limiter.is_blocked(email="victima@x.com", ip="1.1.1.1")  # la víctima, no


def test_email_only_limit_is_a_high_backstop_against_distributed_attacks() -> None:
    limiter = make(FakeClock(), max_attempts=2, per_ip=100, per_email=10)
    for i in range(9):  # el mismo email desde 9 IP distintas: ni el par ni la IP llegan al límite
        fail(limiter, "a@x.com", f"9.9.9.{i}")
    assert not limiter.is_blocked(email="a@x.com", ip="1.2.3.4")
    fail(limiter, " A@x.com ", "9.9.9.99")  # el décimo fallo, normalizado
    assert limiter.is_blocked(email="a@x.com", ip="1.2.3.4")  # bloquea el email desde cualquier IP
    assert not limiter.is_blocked(email="otro@x.com", ip="1.2.3.4")


def test_successful_login_does_not_clear_the_email_counter() -> None:
    limiter = make(FakeClock(), max_attempts=100, per_ip=100, per_email=3)
    fail(limiter, "a@x.com", "1.1.1.1", times=3)
    limiter.record_success(email="a@x.com", ip="1.1.1.1")
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")


def test_ip_limit_applies_across_different_emails() -> None:
    limiter = make(FakeClock(), max_attempts=3, per_ip=6)
    for i in range(6):
        fail(limiter, f"u{i}@x.com", "9.9.9.9")  # ningún par llega a 3
    assert limiter.is_blocked(email="nuevo@x.com", ip="9.9.9.9")
    assert not limiter.is_blocked(email="nuevo@x.com", ip="8.8.8.8")


def test_missing_ip_shares_one_bucket() -> None:
    limiter = make(FakeClock(), per_ip=4)
    for i in range(4):
        fail(limiter, f"u{i}@x.com", None)
    assert limiter.is_blocked(email="otro@x.com", ip=None)
    assert not limiter.is_blocked(email="otro@x.com", ip="1.1.1.1")


def test_failures_expire_after_the_window() -> None:
    clock = FakeClock()
    limiter = make(clock)
    fail(limiter, "a@x.com", "1.1.1.1", times=3)
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")
    clock.advance(minutes=14, seconds=59)
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")
    clock.advance(seconds=2)
    assert not limiter.is_blocked(email="a@x.com", ip="1.1.1.1")


def test_window_slides_instead_of_resetting() -> None:
    clock = FakeClock()
    limiter = make(clock)
    fail(limiter, "a@x.com", "1.1.1.1")
    clock.advance(minutes=10)
    fail(limiter, "a@x.com", "1.1.1.1", times=2)
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")
    clock.advance(minutes=6)  # el primero ya salió de la ventana; quedan 2 fallos
    assert not limiter.is_blocked(email="a@x.com", ip="1.1.1.1")


def test_success_clears_the_pair_but_not_the_ip() -> None:
    limiter = make(FakeClock(), max_attempts=3, per_ip=3)
    fail(limiter, "a@x.com", "1.1.1.1", times=2)
    limiter.record_success(email="a@x.com", ip="1.1.1.1")
    assert not limiter.is_blocked(email="a@x.com", ip="2.2.2.2")

    fail(limiter, "b@x.com", "1.1.1.1")  # la IP conservaba 2 fallos: ahora 3
    assert limiter.is_blocked(email="c@x.com", ip="1.1.1.1")
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")  # por el contador de la IP


def test_success_does_not_touch_other_ips_of_the_same_email() -> None:
    limiter = make(FakeClock(), max_attempts=2, per_ip=100)
    fail(limiter, "a@x.com", "6.6.6.6", times=2)
    limiter.record_success(email="a@x.com", ip="1.1.1.1")  # login legítimo desde otra IP
    assert limiter.is_blocked(email="a@x.com", ip="6.6.6.6")


def test_memory_is_capped_and_oldest_keys_are_evicted_first() -> None:
    clock = FakeClock()
    limiter = make(clock, max_attempts=1, per_ip=100, max_keys=10)
    for i in range(50):
        clock.advance(seconds=1)
        fail(limiter, f"u{i}@x.com", f"10.0.0.{i}")
        assert limiter.size <= 10

    assert limiter.size == 10
    assert not limiter.is_blocked(email="u0@x.com", ip="10.0.0.0")  # la más antigua, desalojada
    assert limiter.is_blocked(email="u49@x.com", ip="10.0.0.49")  # la más reciente, conservada


def test_recently_active_keys_survive_eviction() -> None:
    clock = FakeClock()
    limiter = make(clock, max_attempts=2, per_ip=100, max_keys=6)
    fail(limiter, "a@x.com", "1.1.1.1")  # 2 claves
    for i in range(5):
        clock.advance(seconds=1)
        fail(limiter, f"u{i}@x.com", f"10.0.0.{i}")
        clock.advance(seconds=1)
        fail(limiter, "a@x.com", "1.1.1.1")  # a@x.com se renueva y no es la más antigua
    assert limiter.size <= 6
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")


def test_expired_entries_are_purged_on_insert() -> None:
    clock = FakeClock()
    limiter = make(clock)
    fail(limiter, "a@x.com", "1.1.1.1")
    fail(limiter, "b@x.com", "2.2.2.2")
    assert limiter.size == 6  # tres claves por fallo: par, IP y correo

    clock.advance(minutes=16)
    fail(limiter, "c@x.com", "3.3.3.3")  # inserta y purga las vencidas
    assert limiter.size == 3


def test_blocked_keys_are_not_purged_while_in_the_window() -> None:
    clock = FakeClock()
    limiter = make(clock)
    fail(limiter, "a@x.com", "1.1.1.1", times=3)
    clock.advance(minutes=10)
    fail(limiter, "b@x.com", "2.2.2.2")
    assert limiter.is_blocked(email="a@x.com", ip="1.1.1.1")
