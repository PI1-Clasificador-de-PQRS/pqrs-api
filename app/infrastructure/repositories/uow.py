from sqlalchemy.orm import Session

from app.infrastructure.repositories.api_keys import SqlApiKeyRepository
from app.infrastructure.repositories.classifications import SqlClassificationRepository
from app.infrastructure.repositories.organizations import SqlOrganizationRepository
from app.infrastructure.repositories.sessions import SqlSessionRepository
from app.infrastructure.repositories.users import SqlUserRepository


class SqlUnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.organizations = SqlOrganizationRepository(session)
        self.users = SqlUserRepository(session)
        self.sessions = SqlSessionRepository(session)
        self.api_keys = SqlApiKeyRepository(session)
        self.classifications = SqlClassificationRepository(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()
