"""El plan de caja, visto desde Firefly.

El plan vive en el bot (`plan.vigente`), pero dos partes tienen donde verse
en Firefly, en el celular y sin abrir Telegram:

  la meta          una alcancia (piggy bank) sobre su cuenta, con lo que hay
                   hoy en esa cuenta. «Fondo de emergencia · Nu · 88%».
  lo de cada mes   una factura fija (bill) por compromiso mensual: el
                   arriendo, lo que va al fondo. Firefly las muestra como
                   «por pagar» y las marca pagadas cuando entra el movimiento.

Solo se tocan las facturas que creo el bot (llevan MARCA en las notas): la de
EPM que ya existia es de la persona. Un compromiso que sale del plan no se
borra en Firefly: se desactiva, y su historial queda.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finanzas.adaptadores import firefly
from finanzas.aplicacion import plan as _plan
from finanzas.dominio import fechas

MARCA = 'plan-del-bot'


def _saldo(cuenta_id: str) -> float:
    a = firefly.call('GET', f'/api/v1/accounts/{cuenta_id}')['data']['attributes']
    return float(a.get('current_balance') or 0)


def _siguiente(r: _plan.Regla, hoy: date) -> date:
    """La proxima vez que toca: la fecha que Firefly le pone a la factura."""
    ocurre = r.ocurrencias(hoy, hoy + timedelta(days=62))
    return ocurre[0] if ocurre else (r.desde or hoy)


def sincronizar(plan: _plan.Plan, hoy: date | None = None) -> dict[str, Any]:
    """Deja la alcancia y las facturas fijas como dice el plan. Devuelve lo
    que hizo, para el log y para contarlo."""
    hoy = hoy or fechas.hoy()
    hecho: dict[str, Any] = {'alcancia': None, 'facturas': [], 'desactivadas': []}
    cuentas = firefly.accounts_index()

    # --- la meta
    if plan.meta and plan.meta.cuenta in cuentas:
        cuenta_id = cuentas[plan.meta.cuenta]['id']
        actual = max(0.0, min(_saldo(cuenta_id), plan.meta.objetivo))
        existente = next(
            (
                a['id']
                for a in firefly.alcancias()
                if a['attributes']['name'] == plan.meta.nombre
            ),
            None,
        )
        firefly.guardar_alcancia(
            plan.meta.nombre,
            plan.meta.objetivo,
            cuenta_id,
            actual,
            alcancia_id=existente,
            notas=f'{MARCA}: la meta del plan de caja. Se actualiza sola con el saldo de {plan.meta.cuenta}.',
        )
        hecho['alcancia'] = {
            'nombre': plan.meta.nombre,
            'actual': actual,
            'objetivo': plan.meta.objetivo,
        }

    # --- lo de cada mes. Un concepto partido en el tiempo (2,75M hasta
    # noviembre, 2,8M desde diciembre) es UNA factura: la del tramo que toca
    # primero. Cuando ese tramo se acaba, la sincronizacion del dia la pasa al
    # siguiente.
    mensuales: dict[str, _plan.Regla] = {}
    for r in plan.compromisos:
        if r.dia is None or (r.hasta and r.hasta < hoy):
            continue
        otra = mensuales.get(r.concepto)
        if otra is None or _siguiente(r, hoy) < _siguiente(otra, hoy):
            mensuales[r.concepto] = r
    nuestras = {
        b['attributes']['name']: b
        for b in firefly.facturas_fijas()
        if MARCA in (b['attributes'].get('notes') or '')
    }
    for nombre, r in mensuales.items():
        b = nuestras.get(nombre)
        firefly.guardar_factura_fija(
            nombre,
            r.monto,
            _siguiente(r, hoy),
            factura_id=b['id'] if b else None,
            notas=f'{MARCA}: compromiso del plan de caja, el {r.dia} de cada mes por {r.medio}.',
        )
        hecho['facturas'].append(nombre)
    for nombre, b in nuestras.items():
        if nombre not in mensuales and b['attributes'].get('active'):
            firefly.guardar_factura_fija(
                nombre,
                float(b['attributes']['amount_min']),
                fechas.a_fecha(b['attributes']['date']) or hoy,
                activa=False,
                factura_id=b['id'],
            )
            hecho['desactivadas'].append(nombre)
    return hecho
