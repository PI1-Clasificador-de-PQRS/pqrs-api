"""Comprobación del NER de nombres que usa ``anonymize()``."""

import warnings

from pqrs_classifier.preprocessing import anonymize

_PROBE = "Mi nombre es María Fernanda López Rojas y vivo en Flandes."


def check_ner_available() -> bool:
    """¿Cargó el modelo de spaCy? Debe llamarse al arrancar, antes de cualquier ``anonymize``.

    Sin spaCy o sin ``es_core_news_sm``, ``anonymize`` cae a solo regex y avisa con un
    ``RuntimeWarning``... una única vez por proceso (el resultado de la carga queda en caché),
    así que este es el momento de detectarlo. La frase de prueba no es un dato real.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        anonymize(_PROBE)
    return not any(issubclass(w.category, RuntimeWarning) for w in caught)
