"""La taxonomía de la API sale del paquete y coincide con el CHECK de la base."""

import re

from pqrs_classifier.categories import CATEGORIES as PACKAGE_CATEGORIES
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.categories import CATEGORIES, Category


def test_domain_categories_come_from_the_package_in_canonical_order() -> None:
    assert tuple(c.value for c in PACKAGE_CATEGORIES) == CATEGORIES
    assert list(Category) == list(PACKAGE_CATEGORIES)


def test_database_check_matches_the_package(db_session: Session) -> None:
    definition = db_session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_classifications_category'"
        )
    ).scalar_one()
    values = set(re.findall(r"'([^']+)'", definition))
    assert values == {c.value for c in PACKAGE_CATEGORIES}
