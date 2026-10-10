from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.application.auth import MeData


class LoginRequest(ApiSchema):
    email: str = Field(
        max_length=320, description="Correo del usuario (sin distinguir mayúsculas)."
    )
    password: str = Field(max_length=1024, description="Contraseña del usuario.")


class UserOut(ApiSchema):
    id: UUID
    email: str
    name: str


class OrganizationOut(ApiSchema):
    id: UUID
    name: str


class MeResponse(ApiSchema):
    user: UserOut
    organization: OrganizationOut


def to_me_response(me: MeData) -> MeResponse:
    return MeResponse(
        user=UserOut(id=me.user_id, email=me.user_email, name=me.user_name),
        organization=OrganizationOut(id=me.org_id, name=me.org_name),
    )
