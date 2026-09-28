"""Escribe movimientos en un libro de Actual Budget.

Las mismas redes que el publicador de Firefly, con dos diferencias que salen
de como se usa este libro en la vida real:

1. **Agendapro ya escribe las ventas.** El CRM sube cada noche los pagos de
   las clientas como ingresos «Servicios», con `imported_id = agendapro-tx:N`.
   Una transferencia que llega a la cuenta y que ES una de esas ventas no se
   vuelve a escribir: se enlaza con la que ya esta. Si no se enlazara, cada
   venta pagada por transferencia contaria dos veces.

2. **Un parecido no se descarta solo: se pregunta.** En un salon es normal
   comprar dos veces lo mismo el mismo dia. Descartar el segundo en silencio
   perderia un gasto real; escribirlo sin mirar duplicaria uno que ella ya
   anoto a mano. Se devuelve 'parecido' y el bot le pregunta.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from finanzas.adaptadores import actual, db
from finanzas.dominio import fechas

# Cuanto se parecen dos montos para llamarlos «el mismo». Es el 1% del cruce
# contra extractos, con un piso de un peso.
TOLERANCIA = 0.01
DIAS = 2


def _parece(a: int, b: int) -> bool:
    return abs(a - b) <= max(abs(b) * TOLERANCIA, 100)


def _guardas(p: Any, libro: Any) -> tuple[str, str] | None:
    if not p['libro_id'] or not p['destino_por']:
        return 'sin_destino', 'espera a que la persona elija el libro'
    if libro['id'] != p['libro_id'] or libro['usuario_id'] != p['usuario_id']:
        return (
            'error',
            f'el movimiento es del libro {p["libro_id"]}, no del {libro["id"]}',
        )
    if not p['cuenta_firefly']:
        return 'error', 'sin cuenta resuelta en este libro'
    if not p['fecha']:
        return 'error', 'sin fecha'
    return None


def venta_de_agendapro(cliente: actual.Cliente, p: Any) -> dict[str, Any] | None:
    """La venta de Agendapro que es esta transferencia, si hay UNA sola.

    Mismo monto exacto -- la clienta transfiere lo que se le cobro, con la
    propina incluida, y eso es lo que Agendapro sube -- y el mismo dia o el
    siguiente. Con dos candidatas no se elige: se pregunta.
    """
    cuenta = cliente.cuenta_id(p['cuenta_firefly'])
    f = fechas.a_fecha(p['fecha'])
    if not cuenta or not f or float(p['valor']) <= 0:
        return None
    monto = actual.centavos(p['valor'])
    candidatas = [
        t
        for t in cliente.transacciones(
            cuenta, f - timedelta(days=1), f + timedelta(days=1)
        )
        if str(t.get('imported_id') or '').startswith('agendapro-tx:')
        and t.get('amount') == monto
    ]
    return candidatas[0] if len(candidatas) == 1 else None


def enlazar_con(cx: Any, p: Any, transaccion: dict[str, Any], motivo: str) -> None:
    """El movimiento YA esta en el libro (lo escribio otro): se enlaza sin
    escribir nada."""
    db.pendiente_actualizar(
        cx,
        p['id'],
        estado='publicado',
        firefly_id=transaccion['id'],
        pregunta=None,
        decidido_por=motivo,
    )
    db.bitacora(
        cx,
        'enlazar',
        usuario_id=p['usuario_id'],
        pendiente_id=p['id'],
        firefly_id=transaccion['id'],
        respuesta=motivo,
    )
    cx.commit()


def publicar_uno(
    cx: Any,
    p: Any,
    cliente: actual.Cliente,
    libro: Any,
    dry_run: bool = True,
    aunque_se_parezca: bool = False,
) -> tuple[str, Any]:
    """(accion, detalle). accion: creado | ya_estaba | parecido | seco |
    sin_destino | error. Con 'parecido', el detalle es la transaccion que se
    parece, para mostrarsela a la persona."""
    malo = _guardas(p, libro)
    if malo:
        return malo

    try:
        cuenta = cliente.cuenta_id(p['cuenta_firefly'])
        if not cuenta:
            return 'error', f'«{p["cuenta_firefly"]}» no existe en {libro["nombre"]}'
        f = fechas.a_fecha(p['fecha'])
        cerca = cliente.cerca_de(cuenta, f, DIAS + 1)
    except actual.ApiError as ex:
        return 'error', str(ex)[:200]

    for t in cerca:
        if t.get('imported_id') == p['external_id']:
            enlazar_con(cx, p, t, 'ya_estaba_en_actual')
            return 'ya_estaba', f'ya estaba en {libro["nombre"]}'

    monto = actual.centavos(p['valor'])
    tx: dict[str, Any] = {
        'date': str(f),
        'amount': monto,
        'notes': _nota(p),
        'imported_id': p['external_id'],
        'cleared': False,
    }
    if p['traslado_a'] and p['cuenta_destino']:
        destino = cliente.cuenta_id(p['cuenta_destino'])
        payee = cliente.payee_de_traslado(destino) if destino else None
        if not payee:
            return 'error', f'no encuentro «{p["cuenta_destino"]}» en {libro["nombre"]}'
        tx['payee'] = payee
    else:
        if not p['categoria']:
            return 'error', 'sin categoria'
        cat = cliente.categoria_id(p['categoria'], float(p['valor']) > 0)
        if not cat:
            return (
                'error',
                f'«{p["categoria"]}» no es una categoria de {libro["nombre"]}',
            )
        tx['category'] = cat
        tx['payee_name'] = (p['contraparte'] or p['descripcion'] or 'Sin identificar')[
            :120
        ]

    if not aunque_se_parezca:
        for t in cerca:
            ft = fechas.a_fecha(t.get('date'))
            if (
                t.get('imported_id') != p['external_id']
                and _parece(t.get('amount') or 0, monto)
                and ft
                and abs((ft - f).days) <= DIAS
            ):
                return 'parecido', t

    if dry_run:
        return (
            'seco',
            f'{libro["nombre"]}: {f} {monto / 100:,.0f} {p["categoria"] or "traslado"}',
        )

    try:
        tid = cliente.crear(cuenta, tx)
    except actual.ApiError as ex:
        db.pendiente_actualizar(cx, p['id'], estado='error')
        db.bitacora(
            cx,
            'crear',
            usuario_id=p['usuario_id'],
            pendiente_id=p['id'],
            payload=tx,
            respuesta=str(ex),
            ok=False,
        )
        cx.commit()
        return 'error', str(ex)[:200]
    if not tid:
        # Se creo pero no aparece: no se marca publicado, para que el
        # siguiente intento lo encuentre por imported_id y no lo duplique.
        return 'error', 'Actual acepto el movimiento pero no lo encuentro todavia'
    db.pendiente_actualizar(cx, p['id'], estado='publicado', firefly_id=tid)
    db.bitacora(
        cx,
        'crear',
        usuario_id=p['usuario_id'],
        pendiente_id=p['id'],
        firefly_id=tid,
        payload=tx,
        ok=True,
    )
    cx.commit()
    return 'creado', f'en {libro["nombre"]}'


def _nota(p: Any) -> str:
    origen = (
        'Contado por Telegram'
        if p['origen'] == 'chat'
        else f'Alerta de Bancolombia, plantilla {p["plantilla"]}'
    )
    return f'{origen}. Instrumento {p["instrumento"] or "?"}. {p["descripcion"] or ""}'[
        :500
    ]
