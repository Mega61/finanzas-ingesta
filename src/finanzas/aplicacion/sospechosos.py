"""Los sospechosos de un cargo que no se entiende: junta las pistas y las
puntua con `dominio/sospechosos.py`.

Dos fuentes:

  - las facturas electronicas ya guardadas por el mismo monto (las de
    cualquier comercio, no solo supermercados: la de Corferias llega igual);
  - el buzon alrededor de la hora del cargo, leido en el momento y sin
    guardarlo. Solo Graph por ahora: es el buzon de Juan.

Nunca falla hacia afuera. Si Graph no responde, la pregunta sale igual, sin
sospechosos: una pista que no llego no puede tumbar la pregunta.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from finanzas import registro
from finanzas.adaptadores import graph
from finanzas.adaptadores.almacen import Almacen
from finanzas.dominio import sospechosos as dom
from finanzas.dominio.fechas import BOGOTA


def momento(p: Any) -> datetime | None:
    """La hora del cargo en Bogota, si la alerta la trajo."""
    if not p['fecha'] or not p['hora']:
        return None
    try:
        hora = str(p['hora'])[:5]
        return datetime.fromisoformat(f'{p["fecha"]}T{hora}').replace(tzinfo=BOGOTA)
    except ValueError:
        return None


def _facturas(alm: Almacen, p: Any) -> list[dom.FacturaDelMonto]:
    fuera = []
    for f in alm.facturas_del_monto(p['usuario_id'], p['valor'], str(p['fecha'])):
        nombres = [r['descripcion'] for r in alm.lineas_con_grupo(f['cufe'])]
        fuera.append(
            dom.FacturaDelMonto(
                proveedor=f['proveedor'] or f['nit'],
                nit=f['nit'],
                numero=f['numero'],
                resumen=', '.join(n for n in nombres[:2] if n),
            )
        )
    return fuera


def _correos(alm: Almacen, p: Any, cuando: datetime) -> list[dom.Correo]:
    b = alm.primer_buzon(p['usuario_id'])
    if not b or b['proveedor'] != 'graph':
        return []
    return graph.correos_entre(
        graph.token(),
        cuando - timedelta(minutes=dom.ANTES_MIN),
        cuando + timedelta(minutes=dom.DESPUES_MIN),
    )


def buscar(alm: Almacen, p: Any) -> list[dom.Sospechoso]:
    """Los sospechosos de un gasto, del mas fuerte al mas debil."""
    if float(p['valor']) >= 0:
        return []
    cuando = momento(p)
    try:
        facturas = _facturas(alm, p)
    except Exception as ex:
        registro.aviso(f'  sospechosos: no pude leer facturas de #{p["id"]}: {ex}')
        facturas = []
    correos: list[dom.Correo] = []
    if cuando is not None:
        try:
            correos = _correos(alm, p, cuando)
        except Exception as ex:
            registro.aviso(
                f'  sospechosos: no pude leer el buzon para #{p["id"]}: {ex}'
            )
    return dom.puntuar(p['valor'], cuando or datetime.now(BOGOTA), correos, facturas)
