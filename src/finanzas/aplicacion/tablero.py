"""El resumen diario: una imagen con las graficas y un texto corto debajo.

Antes el resumen era la cola de la ingesta («publicado: 12, falta categoria:
3»). Eso sirve para mantener el sistema, no para saber como va la plata. Esto
contesta lo otro: como van los presupuestos, cuanto hay, cuanto se debe, que
viene en los proximos dias, y si hay que mover plata de los ahorros.

Todo sale de Firefly y del plan de caja (plan.py). Si no hay plan, la imagen
lleva solo los presupuestos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from finanzas.adaptadores import firefly, graficos
from finanzas.aplicacion import asesor, presupuestos
from finanzas.aplicacion import plan as _plan
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas

# Cuantos dias hacia atras se miran los movimientos para saber que parte del
# plan ya paso. Tiene que cubrir DIAS_DE_ANTICIPO del plan.
DIAS_RECIENTES = _plan.DIAS_DE_ANTICIPO + 5
# Lo que viene: cuantos dias y cuantas lineas, para que el texto quepa en el
# pie de foto (Telegram corta en 1024 caracteres).
DIAS_PROXIMOS = 21
MAX_PROXIMOS = 6


@dataclass
class Tablero:
    imagen: bytes
    texto: str


def _plata(v: float) -> str:
    # El signo solo cuando es negativo: «baja a -$4.138.647», no «+$1.803.809».
    return _dinero.formatear(v, con_signo=v < 0)


def movimientos_recientes(hoy: date) -> list[_plan.Movimiento]:
    """Los movimientos de Firefly de las ultimas semanas, por cuenta.

    Un traslado son dos: sale de una cuenta y entra a la otra. Asi el pago de
    la tarjeta cuenta como salida de Bancolombia Y como abono a la tarjeta.
    """
    desde = hoy - timedelta(days=DIAS_RECIENTES)
    salida = []
    for t in firefly.get_all(f'/api/v1/transactions?start={desde}&end={hoy}'):
        for s in t['attributes']['transactions']:
            f = fechas.a_fecha(s.get('date'))
            try:
                monto = abs(float(s.get('amount') or 0))
            except (TypeError, ValueError):
                continue
            tipo = (s.get('type') or '').lower()
            desc = s.get('description') or ''
            if tipo in ('withdrawal', 'transfer'):
                salida.append(_plan.Movimiento(f, desc, -monto, s.get('source_name')))
            if tipo in ('deposit', 'transfer'):
                salida.append(
                    _plan.Movimiento(f, desc, monto, s.get('destination_name'))
                )
    return salida


def _saldos() -> dict[str, float]:
    activos, deudas = asesor.saldos()
    return {c['nombre']: c['saldo'] for c in [*activos, *deudas]}


def _texto(hoy, saldos, estado, plan, proy) -> str:
    fin = fechas.fin_de_mes(hoy)
    lineas = [f'<b>Así vas · {graficos.dia_mes(hoy)}</b>', '']

    if plan:
        caja = f'🏦 {plan.cuenta} <b>{_plata(saldos.get(plan.cuenta, 0))}</b>'
        if plan.reserva:
            caja += f' · {plan.reserva} {_plata(saldos.get(plan.reserva, 0))}'
        lineas.append(caja)
        deudas = []
        for t in plan.tarjetas:
            d = -saldos.get(t.nombre, 0)
            if d <= 0:
                continue
            al_cero = t.sin_interes_despues_de(hoy - timedelta(days=1))
            extra = f' ({graficos.corto(al_cero)} al 0%)' if al_cero else ''
            deudas.append(f'{t.nombre.split()[0]} {graficos.corto(d)}{extra}')
        if deudas:
            lineas.append('💳 Debes: ' + ' · '.join(deudas))

    con_tope = [p for p in estado if p['limite']]
    if con_tope:
        gastado = sum(p['gastado'] for p in con_tope)
        tope = sum(p['limite'] for p in con_tope)
        lineas.append(
            f'📊 Gastado {graficos.corto(gastado)} de {graficos.corto(tope)} '
            f'({gastado / tope * 100:.0f}%) · día {hoy.day} de {fin.day}'
        )
        for p in con_tope:
            if p['gastado'] > p['limite']:
                lineas.append(
                    f'⚠️ {p["nombre"]} pasado por {_plata(p["gastado"] - p["limite"])}'
                )

    if proy:
        hasta = hoy + timedelta(days=DIAS_PROXIMOS)
        proximos = [m for m in proy.eventos if m.fecha <= hasta][:MAX_PROXIMOS]
        if proximos:
            lineas += ['', '<b>Lo que viene</b>']
            for m in proximos:
                lineas.append(
                    f'<code>{graficos.dia_mes(m.fecha):>6}  '
                    f'{graficos.corto(m.monto, signo=True):>6}</code>  '
                    f'{m.concepto[:28]}'
                )
        lineas.append('')
        dmin, smin = proy.minimo
        if proy.faltante:
            de = f' de {proy.reserva}' if proy.reserva else ''
            lineas.append(
                f'⚠️ {plan.cuenta} baja a <b>{_plata(smin)}</b> el '
                f'{graficos.dia_mes(dmin)}. Pasa <b>{_plata(proy.faltante)}</b>{de} '
                f'antes del {graficos.dia_mes(proy.faltante_antes_de)}.'
            )
        else:
            lineas.append(
                f'✅ {plan.cuenta} no baja de {_plata(smin)} '
                f'({graficos.dia_mes(dmin)}). No hace falta mover plata.'
            )
    return '\n'.join(lineas)


def armar(hoy: date | None = None) -> Tablero:
    hoy = hoy or fechas.hoy()
    fin = fechas.fin_de_mes(hoy)
    estado = presupuestos.estado(hoy)
    saldos = _saldos()
    plan = _plan.leer()
    proy = None
    if plan:
        proy = _plan.proyectar(plan, hoy, saldos, movimientos_recientes(hoy))
    imagen = graficos.tablero(
        f'Presupuestos de {graficos.MESES_LARGOS[hoy.month - 1]}',
        estado,
        hoy.day,
        fin.day,
        proy,
        plan.cuenta if plan else '',
    )
    return Tablero(imagen, _texto(hoy, saldos, estado, plan, proy))
