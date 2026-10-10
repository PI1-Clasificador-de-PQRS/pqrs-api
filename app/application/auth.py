from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from app.application.ports import Clock, LoginRateLimiter, PasswordHasher, UnitOfWork
from app.domain.credentials import generate_session_token, hash_token
from app.domain.errors import DomainError, ErrorCode
from app.domain.principal import CredentialType, Principal

# Las marcas de "último uso" se actualizan como máximo una vez por minuto.
TOUCH_INTERVAL = timedelta(minutes=1)

INVALID_CREDENTIALS_MESSAGE = "Correo o contraseña incorrectos."


def _invalid_credentials() -> DomainError:
    # Mismo código y mismo mensaje para todo fallo de autenticación, para no revelar si el
    # usuario existe, está inactivo o su organización lo está.
    return DomainError(ErrorCode.INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE)


@dataclass(frozen=True, slots=True)
class MeData:
    user_id: UUID
    user_email: str
    user_name: str
    org_id: UUID
    org_name: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    me: MeData


class AuthService:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        hasher: PasswordHasher,
        limiter: LoginRateLimiter,
        clock: Clock,
        session_idle: timedelta,
    ) -> None:
        self._uow = uow
        self._hasher = hasher
        self._limiter = limiter
        self._clock = clock
        self._session_idle = session_idle

    def login(self, *, email: str, password: str, ip: str | None) -> LoginResult:
        if self._limiter.is_blocked(email=email, ip=ip):
            raise DomainError(
                ErrorCode.TOO_MANY_ATTEMPTS,
                "Demasiados intentos fallidos. Inténtalo de nuevo más tarde.",
            )

        user = self._uow.users.get_for_login(email)
        if user is None:
            # Se verifica contra un hash ficticio para igualar el tiempo de respuesta.
            self._hasher.verify_dummy(password)
            password_ok = False
        else:
            password_ok = self._hasher.verify(user.password_hash, password)

        org = self._uow.organizations.get(user.org_id) if user is not None else None
        if user is None or org is None or not password_ok or not user.active or not org.active:
            self._limiter.record_failure(email=email, ip=ip)
            raise _invalid_credentials()

        if self._hasher.needs_rehash(user.password_hash):
            self._uow.users.set_password_hash(user.org_id, user.id, self._hasher.hash(password))

        token = generate_session_token()
        self._uow.sessions.add(
            user.org_id,
            user_id=user.id,
            token_hash=hash_token(token),
            created_at=self._clock.now(),
        )
        self._uow.commit()
        self._limiter.record_success(email=email)
        return LoginResult(
            token=token,
            me=MeData(
                user_id=user.id,
                user_email=user.email,
                user_name=user.name,
                org_id=org.id,
                org_name=org.name,
            ),
        )

    def resolve_session(self, token: str) -> Principal:
        now = self._clock.now()
        session = self._uow.sessions.get_by_token_hash(hash_token(token))
        if session is None:
            raise DomainError(
                ErrorCode.SESSION_EXPIRED, "La sesión expiró. Inicia sesión de nuevo."
            )

        if now - session.last_seen_at > self._session_idle:
            self._uow.sessions.delete(session.org_id, session.id)
            self._uow.commit()
            raise DomainError(
                ErrorCode.SESSION_EXPIRED, "La sesión expiró. Inicia sesión de nuevo."
            )

        user = self._uow.users.get(session.org_id, session.user_id)
        org = self._uow.organizations.get(session.org_id)
        if user is None or not user.active or org is None or not org.active:
            self._uow.sessions.delete(session.org_id, session.id)
            self._uow.commit()
            raise _invalid_credentials()

        if now - session.last_seen_at >= TOUCH_INTERVAL:
            self._uow.sessions.touch(session.org_id, session.id, now)
            self._uow.commit()
        return Principal(
            org_id=session.org_id, credential_type=CredentialType.SESSION, user_id=user.id
        )

    def logout(self, org_id: UUID, token: str) -> None:
        session = self._uow.sessions.get_by_token_hash(hash_token(token))
        if session is not None and session.org_id == org_id:
            self._uow.sessions.delete(org_id, session.id)
            self._uow.commit()

    def get_me(self, principal: Principal) -> MeData:
        user = (
            self._uow.users.get(principal.org_id, principal.user_id)
            if principal.user_id is not None
            else None
        )
        org = self._uow.organizations.get(principal.org_id)
        if user is None or org is None:
            raise DomainError(
                ErrorCode.SESSION_EXPIRED, "La sesión expiró. Inicia sesión de nuevo."
            )
        return MeData(
            user_id=user.id,
            user_email=user.email,
            user_name=user.name,
            org_id=org.id,
            org_name=org.name,
        )
