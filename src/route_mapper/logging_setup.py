"""Configuración de logging para la CLI.

Toda la salida por terminal (progreso, resumen, errores) pasa por el módulo
``logging`` estándar; no se usa ``print``. El progreso de recolección se emite
en un nivel de auditoría propio (``AUDIT``, 25) que es visible por defecto pero
queda por debajo de ``WARNING``, de modo que ``-q`` lo silencia sin ocultar los
avisos importantes.
"""

from __future__ import annotations

import logging
import sys

#: Nivel para el flujo normal de recolección: por encima de INFO (para que sea
#: visible sin ``-v``) y por debajo de WARNING (para que ``-q`` lo silencie).
AUDIT = 25

logging.addLevelName(AUDIT, "AUDIT")

_LOGGER_NAME = "route_mapper"


def configure_logging(verbosity: int, *, quiet: bool = False) -> None:
    """Configura el logger raíz del paquete.

    :param verbosity: número de ``-v`` acumulados (0, 1, 2+).
    :param quiet: si es cierto, solo se muestran ``WARNING`` y superiores.
    """
    if quiet:
        level = logging.WARNING
    else:
        level = {0: AUDIT, 1: logging.INFO}.get(verbosity, logging.DEBUG)

    if verbosity >= 2:
        fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    elif verbosity == 1:
        fmt = "%(levelname)-7s %(name)s: %(message)s"
    else:
        # Progreso limpio por defecto: solo el mensaje.
        fmt = "%(message)s"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger(_LOGGER_NAME)
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def audit(logger: logging.Logger, msg: str, *args: object) -> None:
    """Emite un mensaje en el nivel ``AUDIT`` (flujo normal de recolección)."""
    logger.log(AUDIT, msg, *args)
