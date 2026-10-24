"""Que categoria dice la factura de una compra, mirando sus productos.

Una alerta del banco dice «Compraste 17.500 en EXITO SABANETA» y nada mas, asi
que la regla del comercio la manda a Mercado. Pero no todo lo de Exito es
mercado: el almuerzo («Menu Casero») sale de la misma caja, y lo mismo el
mecato. En octubre de 2026 tres almuerzos y un mecato quedaron como Mercado.

La factura electronica si dice que se compro, y cada producto ya tiene su
grupo en el catalogo. Esto decide solo con lo que es claro:

  Restaurante       80% o mas es comida preparada
  Mecato            80% o mas es dulce, bebida o panaderia, y al menos un
                    tercio es dulce (pan y gaseosa para la casa es mercado)
  Gato              80% o mas es de la gata
  Cuidado personal  80% o mas es de cuidado personal

Lo mezclado no se toca: un mercado con un almuerzo adentro sigue siendo
mercado. Los productos sin clasificar y las bolsas no votan: los porcentajes
se sacan sobre lo que el catalogo si conoce, y eso tiene que ser al menos la
mitad de la factura. Sin esto, una gaseosa que nadie habia clasificado dejaba
como mercado unas crispetas, una gaseosa y un ponque.

Logica pura, sin I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

UMBRAL = 0.8
DULCE_MINIMO = 1 / 3
CONOCIDO_MINIMO = 0.5
NO_VOTAN = {None, 'Sin clasificar', 'Servicios'}

COMIDA = ('Comida preparada', None)
DULCE = ('Alimentacion', 'Snacks y dulces')
ANTOJO = {DULCE, ('Alimentacion', 'Bebidas'), ('Alimentacion', 'Panaderia')}
GATO = ('Mascotas', 'Gato')
CUIDADO = ('Cuidado personal', None)


@dataclass(frozen=True)
class Linea:
    descripcion: str
    grupo: str | None
    categoria: str | None
    total: float


def _es(linea: Linea, clave: tuple[str, str | None]) -> bool:
    grupo, categoria = clave
    return linea.grupo == grupo and (categoria is None or linea.categoria == categoria)


def categoria(lineas: Iterable[Linea]) -> tuple[str, str] | None:
    """(categoria de Firefly, razon) si la factura es claramente de una sola
    cosa que no es mercado. None si es mercado, si es mezclada, o si no hay
    con que decidir."""
    todas = [ln for ln in lineas if ln.total and ln.total > 0]
    lineas = [ln for ln in todas if ln.grupo not in NO_VOTAN]
    total = sum(ln.total for ln in lineas)
    if total <= 0 or total < CONOCIDO_MINIMO * sum(ln.total for ln in todas):
        return None

    def parte(pred) -> float:
        return sum(ln.total for ln in lineas if pred(ln)) / total

    def razon(pred) -> str:
        nombres = list(dict.fromkeys(ln.descripcion for ln in lineas if pred(ln)))
        return ', '.join(nombres[:4])

    def comida(ln: Linea) -> bool:
        return _es(ln, COMIDA)

    def antojo(ln: Linea) -> bool:
        return any(_es(ln, k) for k in ANTOJO)

    def dulce(ln: Linea) -> bool:
        return _es(ln, DULCE)

    def gato(ln: Linea) -> bool:
        return _es(ln, GATO)

    def cuidado(ln: Linea) -> bool:
        return _es(ln, CUIDADO)

    if parte(comida) >= UMBRAL:
        return 'Restaurante', razon(comida)
    if parte(antojo) >= UMBRAL and parte(dulce) >= DULCE_MINIMO:
        return 'Mecato', razon(antojo)
    if parte(gato) >= UMBRAL:
        return 'Gato', razon(gato)
    if parte(cuidado) >= UMBRAL:
        return 'Cuidado personal', razon(cuidado)
    return None
