"""Taxonomía del dominio.

Las categorías y su orden canónico salen de ``pqrs_classifier.categories`` (fuente única). Ese
módulo solo usa la biblioteca estándar, por eso el dominio puede importarlo.
"""

from enum import StrEnum

from pqrs_classifier.categories import CATEGORIES as _PACKAGE_CATEGORIES
from pqrs_classifier.categories import Category

__all__ = ["CATEGORIES", "ENGINES", "SOURCES", "Category", "Engine", "Source"]


class Engine(StrEnum):
    LLM = "llm"
    CLASSIFIER = "classifier"


class Source(StrEnum):
    UI = "ui"
    UI_BATCH = "ui_batch"
    API = "api"


CATEGORIES: tuple[str, ...] = tuple(c.value for c in _PACKAGE_CATEGORIES)
ENGINES: tuple[str, ...] = tuple(e.value for e in Engine)
SOURCES: tuple[str, ...] = tuple(s.value for s in Source)
