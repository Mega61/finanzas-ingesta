"""El plan de caja: lo que va a entrar y salir, y como queda la cuenta.

El resumen diario decia cuantas alertas faltaban por clasificar. No decia lo
que importa en un mes apretado: si la plata de la cuenta alcanza para lo que
viene, o cuanto hay que pasar de los ahorros y antes de que dia.

La respuesta depende de cosas que Firefly no sabe: que el arriendo se paga el
23, que la quincena cae el 15, que la Mastercard corta el 30 y se paga el 19,
que lo que se pasa por tarjeta en octubre sale de la cuenta en noviembre. Eso
se declara aqui, en un archivo, y se proyecta contra los saldos de Firefly.

Se lee, en este orden (igual que personas):

  1. PLAN_JSON   en una linea. Es lo que va en Portainer.
  2. PLAN_TOML   el mismo contenido en TOML, para un .env de desarrollo.
  3. plan.toml   en el volumen de datos o en la raiz del repo.

    cuenta = "Bancolombia"     # la caja que se proyecta
    reserva = "Nu"             # de donde se saca si no alcanza
    colchon = 500000           # por debajo de esto, avisar

    [[ingreso]]
    concepto = "Quincena 1"
    dia = 15                   # todos los meses; o `fecha = 2026-10-15` una vez
    monto = 4211025

    [[compromiso]]
    concepto = "Arriendo nuevo"
    dia = 23
    desde = 2026-11-01
    monto = 2750000
    medio = "Bancolombia"      # o el nombre de una tarjeta

    [[tarjeta]]
    nombre = "MASTERCARD BLACK"
    corte = 30
    pago = 19
    gasto_diario = 100000      # lo que se espera pasar por ella cada dia

      [tarjeta.sin_interes]    # compras a 0% que se dejan correr
      saldo = 4715181.68       # lo que faltaba por facturar...
      al = 2026-10-02          # ...en esta fecha
      cuota = 261954.58        # lo que se factura en cada corte

La proyeccion es aritmetica pura: no lee nada. Lo que sabe de Firefly (los
saldos y los movimientos recientes) le llega como argumento.
"""

from __future__ import annotations

import calendar
import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from finanzas import config
from finanzas.dominio import fechas

# Un compromiso con fecha futura que ya aparece pagado en Firefly no se vuelve a
# restar. Se busca un movimiento parecido en esta ventana antes de su fecha: el
# arriendo se puede pagar unos dias antes, y restarlo dos veces dejaria la
# proyeccion en rojo sin motivo.
DIAS_DE_ANTICIPO = 20
TOLERANCIA_MONTO = 0.03


class PlanInvalido(ValueError):
    pass


@dataclass(frozen=True)
class Movimiento:
    """Un ingreso o un compromiso, ya resuelto a una fecha."""

    fecha: date
    concepto: str
    monto: float  # positivo: entra a la cuenta; negativo: sale
    medio: str  # la cuenta o la tarjeta por donde pasa


@dataclass(frozen=True)
class Regla:
    """Una linea del plan: una vez (`fecha`) o cada mes (`dia`)."""

    concepto: str
    monto: float
    medio: str
    fecha: date | None = None
    dia: int | None = None
    desde: date | None = None
    hasta: date | None = None

    def ocurrencias(self, ini: date, fin: date) -> list[date]:
        if self.fecha:
            return [self.fecha] if ini <= self.fecha <= fin else []
        salida = []
        mes = date(ini.year, ini.month, 1)
        while mes <= fin:
            d = _dia_del_mes(mes.year, mes.month, self.dia)
            if (
                ini <= d <= fin
                and (not self.desde or d >= self.desde)
                and (not self.hasta or d <= self.hasta)
            ):
                salida.append(d)
            mes = _mes_siguiente(mes)
        return salida


@dataclass(frozen=True)
class Tarjeta:
    nombre: str
    corte: int
    pago: int
    gasto_diario: float = 0.0
    # Lo que falta por facturar de compras a 0%, y lo que se factura por corte.
    sin_interes_saldo: float = 0.0
    sin_interes_cuota: float = 0.0
    sin_interes_al: date | None = None

    def sin_interes_despues_de(self, corte: date) -> float:
        """Lo que queda por facturar a 0% despues de ese corte.

        El saldo se escribe una vez, con su fecha (`al`), y cada corte posterior
        le quita una cuota. Sin la fecha, el saldo escrito en octubre seguiria
        valiendo en diciembre y el pago de la tarjeta saldria corto.
        """
        if not self.sin_interes_saldo:
            return 0.0
        n, mes = 0, date(self.sin_interes_al.year, self.sin_interes_al.month, 1)
        while True:
            c = _dia_del_mes(mes.year, mes.month, self.corte)
            if c > corte:
                break
            if c > self.sin_interes_al:
                n += 1
            mes = _mes_siguiente(mes)
        return max(0.0, self.sin_interes_saldo - n * self.sin_interes_cuota)

    def cortes(self, ini: date, fin: date) -> list[tuple[date, date]]:
        """(corte, pago) de cada extracto que corta desde hoy y se paga antes del fin.

        Solo cortes de hoy en adelante. Un corte que ya paso no se sabe si ya se
        pago: la Mastercard corto el 30 de septiembre, se pago el 2 de octubre, y
        lo que debe hoy ya es del extracto siguiente. Un extracto ya cortado y
        SIN pagar va en el plan como compromiso.
        """
        salida = []
        mes = date(ini.year, ini.month, 1)
        while mes <= fin:
            c = _dia_del_mes(mes.year, mes.month, self.corte)
            p = _pago_despues_de(c, self.pago)
            if c >= ini and p <= fin:
                salida.append((c, p))
            mes = _mes_siguiente(mes)
        return salida


@dataclass(frozen=True)
class Plan:
    cuenta: str
    reserva: str | None
    colchon: float
    horizonte_dias: int
    ingresos: tuple[Regla, ...] = ()
    compromisos: tuple[Regla, ...] = ()
    tarjetas: tuple[Tarjeta, ...] = field(default_factory=tuple)


@dataclass
class Proyeccion:
    desde: date
    saldo_inicial: float
    eventos: list[Movimiento]  # solo los que tocan la cuenta, en orden
    serie: list[tuple[date, float]]  # el saldo de la cuenta al final de cada dia
    minimo: tuple[date, float]
    colchon: float
    reserva: str | None
    # Cuanto pasar de la reserva, y antes de que dia, para no bajar del colchon.
    faltante: float = 0.0
    faltante_antes_de: date | None = None
    pagos_de_tarjeta: dict[str, list[tuple[date, float]]] = field(default_factory=dict)


# ------------------------------------------------------------------- fechas


def _dia_del_mes(anio: int, mes: int, dia: int) -> date:
    """El dia 30 de febrero es el ultimo de febrero."""
    return date(anio, mes, min(dia, calendar.monthrange(anio, mes)[1]))


def _mes_siguiente(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _pago_despues_de(corte: date, dia_pago: int) -> date:
    """El primer `dia_pago` despues del corte."""
    p = _dia_del_mes(corte.year, corte.month, dia_pago)
    if p <= corte:
        m = _mes_siguiente(corte)
        p = _dia_del_mes(m.year, m.month, dia_pago)
    return p


def _habil_antes(d: date) -> date:
    """Sabado y domingo se corren al viernes.

    La nomina del 15 de noviembre (domingo) llega el viernes 13, y un pago de
    tarjeta que vence en fin de semana hay que hacerlo antes. Para las dos
    cosas, correr al viernes es lo prudente. Los festivos no se miran.
    """
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


# -------------------------------------------------------------------- leer


def _texto_crudo() -> tuple[str, str] | None:
    if config.get('PLAN_JSON'):
        return 'json', config.get('PLAN_JSON')
    if config.get('PLAN_TOML'):
        return 'toml', config.get('PLAN_TOML')
    for ruta in (config.ruta_datos('plan.toml'), config.ruta_proyecto('plan.toml')):
        if os.path.exists(ruta):
            with open(ruta, encoding='utf-8') as fh:
                return 'toml', fh.read()
    return None


def leer() -> Plan | None:
    """El plan configurado, o None si no hay."""
    crudo = _texto_crudo()
    if crudo is None:
        return None
    formato, contenido = crudo
    try:
        datos = json.loads(contenido) if formato == 'json' else tomllib.loads(contenido)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as ex:
        raise PlanInvalido(f'el plan no se lee: {ex}') from None
    return interpretar(datos)


def _fecha(v: Any, donde: str) -> date | None:
    if v is None:
        return None
    f = fechas.a_fecha(v if isinstance(v, (str, date)) else str(v))
    if f is None:
        raise PlanInvalido(f'{donde}: «{v}» no es una fecha')
    return f


def _regla(d: dict[str, Any], donde: str, medio_por_defecto: str) -> Regla:
    if 'concepto' not in d or 'monto' not in d:
        raise PlanInvalido(f'{donde}: falta concepto o monto')
    tiene_fecha, tiene_dia = d.get('fecha') is not None, d.get('dia') is not None
    if tiene_fecha == tiene_dia:
        raise PlanInvalido(f'{donde}: va `fecha` (una vez) o `dia` (cada mes)')
    dia = d.get('dia')
    if tiene_dia and not (isinstance(dia, int) and 1 <= dia <= 31):
        raise PlanInvalido(f'{donde}: el dia tiene que ser de 1 a 31')
    try:
        monto = abs(float(d['monto']))
    except (TypeError, ValueError):
        raise PlanInvalido(
            f'{donde}: el monto «{d["monto"]}» no es un numero'
        ) from None
    return Regla(
        concepto=str(d['concepto']),
        monto=monto,
        medio=str(d.get('medio') or medio_por_defecto),
        fecha=_fecha(d.get('fecha'), donde),
        dia=dia,
        desde=_fecha(d.get('desde'), donde),
        hasta=_fecha(d.get('hasta'), donde),
    )


def interpretar(datos: dict[str, Any]) -> Plan:
    cuenta = datos.get('cuenta')
    if not cuenta:
        raise PlanInvalido('el plan no dice que `cuenta` proyectar')
    tarjetas = []
    for i, t in enumerate(datos.get('tarjeta') or []):
        donde = f'tarjeta {i + 1}'
        if not t.get('nombre') or not t.get('corte') or not t.get('pago'):
            raise PlanInvalido(f'{donde}: falta nombre, corte o pago')
        s0 = t.get('sin_interes') or {}
        if s0 and not s0.get('al'):
            raise PlanInvalido(
                f'{donde}: `sin_interes` necesita la fecha `al` del saldo'
            )
        tarjetas.append(
            Tarjeta(
                nombre=str(t['nombre']),
                corte=int(t['corte']),
                pago=int(t['pago']),
                gasto_diario=float(t.get('gasto_diario') or 0),
                sin_interes_saldo=float(s0.get('saldo') or 0),
                sin_interes_cuota=float(s0.get('cuota') or 0),
                sin_interes_al=_fecha(s0.get('al'), donde),
            )
        )
    nombres = {t.nombre for t in tarjetas}
    compromisos = tuple(
        _regla(c, f'compromiso «{c.get("concepto", i + 1)}»', cuenta)
        for i, c in enumerate(datos.get('compromiso') or [])
    )
    for c in compromisos:
        if c.medio != cuenta and c.medio not in nombres:
            raise PlanInvalido(
                f'compromiso «{c.concepto}»: el medio «{c.medio}» no es la cuenta '
                f'ni una tarjeta del plan'
            )
    return Plan(
        cuenta=str(cuenta),
        reserva=datos.get('reserva'),
        colchon=float(datos.get('colchon') or 0),
        horizonte_dias=int(datos.get('horizonte_dias') or 45),
        ingresos=tuple(
            _regla(r, f'ingreso «{r.get("concepto", i + 1)}»', cuenta)
            for i, r in enumerate(datos.get('ingreso') or [])
        ),
        compromisos=compromisos,
        tarjetas=tuple(tarjetas),
    )


# --------------------------------------------------------------- proyectar


def _ya_esta(
    mov: Movimiento, recientes: list[Movimiento], hoy: date, usados: set[int]
) -> bool:
    """¿Ese ingreso o compromiso ya aparece en Firefly?

    Mismo medio, mismo sentido, monto parecido, en los dias antes de su fecha.
    Cada movimiento de Firefly se usa una sola vez: dos quincenas iguales no se
    pueden cubrir con un mismo deposito.
    """
    ini = mov.fecha - timedelta(days=DIAS_DE_ANTICIPO)
    for i, r in enumerate(recientes):
        if i in usados or r.medio != mov.medio:
            continue
        if (r.monto > 0) != (mov.monto > 0):
            continue
        if not ini <= r.fecha <= hoy:
            continue
        if abs(abs(r.monto) - abs(mov.monto)) <= abs(mov.monto) * TOLERANCIA_MONTO:
            usados.add(i)
            return True
    return False


def proyectar(
    plan: Plan,
    hoy: date,
    saldos: dict[str, float],
    recientes: list[Movimiento] | None = None,
) -> Proyeccion:
    """Como queda la cuenta dia por dia, de hoy al horizonte.

    `saldos` son los de Firefly: la cuenta en positivo, las tarjetas en
    negativo (lo que se debe). `recientes` son los movimientos de Firefly de
    las ultimas semanas, para no volver a contar lo que ya paso.

    Lo que se pasa por tarjeta no sale de la cuenta el dia que se compra: sale
    el dia de pago del extracto en el que cae. Por eso cada tarjeta lleva su
    deuda aparte, y la cuenta solo ve el pago.
    """
    recientes = recientes or []
    fin = hoy + timedelta(days=plan.horizonte_dias)
    usados: set[int] = set()

    # Hoy cuenta: la quincena que llega hoy todavia no esta en el saldo si
    # Firefly no la tiene, y si la tiene, `_ya_esta` la descarta.
    pendientes: list[Movimiento] = []
    for r in plan.ingresos:
        for d in r.ocurrencias(hoy, fin):
            pendientes.append(Movimiento(_habil_antes(d), r.concepto, r.monto, r.medio))
    for r in plan.compromisos:
        for d in r.ocurrencias(hoy, fin):
            pendientes.append(Movimiento(d, r.concepto, -r.monto, r.medio))
    pendientes = [
        m
        for m in sorted(pendientes, key=lambda m: m.fecha)
        if not _ya_esta(m, recientes, hoy, usados)
    ]
    # Lo que quedo con fecha pasada y no aparece: se asume que se va a pagar
    # hoy. Ignorarlo escondia justo lo que se olvido.
    pendientes = [
        Movimiento(max(m.fecha, hoy), m.concepto, m.monto, m.medio) for m in pendientes
    ]

    eventos: list[Movimiento] = [m for m in pendientes if m.medio == plan.cuenta]
    pagos: dict[str, list[tuple[date, float]]] = {}
    for t in plan.tarjetas:
        deuda = -saldos.get(t.nombre, 0.0)
        cargos = [m for m in pendientes if m.medio == t.nombre]
        # El gasto de hoy ya esta en Firefly; el planeado para hoy, todavia no.
        ultimo_dia, ultimo_cargo = hoy, hoy - timedelta(days=1)
        for corte, pago in t.cortes(hoy, fin):
            # La deuda al corte: la de hoy, lo planeado y el gasto de cada dia.
            deuda += t.gasto_diario * (corte - ultimo_dia).days
            deuda += sum(-m.monto for m in cargos if ultimo_cargo < m.fecha <= corte)
            ultimo_dia = ultimo_cargo = corte
            a_pagar = max(0.0, round(deuda - t.sin_interes_despues_de(corte), 2))
            deuda -= a_pagar
            fecha_pago = _habil_antes(pago)
            pagos.setdefault(t.nombre, []).append((fecha_pago, a_pagar))
            if a_pagar:
                eventos.append(
                    Movimiento(
                        fecha_pago,
                        f'Pago {t.nombre.split()[0].capitalize()}',
                        -a_pagar,
                        plan.cuenta,
                    )
                )

    eventos.sort(key=lambda m: (m.fecha, -m.monto))
    saldo = saldos.get(plan.cuenta, 0.0)
    serie, i = [], 0
    d = hoy
    while d <= fin:
        while i < len(eventos) and eventos[i].fecha == d:
            saldo += eventos[i].monto
            i += 1
        serie.append((d, round(saldo, 2)))
        d += timedelta(days=1)
    minimo = min(serie, key=lambda x: x[1])

    p = Proyeccion(
        desde=hoy,
        saldo_inicial=saldos.get(plan.cuenta, 0.0),
        eventos=eventos,
        serie=serie,
        minimo=minimo,
        colchon=plan.colchon,
        reserva=plan.reserva,
        pagos_de_tarjeta=pagos,
    )
    if minimo[1] < plan.colchon:
        p.faltante = plan.colchon - minimo[1]
        p.faltante_antes_de = next(d for d, s in serie if s < plan.colchon)
    return p
