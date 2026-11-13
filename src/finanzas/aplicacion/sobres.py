"""Los sobres de mercado: cuanto va de cada cosa en la quincena.

La meta de mercado del mes (1,2M) decia cuanto se habia ido, no en que. Un
mes podia llegar a la meta con la mitad en carne y nadie lo veia hasta el
cierre. Los sobres parten el mercado en cuatro, con tope cada uno, y se
llenan con las LINEAS de las facturas del super, no con el movimiento del
banco: el banco dice «Exito 312.000», la factura dice que 150.000 fueron
lomo.

    🥬 Fruver       'Frutas y verduras'                       200.000 / quincena
    🥩 Carne        'Carnes y pollo', 'Pescados y mariscos'   200.000 / quincena
    🥫 Alacena      el resto de Alimentacion, Comida
                    preparada y Licores                       200.000 / quincena
    🧴 Aseo y casa  Aseo y hogar, Cuidado personal y
                    Servicios (bolsas, domicilio)             100.000 / MES

La quincena es del 1 al 15 y del 16 a fin de mes, en fechas de Bogota. Aseo
va por mes porque se compra de a mucho y pocas veces: un tope quincenal lo
haria saltar la quincena del detergente y sobrar la otra.

No cuentan Hogar, Electrodomesticos ni Tecnologia (compras de una vez), ni
'No es mercado'. Mascotas tampoco: la comida del gato no es de ningun sobre.
Lo 'Sin clasificar' cae en Alacena, porque es lo mas probable, pero se dice
cuantas lineas son: hasta que se contesten en /productos el sobre es una
estimacion.

Las facturas llegan por correo con dias de atraso, asi que cada sobre dice
hasta que fecha hay facturas: «Carne 40.000» un dia 14 puede ser solo lo del
lunes.

`calcular` es puro para probarlo sin base; `leer` trae las lineas.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, timedelta

from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import catalogo as _catalogo
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas as _fechas

QUINCENA = 'quincena'
MES = 'mes'


@dataclass(frozen=True)
class Definicion:
    clave: str
    emoji: str
    nombre: str
    limite: float
    periodo: str  # QUINCENA o MES


FRUVER = Definicion('fruver', '🥬', 'Fruver', 200_000, QUINCENA)
CARNE = Definicion('carne', '🥩', 'Carne', 200_000, QUINCENA)
ALACENA = Definicion('alacena', '🥫', 'Alacena', 200_000, QUINCENA)
ASEO = Definicion('aseo', '🧴', 'Aseo y casa', 100_000, MES)
# El orden es el orden en que se muestran.
SOBRES = (FRUVER, CARNE, ALACENA, ASEO)

CATEGORIAS_FRUVER = frozenset({'Frutas y verduras'})
CATEGORIAS_CARNE = frozenset({'Carnes y pollo', 'Pescados y mariscos'})
GRUPOS_ALACENA = frozenset({'Alimentacion', 'Comida preparada', 'Licores'})
GRUPOS_ASEO = frozenset({'Aseo y hogar', 'Cuidado personal', 'Servicios'})
SIN_CLASIFICAR = 'Sin clasificar'

# Cuantos productos se muestran por sobre en /mercado.
TOP = 3


def sobre_de(grupo: str | None, categoria: str | None) -> Definicion | None:
    """El sobre al que va una linea, o None si no es de ninguno."""
    grupo = grupo or SIN_CLASIFICAR
    if grupo == 'Alimentacion' and categoria in CATEGORIAS_FRUVER:
        return FRUVER
    if grupo == 'Alimentacion' and categoria in CATEGORIAS_CARNE:
        return CARNE
    if grupo in GRUPOS_ALACENA or grupo == SIN_CLASIFICAR:
        return ALACENA
    if grupo in GRUPOS_ASEO:
        return ASEO
    return None


# ---------------------------------------------------------------- periodos


def quincena(d: date) -> tuple[date, date]:
    """(desde, hasta) de la quincena de `d`: 1-15 o 16-fin de mes."""
    if d.day <= 15:
        return d.replace(day=1), d.replace(day=15)
    return d.replace(day=16), _fechas.fin_de_mes(d)


def quincena_anterior(d: date) -> tuple[date, date]:
    return quincena(quincena(d)[0] - timedelta(days=1))


def mes(d: date) -> tuple[date, date]:
    return _fechas.inicio_de_mes(d), _fechas.fin_de_mes(d)


def mes_anterior(d: date) -> tuple[date, date]:
    return mes(_fechas.inicio_de_mes(d) - timedelta(days=1))


def periodo(s: Definicion, hoy: date, anterior: bool = False) -> tuple[date, date]:
    if s.periodo == MES:
        return mes_anterior(hoy) if anterior else mes(hoy)
    return quincena_anterior(hoy) if anterior else quincena(hoy)


def desde_para_leer(hoy: date) -> date:
    """La fecha mas vieja que hace falta para calcular `hoy` y su comparacion."""
    return min(quincena_anterior(hoy)[0], mes_anterior(hoy)[0])


# ----------------------------------------------------------------- calculo


@dataclass
class Sobre:
    definicion: Definicion
    desde: date
    hasta: date
    gastado: float = 0.0
    anterior: float = 0.0  # lo del periodo pasado, para comparar
    sin_clasificar: int = 0  # lineas que entraron sin saber que eran
    productos: dict[tuple[str, str], list] = field(default_factory=dict)

    @property
    def limite(self) -> float:
        return self.definicion.limite

    @property
    def queda(self) -> float:
        return self.limite - self.gastado

    @property
    def pasado(self) -> bool:
        return self.gastado > self.limite

    def top(self, n: int = TOP) -> list[tuple[str, float]]:
        """Los `n` productos que mas se llevaron del sobre en el periodo."""
        orden = sorted(self.productos.values(), key=lambda p: -p[1])
        return [(desc, monto) for desc, monto in orden[:n] if monto > 0]


@dataclass
class Sobres:
    hoy: date
    sobres: list[Sobre]
    ultima_factura: date | None  # la mas nueva que se alcanzo a contar

    @property
    def sin_clasificar(self) -> int:
        return sum(s.sin_clasificar for s in self.sobres)


def _campo(linea, nombre):
    try:
        return linea[nombre]
    except (KeyError, IndexError):
        return None


def calcular(lineas, hoy: date) -> Sobres:
    """`lineas`: dicts o filas con fecha, signo, nit, codigo, descripcion,
    total, grupo y categoria. Las que caen fuera de los periodos se ignoran."""
    sobres = {s.clave: Sobre(s, *periodo(s, hoy)) for s in SOBRES}
    ultima = None
    for ln in lineas:
        fecha = _fechas.a_fecha(_campo(ln, 'fecha'))
        if fecha is None or fecha > hoy:
            continue
        grupo = _campo(ln, 'grupo') or SIN_CLASIFICAR
        d = sobre_de(grupo, _campo(ln, 'categoria'))
        if d is None:
            continue
        monto = float(_campo(ln, 'total') or 0) * int(_campo(ln, 'signo') or 1)
        s = sobres[d.clave]
        desde_ant, hasta_ant = periodo(d, hoy, anterior=True)
        if s.desde <= fecha <= s.hasta:
            s.gastado += monto
            if grupo == SIN_CLASIFICAR:
                s.sin_clasificar += 1
            llave = (str(_campo(ln, 'nit') or ''), str(_campo(ln, 'codigo') or ''))
            desc = (_campo(ln, 'descripcion') or '').strip() or llave[1]
            p = s.productos.setdefault(llave, [desc, 0.0])
            p[1] += monto
            if len(desc) > len(p[0]):
                p[0] = desc  # el nombre mas largo: el super lo trunca distinto
        elif desde_ant <= fecha <= hasta_ant:
            s.anterior += monto
        else:
            continue
        ultima = max(ultima, fecha) if ultima else fecha
    return Sobres(hoy, list(sobres.values()), ultima)


def leer(cx, hoy: date | None = None) -> Sobres:
    hoy = hoy or _fechas.hoy()
    lineas = Almacen(cx).lineas_de_mercado(
        _catalogo.NITS_MERCADO, desde_para_leer(hoy).isoformat(), hoy.isoformat()
    )
    return calcular(lineas, hoy)


# ------------------------------------------------------------------- texto


def _n(v: float) -> str:
    """152.560, sin el signo de pesos: en una linea con «/ 200.000» sobra."""
    return _dinero.formatear(v).lstrip('$')


def _dia(d: date) -> str:
    """«11-nov»."""
    return _fechas.corta(d).replace(' ', '-')


def _rango(desde: date, hasta: date) -> str:
    """Del 1 al 15 de noviembre, corto y con raya: como en el calendario."""
    return f'{desde.day}–{_fechas.corta(hasta)}'  # noqa: RUF001


def _linea(s: Sobre) -> str:
    cola = ' al mes' if s.definicion.periodo == MES else ''
    alerta = ' ⚠️' if s.pasado else ''
    return (
        f'{s.definicion.emoji} {s.definicion.nombre} '
        f'{_n(s.gastado)} / {_n(s.limite)}{cola}{alerta}'
    )


def _pie(x: Sobres) -> str:
    partes = [
        f'facturas hasta el {_dia(x.ultima_factura)}'
        if x.ultima_factura
        else 'sin facturas todavía'
    ]
    if x.sin_clasificar:
        partes.append(f'{x.sin_clasificar} sin clasificar en Alacena')
    return '<i>' + ' · '.join(partes) + '</i>'


def texto_bloque(x: Sobres) -> str:
    """El bloque corto que va en /hoy."""
    desde, hasta = quincena(x.hoy)
    return '\n'.join(
        [f'🧺 <b>Sobres</b> · {_rango(desde, hasta)}']
        + [_linea(s) for s in x.sobres]
        + [_pie(x)]
    )


def texto_mercado(x: Sobres) -> str:
    """/mercado: los sobres con lo que mas se llevo cada uno, y la quincena
    pasada al lado para comparar."""
    desde, hasta = quincena(x.hoy)
    lineas = [f'🧺 <b>Mercado</b> · quincena {_rango(desde, hasta)}', _pie(x)]
    for s in x.sobres:
        if s.pasado:
            estado = f'te pasaste por {_n(-s.queda)}'
        else:
            estado = f'quedan {_n(s.queda)}'
        lineas += ['', f'{_linea(s)} · {estado}']
        if s.definicion.periodo == MES:
            lineas[-1] += f'\n<i>va por mes: {_rango(s.desde, s.hasta)}</i>'
        top = s.top()
        if not top:
            lineas.append('   · nada todavía')
        for desc, monto in top:
            lineas.append(f'   · {html.escape(desc, quote=False)} {_n(monto)}')
    q_desde, q_hasta = quincena_anterior(x.hoy)
    lineas += ['', f'<b>Quincena pasada</b> · {_rango(q_desde, q_hasta)}']
    for s in x.sobres:
        a_desde, a_hasta = periodo(s.definicion, x.hoy, anterior=True)
        cola = (
            f' (mes {_rango(a_desde, a_hasta)})' if s.definicion.periodo == MES else ''
        )
        alerta = ' ⚠️' if s.anterior > s.limite else ''
        lineas.append(
            f'{s.definicion.emoji} {s.definicion.nombre} '
            f'{_n(s.anterior)} / {_n(s.limite)}{cola}{alerta}'
        )
    return '\n'.join(lineas)
