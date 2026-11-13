"""Cuanto se puede gastar hoy, de verdad.

El tablero decia cuanto quedaba en cada presupuesto, y eso engaña: el 13 de
noviembre Esencial tenia 2,79M "libres" con el arriendo de 2,75M todavia por
pagar y medio mes de mercado por delante. Lo libre de verdad es lo que queda
despues de apartar lo que ya se sabe que viene:

1. Las cuentas fijas de Firefly (bills) que vencen antes de fin de mes y no
   se han pagado: el arriendo, EPM.
2. Lo que falta de mercado para llegar a la meta del mes.

Si con eso Esencial no alcanza, el hueco sale primero de Antojos y despues de
Vivir: se come y se paga el arriendo antes que comprar un sofa.

El calculo es puro (`calcular`) para poder probarlo sin Firefly; `leer` trae
los datos.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import date

from finanzas import config
from finanzas.adaptadores import firefly
from finanzas.aplicacion import presupuestos
from finanzas.dominio import dinero as _dinero
from finanzas.dominio import fechas as _fechas
from finanzas.dominio import texto as _texto

ESENCIAL = 'Esencial'
# Se gasta por dia: comer afuera, domicilios, salir.
DIARIO = 'Vivir'
# Se gasta de a pedazos: lo que se quiere y no se necesita.
ANTOJOS = 'Antojos'
IMPREVISTOS = 'Imprevistos Esenciales'
# De donde sale el hueco de Esencial, en ese orden.
CUBREN_EL_HUECO = (ANTOJOS, DIARIO)
MERCADO = 'Mercado'
META_MERCADO = float(config.get('MERCADO_META', '1200000') or 1200000)

# Una cuenta fija cuenta como pagada si este mes hay un gasto de su categoria
# por un monto parecido, desde unos dias antes del vencimiento.
HOLGURA_MONTO = 0.15
DIAS_ANTES = 12
# Las bills que son plata que se guarda, no que se gasta.
AHORRO = re.compile(r'fondo|ahorro|alcanc', re.I)

# Palabras -> presupuesto, para «/puedo 180000 restaurante».
PALABRAS = {
    DIARIO: (
        'restaurante comida almuerzo cena desayuno domicilio rappi salir salida '
        'bar cerveza trago uber didi taxi cine mecato cafe pizza hamburguesa'
    ),
    ANTOJOS: (
        'steam juego juegos videojuego ropa zapatos tenis mueble muebles sofa '
        'silla mesa cama ikea homecenter casa decoracion tecnologia gadget '
        'monitor audifonos regalo libro antojo'
    ),
    ESENCIAL: 'mercado super supermercado gasolina moto gato veterinaria',
    IMPREVISTOS: 'medicamento medicina droga drogueria medico',
}


@dataclass
class Cuenta:
    nombre: str
    monto: float
    vence: date
    presupuesto: str | None


@dataclass
class Bolsillo:
    nombre: str
    limite: float
    gastado: float
    apartado: float = 0.0  # cuentas fijas y mercado por venir
    prestado: float = 0.0  # lo que se le da a Esencial

    @property
    def libre(self) -> float:
        return self.limite - self.gastado - self.apartado - self.prestado


@dataclass
class Disponible:
    hoy: date
    dias: int  # los que quedan del mes, contando hoy
    bolsillos: dict[str, Bolsillo]
    cuentas: list[Cuenta]
    mercado_gastado: float
    mercado_falta: float
    hueco: float  # lo que a Esencial le falta, antes de cubrirlo
    sin_cubrir: float  # lo que ni Antojos ni Vivir alcanzaron a cubrir
    notas: list[str] = field(default_factory=list)

    def libre(self, nombre: str) -> float:
        b = self.bolsillos.get(nombre)
        return max(b.libre, 0.0) if b else 0.0

    def por_dia(self, nombre: str = DIARIO) -> float:
        return self.libre(nombre) / max(self.dias, 1)


def calcular(
    hoy: date,
    estado: list[dict],
    cuentas: list[Cuenta],
    mercado_gastado: float,
    meta_mercado: float = META_MERCADO,
) -> Disponible:
    """`estado` es lo de presupuestos.estado(); `cuentas`, las fijas que faltan."""
    bolsillos = {
        e['nombre']: Bolsillo(e['nombre'], float(e['limite']), float(e['gastado']))
        for e in estado
        if e.get('limite')
    }
    for c in cuentas:
        if c.presupuesto in bolsillos:
            bolsillos[c.presupuesto].apartado += c.monto
    mercado_falta = max(meta_mercado - mercado_gastado, 0.0)
    hueco = sin_cubrir = 0.0
    esencial = bolsillos.get(ESENCIAL)
    if esencial:
        esencial.apartado += mercado_falta
        hueco = max(-esencial.libre, 0.0)
        falta = hueco
        for nombre in CUBREN_EL_HUECO:
            b = bolsillos.get(nombre)
            if not b or falta <= 0:
                continue
            toma = min(max(b.libre, 0.0), falta)
            b.prestado += toma
            esencial.prestado -= toma
            falta -= toma
        sin_cubrir = falta
    dias = (_fechas.fin_de_mes(hoy) - hoy).days + 1
    return Disponible(
        hoy, dias, bolsillos, cuentas, mercado_gastado, mercado_falta, hueco, sin_cubrir
    )


def presupuesto_de(texto: str) -> str | None:
    """El presupuesto al que va lo que se quiere comprar, por palabras."""
    palabras = set(_texto.normalizar(texto).lower().split())
    for nombre, lista in PALABRAS.items():
        if palabras & set(lista.split()):
            return nombre
    return None


@dataclass
class Respuesta:
    cabe: bool
    presupuesto: str
    queda: float
    por_dia: float | None  # solo para el diario


def puedo(d: Disponible, monto: float, presupuesto: str) -> Respuesta:
    queda = d.libre(presupuesto) - monto
    por_dia = queda / max(d.dias, 1) if presupuesto == DIARIO else None
    return Respuesta(queda >= 0, presupuesto, queda, por_dia)


# --------------------------------------------------------------- Firefly


def _cuentas_por_pagar(
    hoy: date, gastos: list[dict], categorias: list[str], cx=None
) -> list:
    """Las bills que vencen de hoy a fin de mes y que no se ven pagadas.

    Firefly casi nunca enlaza el pago con la bill (el gasto lo mete la ingesta
    y no pasa por sus reglas), asi que `paid_dates` llega vacio aunque se haya
    pagado: se busca el gasto parecido a mano.
    """
    fin = _fechas.fin_de_mes(hoy)
    ruta = f'/api/v1/bills?start={hoy.replace(day=1)}&end={fin}'
    salida = []
    for b in firefly.get_all(ruta):
        a = b['attributes']
        if not a.get('active') or AHORRO.search(a.get('name') or ''):
            continue
        if a.get('paid_dates'):
            continue
        vence = [
            v
            for v in (_fechas.a_fecha(str(x)[:10]) for x in a.get('pay_dates') or [])
            if v and hoy <= v <= fin
        ]
        if not vence:
            continue
        minimo = float(a.get('amount_min') or 0)
        maximo = float(a.get('amount_max') or minimo)
        categoria = _categoria_de_cuenta(a.get('name') or '', categorias)
        if _ya_pagada(gastos, categoria, minimo, maximo, vence[0]):
            continue
        salida.append(
            Cuenta(
                a['name'],
                maximo,
                vence[0],
                presupuestos.presupuesto_de_categoria(categoria, cx)
                if categoria
                else None,
            )
        )
    return salida


def _categoria_de_cuenta(nombre: str, categorias: list[str]) -> str | None:
    """«Arriendo nuevo» -> Arriendo, «Factura EPM» -> Facturas."""
    palabras = _texto.normalizar(nombre).split()
    for c in categorias:
        cn = _texto.normalizar(c)
        if any(len(p) >= 5 and (cn == p or cn.startswith(p)) for p in palabras):
            return c
    return None


def _ya_pagada(gastos, categoria, minimo, maximo, vence) -> bool:
    if not categoria:
        return False
    desde = vence.toordinal() - DIAS_ANTES
    for g in gastos:
        if g['categoria'] != categoria or g['fecha'].toordinal() < desde:
            continue
        if minimo * (1 - HOLGURA_MONTO) <= g['monto'] <= maximo * (1 + HOLGURA_MONTO):
            return True
    return False


def _gastos_del_mes(hoy: date) -> list[dict]:
    salida = []
    ruta = f'/api/v1/transactions?type=withdrawal&start={hoy.replace(day=1)}&end={hoy}'
    for t in firefly.get_all(ruta):
        for s in t['attributes']['transactions']:
            with contextlib.suppress(TypeError, ValueError):
                salida.append(
                    {
                        'categoria': s.get('category_name'),
                        'monto': abs(float(s.get('amount') or 0)),
                        'fecha': _fechas.a_fecha(str(s.get('date'))[:10]),
                    }
                )
    return salida


def leer(hoy: date | None = None, cx=None) -> Disponible:
    hoy = hoy or _fechas.hoy()
    gastos = _gastos_del_mes(hoy)
    categorias = [
        c['attributes']['name'] for c in firefly.get_all('/api/v1/categories')
    ]
    mercado = sum(g['monto'] for g in gastos if g['categoria'] == MERCADO)
    return calcular(
        hoy,
        presupuestos.estado(hoy),
        _cuentas_por_pagar(hoy, gastos, categorias, cx),
        mercado,
    )


# ----------------------------------------------------------------- texto


def _p(v: float) -> str:
    return _dinero.formatear(round(v, -3), 'COP')


def texto_hoy(d: Disponible) -> str:
    lineas = [
        f'💸 <b>Hoy puedes gastar</b> · quedan {d.dias} días',
        '',
        f'🍽️ {DIARIO}: <b>{_p(d.por_dia())}</b> al día '
        f'({_p(d.libre(DIARIO))} hasta fin de mes)',
        f'🛋️ {ANTOJOS}: <b>{_p(d.libre(ANTOJOS))}</b> para el resto del mes',
    ]
    if IMPREVISTOS in d.bolsillos:
        lineas.append(f'🩹 {IMPREVISTOS}: {_p(d.libre(IMPREVISTOS))}')
    lineas += [
        '',
        f'🛒 Mercado: {_p(d.mercado_gastado)} de {_p(META_MERCADO)}',
    ]
    if d.cuentas:
        lineas.append(
            '📌 Apartado: '
            + ', '.join(f'{c.nombre} {_p(c.monto)} ({c.vence.day})' for c in d.cuentas)
        )
    if d.hueco:
        lineas.append(
            f'⚠️ {ESENCIAL} no alcanza por {_p(d.hueco)}: ya lo descuento de '
            + ' y '.join(CUBREN_EL_HUECO)
            + '.'
        )
    if d.sin_cubrir:
        lineas.append(f'🚨 Y aun así faltan {_p(d.sin_cubrir)}: eso sale del ahorro.')
    lineas += ['', '<i>¿Algo puntual? «/puedo 180000 restaurante»</i>']
    return '\n'.join(lineas)


def texto_puedo(d: Disponible, r: Respuesta, monto: float) -> str:
    if r.cabe:
        cola = f'Te quedan {_p(r.queda)} en {r.presupuesto}' + (
            f', <b>{_p(r.por_dia)}</b> al día.' if r.por_dia is not None else '.'
        )
        # Que quepa no es que convenga: un plan que se lleva mas de la mitad
        # de lo del mes deja el resto de los dias a punta de nada.
        if r.queda < d.libre(r.presupuesto) / 2:
            return f'⚠️ <b>Cabe, pero</b> se lleva más de la mitad.\n{cola}'
        return f'✅ <b>Sí</b>, {_p(monto)} cabe.\n{cola}'
    otros = [
        n for n in (DIARIO, ANTOJOS) if n != r.presupuesto and d.libre(n) + r.queda >= 0
    ]
    salida = (
        f'❌ <b>No</b>. En {r.presupuesto} quedan {_p(d.libre(r.presupuesto))} '
        f'libres: te faltan {_p(-r.queda)}.'
    )
    if otros:
        n = otros[0]
        resto = d.libre(n) + r.queda
        salida += f'\nSaliendo también de {n}, ahí te quedarían {_p(resto)}.'
    return salida
