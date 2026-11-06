"""Corregir la categoria de una compra con lo que dice su factura.

La regla de EXITO SABANETA dice Mercado, y casi siempre acierta. Cuando la
factura de esa compra dice otra cosa -- un «Menu Casero», unas crispetas y
una gaseosa -- se corrige en Firefly y se cuenta. La decision de que es cada
factura vive en `dominio/por_factura.py`; aqui solo se cruza y se escribe.

Nunca se toca lo que la persona contesto: su respuesta gana sobre cualquier
factura. Y una compra con dos facturas posibles del mismo monto no se adivina.
"""

from __future__ import annotations

import contextlib
from typing import Any

from finanzas import registro
from finanzas.adaptadores import db, firefly
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import presupuestos
from finanzas.dominio import por_factura

DIAS = 7

# Los comercios donde una compra mezclada se pregunta en vez de quedarse con
# la regla del comercio. En Farmatodo la regla dice Mercado, y lo mezclado es
# pastillas, mecato y cuidado personal: casi nunca mercado.
PREGUNTAR_MEZCLA = frozenset({'830129327'})  # Farmatodo


def _presupuesto(categoria: str, cx: Any = None) -> str | None:
    """El presupuesto de la categoria: el que dijo la persona o el codigo, el
    seguro, o el que mas lleva. Sin lo primero, una compra que la factura
    pasaba a Medicamentos se quedaba en el presupuesto de Mercado."""
    with contextlib.suppress(Exception):
        dicho = presupuestos.presupuesto_de_categoria(categoria, cx)
        if dicho:
            return dicho
    try:
        info = presupuestos.mapa_categoria().get(categoria)
    except Exception as ex:
        registro.aviso(f'  sin mapa de presupuestos: {ex}')
        return None
    if not info:
        return None
    if info.get('seguro'):
        return info['presupuesto']
    reparto = info.get('reparto') or {}
    return max(reparto, key=reparto.get) if reparto else None


def lectura(alm: Almacen, p: Any) -> tuple[str, str, Any] | None:
    """(categoria, razon, factura) si la factura de `p` dice otra categoria."""
    candidatas = alm.facturas_del_monto(p['usuario_id'], p['valor'], str(p['fecha']))
    if len(candidatas) != 1:
        return None
    f = candidatas[0]
    lineas = [
        por_factura.Linea(
            r['descripcion'] or '', r['grupo'], r['categoria'], r['total'] or 0
        )
        for r in alm.lineas_con_grupo(f['cufe'])
    ]
    d = por_factura.categoria(lineas)
    if d is None or d[0] == p['categoria']:
        return None
    return d[0], d[1], f


def revisar(cx: Any, dias: int = DIAS, dry_run: bool = False) -> list[dict[str, Any]]:
    """Corrige las compras recientes cuya factura dice otra categoria.
    Devuelve lo que cambio, para contarselo a cada quien."""
    alm = Almacen(cx)
    cambios = []
    for p in alm.compras_por_revisar_con_factura(dias):
        d = lectura(alm, p)
        if d is None:
            continue
        categoria, razon, f = d
        presupuesto = _presupuesto(categoria, cx)
        cambio = {
            'pendiente_id': p['id'],
            'usuario_id': p['usuario_id'],
            'fecha': p['fecha'],
            'valor': p['valor'],
            'contraparte': p['contraparte'],
            'antes': p['categoria'],
            'categoria': categoria,
            'presupuesto': presupuesto,
            'razon': razon,
            'factura': f['numero'],
        }
        if dry_run:
            cambios.append(cambio)
            continue
        campos = {'category_name': categoria}
        if presupuesto:
            campos['budget_name'] = presupuesto
        try:
            firefly.actualizar_split(str(p['firefly_id']), **campos)
        except firefly.ApiError as ex:
            registro.aviso(f'  no pude corregir #{p["id"]} con su factura: {ex}')
            continue
        alm.actualizar_pendiente(
            p['id'],
            categoria=categoria,
            presupuesto=presupuesto,
            decidido_por='factura',
        )
        db.bitacora(
            cx,
            'por_factura',
            usuario_id=p['usuario_id'],
            pendiente_id=p['id'],
            firefly_id=p['firefly_id'],
            payload={**campos, 'factura': f['numero'], 'razon': razon},
            ok=True,
        )
        cx.commit()
        cambios.append(cambio)
    return cambios


def mezclas_por_preguntar(cx: Any, dias: int = DIAS) -> list[dict[str, Any]]:
    """Las compras recientes de un comercio de `PREGUNTAR_MEZCLA` cuya factura
    es mezclada: no hay una categoria clara, y la regla del comercio no es
    respuesta. Una vez por compra; contestada, ya no entra."""
    alm = Almacen(cx)
    salida = []
    for p in alm.compras_por_revisar_con_factura(dias):
        if alm.ya_anotado(p['id'], 'por_factura_mezcla'):
            continue
        candidatas = alm.facturas_del_monto(
            p['usuario_id'], p['valor'], str(p['fecha'])
        )
        if len(candidatas) != 1 or candidatas[0]['nit'] not in PREGUNTAR_MEZCLA:
            continue
        f = candidatas[0]
        lineas = [
            por_factura.Linea(
                r['descripcion'] or '', r['grupo'], r['categoria'], r['total'] or 0
            )
            for r in alm.lineas_con_grupo(f['cufe'])
        ]
        if por_factura.categoria(lineas) is not None:
            continue
        opciones = por_factura.mezcla(lineas)
        if len(opciones) < 2:
            continue
        salida.append({'pendiente': p, 'factura': f, 'opciones': opciones})
    return salida


def presupuesto_de(categoria: str, cx: Any = None) -> str | None:
    return _presupuesto(categoria, cx)
