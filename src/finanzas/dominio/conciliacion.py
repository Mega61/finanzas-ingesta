"""Emparejar lo publicado contra lo que trae el extracto. Logica pura.

Aqui vivio el bug mas peligroso del sistema. La primera version, al no
encontrar el monto exacto, tomaba "el mas parecido del mismo comercio". Con
varios viajes en pocos dias eso encadenaba correcciones equivocadas:

    corrige 19-ago  -19.457 -> -18.828  UBER
    corrige 20-ago  -18.828 -> -23.440  UBER
    corrige 21-ago  -23.440 -> -64.617  UBER

O sea: cambiaba montos al azar en el libro contable, en silencio.

La regla que lo arregla: **solo se corrige un monto cuando el candidato es
UNICO.** Si hay varios, se marca ambiguo y se pregunta. Adivinar un monto es
peor que preguntar.

Este modulo no importa nada de I/O a proposito: recibe listas de datos y
devuelve decisiones. Eso es lo que lo hace testeable sin levantar un Firefly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from finanzas.dominio import dinero, texto
from finanzas.dominio.fechas import a_fecha

# Se prueban en este orden y gana el primer match mas cercano. Sale de la
# reconciliacion manual que ya habia funcionado.
TOLERANCIAS = (0, 1, 2, 3, 5, 8, 20, 45)

# Diferencia de monto que cuenta como "el mismo": redondeos y centavos.
EPS = 2.0

# Al convertir moneda el redondeo es mayor, se tolera un porcentaje.
MARGEN_MONEDA = 0.015

# Cuanto puede diferir una preautorizacion del cargo real para que siga siendo
# creible que son el mismo consumo. Mas alla de esto no se corrige: se pregunta.
MAX_DELTA_REL = 0.6

# Banda plausible de la tasa COP/USD. Junto con que el comercio coincida, es lo
# que evita emparejar cosas distintas solo porque el numero cuadra.
TASA_MIN, TASA_MAX = 2600.0, 5400.0


class Clase(StrEnum):
    """Que se decidio para un movimiento publicado."""

    IGUAL = 'igual'  # el extracto lo trajo con el mismo monto
    OTRO_MONTO = 'otro_monto'  # trajo otro monto y es creible: se corrige
    AMBIGUO = 'ambiguo'  # varios candidatos: NO se toca, se pregunta


@dataclass(frozen=True)
class Linea:
    """Una linea, venga del libro o del extracto. Solo lo que hace falta."""

    fecha: date | None
    valor: float
    moneda: str = 'COP'
    descripcion: str = ''
    ref: object = None  # el id de donde salio, para volver a el

    @property
    def monto(self) -> float:
        return abs(float(self.valor))


@dataclass
class Par:
    libro: Linea
    extracto: Linea
    clase: Clase
    por_que: str = ''


@dataclass
class Resultado:
    pares: list[Par] = field(default_factory=list)
    sin_pareja: list[Linea] = field(default_factory=list)
    solo_en_extracto: list[Linea] = field(default_factory=list)
    tasa_usada: float | None = None


def _parecido(a: Linea, b: Linea) -> bool:
    """¿Hablan del mismo comercio? Basta una palabra distintiva compartida."""
    return bool(
        texto.tokens_distintivos(a.descripcion)
        & texto.tokens_distintivos(b.descripcion)
    )


def tasa_implicita(libro: list[Linea], extracto: list[Linea]) -> float | None:
    """La tasa COP/USD deducida de los pares cuyo comercio coincide.

    Existe porque la AMEX factura en USD y las alertas llegan en COP. Sin
    cruzar moneda, TODA compra de esa tarjeta se declaraba fantasma: eran ~93
    de 115 falsos positivos en la primera medicion.

    Devuelve None si no hay con que deducirla, y ahi no se cruza moneda: es
    mejor no emparejar que emparejar con una tasa inventada.
    """
    razones: list[float] = []
    for mov in libro:
        if (mov.moneda or 'COP').upper() != 'COP':
            continue
        for e in extracto:
            if (e.moneda or 'COP').upper() != 'USD' or not e.valor:
                continue
            dias = _dias(mov.fecha, e.fecha)
            if dias is not None and dias > 5:
                continue
            r = mov.monto / e.monto
            if TASA_MIN <= r <= TASA_MAX and _parecido(mov, e):
                razones.append(r)
    if len(razones) < 2:
        return None
    razones.sort()
    return razones[len(razones) // 2]


def _dias(a: date | None, b: date | None) -> int | None:
    if a is None or b is None:
        return None
    return abs((a - b).days)


def emparejar(
    libro: list[Linea],
    extracto: list[Linea],
    tasa_respaldo: float | None = None,
) -> Resultado:
    """Cruza el libro contra el extracto. Cada linea del extracto se consume
    una sola vez.

    No muta las entradas y no toca nada de afuera: solo decide.
    """
    tasa = tasa_implicita(libro, extracto) or tasa_respaldo
    usados: set[int] = set()
    res = Resultado(tasa_usada=tasa)

    def equivalente(mov: Linea, ext: Linea) -> float | None:
        """El monto del extracto expresado en la moneda del libro."""
        ml, me = (mov.moneda or 'COP').upper(), (ext.moneda or 'COP').upper()
        if ml == me:
            return ext.monto
        if tasa and ml == 'COP' and me == 'USD':
            return ext.monto * tasa
        if tasa and ml == 'USD' and me == 'COP':
            return ext.monto / tasa
        return None

    for mov in libro:
        # --- 1. mismo monto, con tolerancia de fecha creciente
        mejor: tuple[int, int, Linea] | None = None
        for tol in TOLERANCIAS:
            candidatos = []
            for i, e in enumerate(extracto):
                if i in usados:
                    continue
                eq = equivalente(mov, e)
                if eq is None:
                    continue
                misma_moneda = (e.moneda or 'COP').upper() == (
                    mov.moneda or 'COP'
                ).upper()
                margen = EPS if misma_moneda else max(mov.monto * MARGEN_MONEDA, EPS)
                if abs(eq - mov.monto) > margen:
                    continue
                d = _dias(mov.fecha, e.fecha)
                if d is not None and d > tol:
                    continue
                candidatos.append((d if d is not None else 0, i, e))
            if candidatos:
                candidatos.sort(key=lambda c: c[0])
                mejor = candidatos[0]
                break
        if mejor is not None:
            usados.add(mejor[1])
            res.pares.append(Par(mov, mejor[2], Clase.IGUAL))
            continue

        # --- 2. otro monto del mismo comercio
        #
        # AQUI ESTUVO EL BUG. Tomar "el mas parecido" encadenaba correcciones
        # equivocadas. Solo se corrige si el candidato es UNICO y la diferencia
        # es creible; si hay varios, se pregunta.
        cands = []
        for i, e in enumerate(extracto):
            if i in usados:
                continue
            eq = equivalente(mov, e)
            if eq is None:
                continue
            d = _dias(mov.fecha, e.fecha)
            if d is None or d > 3:
                continue
            if not _parecido(mov, e):
                continue
            delta = abs(eq - mov.monto) / mov.monto if mov.monto else 1.0
            cands.append((delta, i, e))

        if len(cands) == 1 and cands[0][0] <= MAX_DELTA_REL:
            usados.add(cands[0][1])
            res.pares.append(
                Par(
                    mov,
                    cands[0][2],
                    Clase.OTRO_MONTO,
                    f'unico candidato, difiere {cands[0][0] * 100:.0f}%',
                )
            )
        elif cands:
            cands.sort(key=lambda c: c[0])
            res.pares.append(
                Par(
                    mov,
                    cands[0][2],
                    Clase.AMBIGUO,
                    f'{len(cands)} candidatos del mismo comercio: no se toca',
                )
            )
        else:
            res.sin_pareja.append(mov)

    res.solo_en_extracto = [e for i, e in enumerate(extracto) if i not in usados]
    return res


def es_fantasma(
    linea: Linea, cierre_extracto: date, hoy_: date, gracia_dias: int = 45
) -> bool:
    """¿Se puede declarar que este cargo nunca ocurrio?

    Solo si el extracto que deberia traerlo ya cerro y paso la gracia. Un cargo
    puede aparecer en el extracto siguiente, y declararlo fantasma antes de
    tiempo borra plata que si se gasto.
    """
    f = a_fecha(linea.fecha)
    if f is None or f > cierre_extracto:
        return False
    return (hoy_ - f).days > gracia_dias


def formatear_par(p: Par) -> str:
    """Una linea legible, para el log y para Telegram."""
    mov, ext = p.libro, p.extracto
    if p.clase is Clase.IGUAL:
        return f'{mov.fecha} {dinero.formatear(mov.monto)} confirmado'
    return (
        f'{mov.fecha} {dinero.formatear(mov.monto)} -> '
        f'{dinero.formatear(ext.monto)} {ext.moneda} [{p.clase.value}] {p.por_que}'
    )
