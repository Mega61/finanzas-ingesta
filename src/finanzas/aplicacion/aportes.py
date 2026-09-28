"""Un gasto del negocio pagado con la plata personal de la duena.

Es un aporte en especie, y se escribe en los DOS libros, enlazados por el
mismo external_id:

  Actual (el negocio)   UNA transaccion dividida, de monto 0, en la cuenta
                        «Aportes en especie»: +X como «Aportes de la dueña» y
                        -X en la categoria del gasto. El presupuesto del negocio
                        ve las dos cosas -- que gasto en insumos y que la duena
                        lo puso -- y ninguna cuenta real del negocio se mueve,
                        porque la plata nunca paso por ahi.
  Firefly (lo personal) el cargo en la tarjeta con que pago, hacia la cuenta de
                        gasto «Golden Beauty Studio», como «Aporte al estudio».

El orden importa: primero Actual, despues Firefly. Cada escritura se busca por
su id externo antes de hacerse, asi que si la segunda falla, reintentar no
duplica la primera.
"""

from __future__ import annotations

from typing import Any

from finanzas.adaptadores import actual, db, firefly
from finanzas.aplicacion import libros, ruteo
from finanzas.dominio import fechas

SUFIJO_FIREFLY = '-aporte'


def publicar(cx: Any, p: Any, dry_run: bool = True) -> tuple[str, Any]:
    alm = db.almacen(cx)
    negocio = alm.libro(p['libro_id'])
    personal = alm.libro(p['pago_libro_id'])
    if negocio is None or personal is None:
        return 'error', 'falta uno de los dos libros'
    if (
        negocio['usuario_id'] != p['usuario_id']
        or personal['usuario_id'] != p['usuario_id']
    ):
        return 'error', 'uno de los libros es de otra persona'
    if not p['categoria']:
        return 'error', 'sin categoria'
    try:
        act = libros.cliente(negocio)
        ff = libros.cliente(personal)
    except libros.LibroNoDisponible as ex:
        return 'error', str(ex)
    if not isinstance(act, actual.Cliente) or not isinstance(ff, firefly.Cliente):
        return 'error', 'el aporte en especie es de un Firefly a un Actual'

    x = actual.centavos(abs(float(p['valor'])))
    f = fechas.a_fecha(p['fecha'])
    try:
        cuenta = act.cuenta_id(p['cuenta_firefly'])
        if not cuenta:
            return 'error', f'«{p["cuenta_firefly"]}» no existe en {negocio["nombre"]}'
        cat_gasto = act.categoria_id(p['categoria'], False)
        cat_aporte = act.categoria_id(ruteo.ajuste(negocio, 'categoria_aportes'), True)
        if not cat_gasto or not cat_aporte:
            return 'error', f'faltan categorias en {negocio["nombre"]}'
        ya_actual = next(
            (
                t
                for t in act.cerca_de(cuenta, f, 1)
                if t.get('imported_id') == p['external_id']
            ),
            None,
        )
    except actual.ApiError as ex:
        return 'error', str(ex)[:200]

    ext_ff = f'{p["external_id"]}{SUFIJO_FIREFLY}'
    ya_ff = ff.buscar_por_external_id(ext_ff)

    if dry_run:
        return 'seco', (
            f'{negocio["nombre"]}: {p["categoria"]} {x / 100:,.0f} como aporte; '
            f'{personal["nombre"]}: {p["cuenta_pago"]} -> {ruteo.ajuste(personal, "cuenta_negocio")}'
        )

    que = (p['descripcion'] or p['contraparte'] or 'Gasto del estudio')[:200]
    tid = ya_actual['id'] if ya_actual else None
    try:
        if tid is None:
            tid = act.crear(
                cuenta,
                {
                    'date': str(f),
                    'amount': 0,
                    'payee_name': (p['contraparte'] or 'La dueña')[:120],
                    'imported_id': p['external_id'],
                    'cleared': False,
                    'notes': f'Pagado por la dueña con {p["cuenta_pago"]}. {que}'[:500],
                    'subtransactions': [
                        {
                            'amount': x,
                            'category': cat_aporte,
                            'notes': 'aporte de la dueña',
                        },
                        {'amount': -x, 'category': cat_gasto, 'notes': que},
                    ],
                },
            )
            if not tid:
                return 'error', 'Actual acepto el aporte pero no lo encuentro todavia'
            db.bitacora(
                cx,
                'crear',
                usuario_id=p['usuario_id'],
                pendiente_id=p['id'],
                firefly_id=tid,
                respuesta='aporte en especie: Actual',
            )
        if not ya_ff:
            payload = {
                'apply_rules': False,
                'fire_webhooks': False,
                'transactions': [
                    {
                        'type': 'withdrawal',
                        'date': str(f),
                        'amount': f'{x / 100:.2f}',
                        'currency_code': p['moneda'] or 'COP',
                        'description': f'Aporte al estudio: {que}'[:255],
                        'source_name': p['cuenta_pago'],
                        'destination_name': ruteo.ajuste(personal, 'cuenta_negocio'),
                        'category_name': ruteo.ajuste(personal, 'categoria_aporte'),
                        'tags': ['ingesta-automatica'],
                        'external_id': ext_ff,
                        'notes': f'Gasto de {negocio["nombre"]} pagado con plata personal.',
                    }
                ],
            }
            r = ff.call('POST', '/api/v1/transactions', payload)
            db.bitacora(
                cx,
                'crear',
                usuario_id=p['usuario_id'],
                pendiente_id=p['id'],
                firefly_id=(r.get('data') or {}).get('id'),
                payload=payload,
                respuesta='aporte en especie: Firefly',
            )
    except (actual.ApiError, firefly.ApiError) as ex:
        db.pendiente_actualizar(cx, p['id'], estado='error')
        db.bitacora(
            cx,
            'crear',
            usuario_id=p['usuario_id'],
            pendiente_id=p['id'],
            respuesta=f'aporte en especie: {ex}',
            ok=False,
        )
        cx.commit()
        return 'error', str(ex)[:200]
    db.pendiente_actualizar(cx, p['id'], estado='publicado', firefly_id=tid)
    cx.commit()
    return 'creado', f'en {negocio["nombre"]} y {personal["nombre"]}'
