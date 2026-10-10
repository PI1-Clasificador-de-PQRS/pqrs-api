import ipaddress


def resolve_client_ip(forwarded_for: str | None, peer: str | None, hops: int) -> str | None:
    """IP del cliente a efectos del límite de intentos de login.

    ``forwarded_for`` es el valor (ya unido, si llegaron varias líneas) de ``X-Forwarded-For`` y
    ``peer`` la IP de la conexión TCP. ``hops`` es el número de proxies de confianza que agregan
    una entrada a la cabecera entre el cliente y esta API:

    * ``0`` (por defecto): se ignora la cabecera y se usa ``peer``. Quien llama puede escribir lo
      que quiera en ella.
    * ``N > 0``: se toma la entrada N contando desde la derecha, es decir, la que agregó el proxy
      de confianza más externo. Las entradas a su izquierda las controla quien llama y se ignoran.

    Si la cabecera tiene menos de N entradas, o la entrada elegida está vacía o no es una IP, se
    usa ``peer``. Nunca lanza.
    """
    if hops <= 0 or not forwarded_for:
        return peer
    entries = [entry.strip() for entry in forwarded_for.split(",")]
    if len(entries) < hops:
        return peer
    try:
        # Se normaliza para que "::1" y "0:0:0:0:0:0:0:1" cuenten como la misma IP.
        return str(ipaddress.ip_address(entries[-hops]))
    except ValueError:
        return peer
