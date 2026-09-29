"""Categorias que la persona crea desde el chat.

Hasta aqui solo se podia elegir entre las que ya existian: la IA iba
restringida por enum y lo escrito se buscaba en la lista. Una cuenta nueva
arranca con una docena de categorias y la vida no cabe en doce, asi que la
persona se quedaba sin salida: «eso no es una categoria», y nada mas.

Ahora hay dos caminos a una categoria nueva, y en los dos la crea un toque:

  la IA la propone    mira lo que dice el banco o lo que ella conto, y las que
                      ya hay. Si una le queda, la marca; si ninguna, propone un
                      nombre. Es un boton, nunca se crea sola.
  ella la escribe     y no existe: se le ofrece crearla con ese nombre.

Cuando se crea tambien cambia por libro:

  Firefly   basta con el nombre: al publicar con `category_name` la crea.
  Actual    toda categoria vive en un grupo. Se pregunta el grupo si hay mas
            de uno, y se crea en el mismo momento que el movimiento, para que
            un libro en seco no se toque.
"""

from __future__ import annotations

import re
from typing import Any

from finanzas import registro
from finanzas.adaptadores import ia
from finanzas.dominio import texto as _texto

LARGO_MAXIMO = 40
PALABRAS_MAXIMAS = 4


def nombre_valido(escrito: str | None) -> str | None:
    """El nombre de una categoria a partir de lo que escribio, o None si no
    parece un nombre: una frase larga («fue lo del gato de la vecina») no se
    convierte en categoria."""
    if not escrito:
        return None
    t = re.sub(r'\s+', ' ', str(escrito)).strip(' .,;:!¡¿?«»"\'')
    if not t or len(t) > LARGO_MAXIMO or len(t.split()) > PALABRAS_MAXIMAS:
        return None
    if not re.search(r'[A-Za-zÁÉÍÓÚÑáéíóúñ]', t) or re.search(r'\d{3,}', t):
        return None
    return t[0].upper() + t[1:]


def ya_existe(nombre: str, existentes: list[str]) -> str | None:
    """La existente que se llama igual, sin mirar tildes ni mayusculas."""
    n = _texto.normalizar(nombre)
    return next((e for e in existentes if _texto.normalizar(e) == n), None)


def proponer(p: Any, existentes: list[str], libro: str = '') -> dict[str, Any]:
    """{'existente', 'nueva', 'razon'} para ese movimiento. Sin IA, o si falla,
    todo en None: la pregunta sigue funcionando con los botones de siempre."""
    vacio: dict[str, Any] = {'existente': None, 'nueva': None, 'razon': ''}
    try:
        if not ia.disponible():
            return vacio
        d = ia.proponer_categoria(
            {
                'valor': p['valor'],
                'contraparte': p['contraparte'],
                'descripcion': p['descripcion'],
            },
            existentes,
            libro,
        )
    except Exception as ex:
        registro.aviso(f'  no pude proponer categoria: {ex}')
        return vacio
    nueva = nombre_valido(d.get('nueva'))
    if nueva and ya_existe(nueva, existentes):
        return {**d, 'existente': ya_existe(nueva, existentes), 'nueva': None}
    return {**d, 'nueva': nueva}
