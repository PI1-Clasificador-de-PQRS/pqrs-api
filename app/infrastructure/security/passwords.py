from argon2 import PasswordHasher as Argon2
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    """argon2id con los parámetros por defecto de argon2-cffi."""

    def __init__(self, hasher: Argon2 | None = None) -> None:
        self._hasher = hasher or Argon2()
        # Se calcula una vez al arrancar; sirve para igualar tiempos de respuesta.
        self._dummy_hash = self._hasher.hash("contraseña-ficticia-para-igualar-tiempos")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)

    def verify_dummy(self, password: str) -> None:
        self.verify(self._dummy_hash, password)
