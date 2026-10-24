"""Cambiar el plan de caja hablando con el bot.

«la Rappi paga el 10 y corta el 29», «el arriendo nuevo es 2,8 millones desde
diciembre». El modelo devuelve el plan completo cambiado; aqui se valida con
el mismo `plan.interpretar` que valida un archivo, se calcula QUE cambio, y
se guarda como propuesta. Rige solo cuando la persona confirma con un toque.

Mostrar el plan entero para confirmar no sirve: nadie revisa cuarenta lineas.
Se muestra la diferencia: lo que entra, lo que sale y lo que cambia.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from finanzas import registro
from finanzas.adaptadores import ia
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import plan as _plan
from finanzas.dominio import dinero

SECCIONES = (
    ('ingreso', 'concepto', 'ingreso'),
    ('compromiso', 'concepto', 'compromiso'),
    ('tarjeta', 'nombre', 'tarjeta'),
)
ESCALARES = ('cuenta', 'reserva', 'colchon', 'horizonte_dias')
# Por nombre: ruff lo toma por un «-» disfrazado si va pegado en el texto.
MENOS = '\N{MINUS SIGN}'


@dataclass
class Propuesta:
    accion: str  # 'cambiar' | 'preguntar' | 'nada' | 'error'
    resumen: str = ''
    pregunta: str = ''
    cambios: list[str] = field(default_factory=list)
    plan_id: int | None = None


def _valor(v: Any) -> str:
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, (int, float)) and abs(v) >= 1000:
        return dinero.formatear(v, 'COP')
    if isinstance(v, dict):
        return ', '.join(f'{k} {_valor(x)}' for k, x in v.items())
    return str(v)


def _linea(d: dict[str, Any], clave: str) -> str:
    extra = [f'{k} {_valor(v)}' for k, v in d.items() if k != clave]
    return f'{d.get(clave)}: ' + ', '.join(extra)


def diferencias(antes: dict[str, Any], despues: dict[str, Any]) -> list[str]:
    """Lo que cambio entre dos planes, en lineas para leer en el chat."""
    fuera = []
    for k in ESCALARES:
        if antes.get(k) != despues.get(k):
            fuera.append(f'~ {k}: {_valor(antes.get(k))} → {_valor(despues.get(k))}')
    if antes.get('meta') != despues.get('meta'):
        if not despues.get('meta'):
            fuera.append(f'{MENOS} meta')
        else:
            fuera.append(f'~ meta: {_valor(despues["meta"])}')
    for seccion, clave, nombre in SECCIONES:
        viejos, nuevos = antes.get(seccion) or [], despues.get(seccion) or []
        # Un mismo concepto puede ir partido en el tiempo: «el arriendo es 2,8M
        # desde diciembre» deja dos «Arriendo nuevo», uno hasta noviembre y otro
        # desde diciembre. Por nombre solo, el diff los juntaba y mostraba un
        # arriendo que se movia a diciembre: noviembre desaparecia sin que se
        # viera. Se distinguen por su `desde`.
        repetido = {
            n
            for lista in (viejos, nuevos)
            for n in {str(x.get(clave)) for x in lista}
            if sum(1 for x in lista if str(x.get(clave)) == n) > 1
        }

        def llave(x: dict[str, Any], clave: str = clave) -> tuple[str, str]:
            return str(x.get(clave)), str(x.get('desde') or '')

        def rotulo(k: tuple[str, str], repetido: set[str] = repetido) -> str:
            n, desde = k
            return f'{n} (desde {desde})' if n in repetido and desde else n

        a = {llave(x): x for x in viejos}
        b = {llave(x): x for x in nuevos}
        # Lo que solo cambio de `desde`, siendo uno solo antes y despues, es un
        # cambio y no una baja y un alta.
        for ka in list(a.keys() - b.keys()):
            pares = [kb for kb in b.keys() - a.keys() if kb[0] == ka[0]]
            solos = (
                sum(1 for k in a if k[0] == ka[0])
                == 1
                == sum(1 for k in b if k[0] == ka[0])
            )
            if solos and len(pares) == 1:
                b[ka] = b.pop(pares[0])
        for k in sorted(b.keys() - a.keys()):
            fuera.append(f'+ {nombre} {_linea(b[k], clave)}')
        for k in sorted(a.keys() - b.keys()):
            fuera.append(f'{MENOS} {nombre} {rotulo(k)}')
        for k in sorted(a.keys() & b.keys()):
            if a[k] != b[k]:
                cambios = [
                    f'{c} {_valor(a[k].get(c))} → {_valor(b[k].get(c))}'
                    for c in sorted(a[k].keys() | b[k].keys())
                    if a[k].get(c) != b[k].get(c)
                ]
                fuera.append(f'~ {nombre} {rotulo(k)}: ' + ', '.join(cambios))
    return fuera


def proponer(
    alm: Almacen, usuario_id: int, texto: str, cuentas: list[str], hoy: date
) -> Propuesta:
    actual = _plan.vigente(alm, usuario_id)
    if actual is None:
        return Propuesta('error', resumen='No tienes un plan de caja todavía.')
    _, datos = actual
    if not ia.disponible():
        return Propuesta(
            'error', resumen='Para cambiar el plan hablando necesito Gemini.'
        )
    try:
        r = ia.entender_cambio_de_plan(
            texto, _plan.a_json(datos), cuentas, hoy.isoformat()
        )
    except Exception as ex:
        registro.aviso(f'  no pude entender el cambio de plan: {ex}')
        return Propuesta('error', resumen=f'No pude entenderlo: {str(ex)[:150]}')
    if r.get('accion') == 'preguntar':
        return Propuesta('preguntar', pregunta=r.get('pregunta') or '¿Qué cambio?')
    if r.get('accion') != 'cambiar' or not r.get('plan'):
        return Propuesta('nada', resumen=r.get('resumen') or '')
    try:
        nuevo = json.loads(r['plan'])
        _plan.interpretar(nuevo)
    except (json.JSONDecodeError, _plan.PlanInvalido) as ex:
        return Propuesta('error', resumen=f'Lo entendí, pero el plan no cuadra: {ex}')
    cambios = diferencias(datos, nuevo)
    if not cambios:
        return Propuesta('nada', resumen='Eso ya está así en el plan.')
    plan_id = alm.guardar_plan(usuario_id, _plan.a_json(nuevo), texto)
    return Propuesta(
        'cambiar', r.get('resumen') or '', cambios=cambios, plan_id=plan_id
    )


def confirmar(alm: Almacen, usuario_id: int, plan_id: int) -> _plan.Plan | None:
    """La propuesta pasa a regir. None si no es suya o ya no esta."""
    fila = alm.plan_version(plan_id)
    if fila is None or fila['usuario_id'] != usuario_id or fila['confirmado']:
        return None
    alm.confirmar_plan(plan_id)
    return _plan.interpretar(json.loads(fila['datos']))


def texto(plan: _plan.Plan, saldos: dict[str, float], proy: Any | None) -> str:
    """El plan para leer en el chat: la meta, lo que viene y lo que hay."""

    def plata(v: float) -> str:
        return dinero.formatear(v, 'COP')

    lineas = ['<b>Tu plan de caja</b>', '']
    if plan.meta:
        hay = saldos.get(plan.meta.cuenta, 0.0)
        pct = (
            min(100, round(hay / plan.meta.objetivo * 100)) if plan.meta.objetivo else 0
        )
        lineas.append(
            f'🎯 {plan.meta.nombre}: <b>{plata(hay)}</b> de {plata(plan.meta.objetivo)} '
            f'({pct}%) en {plan.meta.cuenta}'
        )
    lineas.append(
        f'🏦 {plan.cuenta} {plata(saldos.get(plan.cuenta, 0.0))} · colchón {plata(plan.colchon)}'
    )
    if proy is not None:
        proximos = [e for e in proy.eventos if abs(e.monto) >= 1][:6]
        if proximos:
            lineas += ['', '<b>Lo que viene</b>']
            lineas += [
                f'{e.fecha.day:>2}/{e.fecha.month:02d}  '
                f'{dinero.formatear(e.monto, "COP", con_signo=True)}  {e.concepto}'
                for e in proximos
            ]
        if proy.faltante:
            lineas.append(
                f'\n⚠️ Pasa <b>{plata(proy.faltante)}</b> de {proy.reserva or "la reserva"} '
                f'antes del {proy.faltante_antes_de.day}/{proy.faltante_antes_de.month:02d}.'
            )
    lineas += [
        '',
        f'{len(plan.ingresos)} ingresos · {len(plan.compromisos)} compromisos · '
        f'{len(plan.tarjetas)} tarjetas',
        '<i>Para cambiarlo: /plan y lo que cambia, por ejemplo '
        '«/plan el arriendo nuevo es 2,8 millones desde diciembre».</i>',
    ]
    return '\n'.join(lineas)
