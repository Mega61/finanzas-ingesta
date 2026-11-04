"""Dos correos, un solo movimiento: el avance y su «recibiste». Logica pura.

Un avance desde la tarjeta a la cuenta propia manda DOS alertas al mismo
minuto (noviembre de 2026, la plantilla nueva):

    Hiciste un avance de $550,000 ... desde tu T.Credito *9919 a la cuenta *5788
    Recibiste una transferencia por $550,000 de MARIANA GARCIA en tu cuenta **5788

El primero ya es el traslado completo (sube la deuda de la tarjeta y mete la
plata en la cuenta). El segundo es la otra pata vista desde la cuenta, y si
entra como ingreso son 550.000 que nadie gano. El 3 de noviembre fueron tres
avances: 1,3 millones de ingreso falso esperando a que se les eligiera libro.

Y el orden no es fijo: ese dia el «recibiste» de los 600.000 llego un minuto
ANTES que su avance. Por eso se mira en las dos direcciones.
"""

from __future__ import annotations

from typing import Any

from finanzas.dominio.dinero import mismo_monto

MINUTOS = 5


def _minutos(hora: str | None) -> int | None:
    if not hora:
        return None
    try:
        h, m = str(hora).split(':')[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def es_espejo(avance: Any, entrada: Any) -> bool:
    """Si `entrada` (una transferencia recibida) es la otra pata de `avance`.

    Los dos aceptan un dict o una fila: se leen por clave.
    """
    if avance['tipo'] != 'avance' or entrada['tipo'] != 'transferencia_entrada':
        return False
    if str(avance['traslado_a'] or '') != str(entrada['instrumento'] or ''):
        return False
    if str(avance['fecha']) != str(entrada['fecha']):
        return False
    if not mismo_monto(abs(float(avance['valor'])), abs(float(entrada['valor'])), 1):
        return False
    a, e = _minutos(avance['hora']), _minutos(entrada['hora'])
    return a is not None and e is not None and abs(a - e) <= MINUTOS
