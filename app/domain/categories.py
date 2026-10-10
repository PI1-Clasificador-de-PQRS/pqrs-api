"""Taxonomía del dominio.

Las categorías y su orden canónico deben coincidir con ``pqrs_classifier.categories``
(en la parte 2 se importarán del paquete en lugar de redefinirse aquí).
"""

from enum import StrEnum


class Category(StrEnum):
    PETICION = "Petición"
    RECLAMO = "Reclamo"
    DENUNCIA = "Denuncia"
    SOLICITUD_INFORMACION = "Solicitud de información"


class Engine(StrEnum):
    LLM = "llm"
    CLASSIFIER = "classifier"


class Source(StrEnum):
    UI = "ui"
    UI_BATCH = "ui_batch"
    API = "api"


CATEGORIES: tuple[str, ...] = tuple(c.value for c in Category)
ENGINES: tuple[str, ...] = tuple(e.value for e in Engine)
SOURCES: tuple[str, ...] = tuple(s.value for s in Source)
