from datetime import datetime
from uuid import UUID

from app.api.schemas.common import ApiSchema
from app.domain.categories import Category, Engine, Source


class ClassificationOut(ApiSchema):
    id: UUID
    created_at: datetime
    text_anonymized: str
    category: Category
    model: Engine
    model_version: str
    source: Source
    batch_id: UUID | None
    external_ref: str | None


class ClassificationPage(ApiSchema):
    items: list[ClassificationOut]
    total: int
    page: int
    page_size: int
