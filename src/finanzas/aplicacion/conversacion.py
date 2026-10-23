"""Entender una respuesta hablando: la IA lee primero, pregunta de vuelta, y
lo que aprende queda para la proxima.

Antes, contestar por escrito era emparejar palabras contra una lista: «gata»
-> Gato. Lo que no estaba en la lista no se entendia, y el bot no podia
preguntar nada de vuelta: o adivinaba o volvia a mostrar los botones. Y lo que
la persona contaba («es la cuenta de la arrendadora») se perdia apenas se
elegia la categoria.

Ahora el modelo lee la respuesta con todo lo que hace falta -- el movimiento,
el hilo de lo que ya se hablo, lo que se sabe de esa contraparte, los
parecidos -- y decide UNA cosa:

  proponer    lo que entendio, para confirmar con un toque. NUNCA se escribe
              en un libro sin ese toque: la regla la puso el usuario.
  preguntar   le falta algo que solo ella sabe. Una pregunta corta, y el hilo
              sigue sobre ese mismo movimiento.
  descartar   no fue un movimiento real. Tambien se confirma.
  nada        el mensaje no habla de esto: sigue por el camino de siempre.
  rendirse    ya pregunto demasiado sin entender: vuelven los botones.

Lo que devuelve el modelo se valida aqui contra lo que EXISTE: una categoria
del libro equivocado, un libro que no es de ella o un presupuesto inventado no
pasan, aunque el esquema ya los restrinja. Sin IA, o si falla, devuelve None y
cada bot sigue con su camino de antes.
"""

from __future__ import annotations

import json
from typing import Any

from finanzas import registro
from finanzas.adaptadores import ia
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import categorias, interprete, prestamos
from finanzas.dominio import texto as _texto

# Preguntas de vuelta antes de rendirse. Con tres sin entender, una cuarta no
# va a ayudar: los botones resuelven en un toque.
MAX_PREGUNTAS = 3

ACCIONES = ('proponer', 'preguntar', 'descartar', 'nada')


def clave_de(p: Any) -> str:
    """La misma clave de las reglas: lo que dice el banco, normalizado."""
    return _texto.normalizar(p['contraparte'] or p['descripcion'] or '') or ''


def entender(
    alm: Almacen,
    p: Any,
    texto: str,
    libros: list[dict[str, Any]],
    presupuestos: list[str] | None = None,
    comercios: list[str] | None = None,
    con_prestamos: bool = False,
) -> dict[str, Any] | None:
    """La lectura de `texto` como respuesta sobre el movimiento `p`.

    libros: [{'id', 'nombre', 'categorias'}], los libros posibles para este
            movimiento. Uno solo si ya tiene libro o si la persona lleva uno.

    Guarda lo que dijo en el hilo solo si el modelo contesto: si la llamada
    falla, el camino de respaldo atiende el mensaje y el hilo no queda con
    una frase que nadie leyo.
    """
    if not libros or not ia.disponible():
        return None
    presupuestos, comercios = list(presupuestos or []), list(comercios or [])
    hilo = [*alm.turnos(p['id']), ('persona', texto)]
    actual = next((lb for lb in libros if lb['id'] == p['libro_id']), None)
    try:
        crudo = ia.entender_respuesta(
            {
                'fecha': p['fecha'],
                'valor': p['valor'],
                'moneda': p['moneda'],
                'contraparte': p['contraparte'],
                'descripcion': p['descripcion'],
                'libro': actual['nombre'] if actual else None,
            },
            hilo,
            {lb['nombre']: lb['categorias'] for lb in libros},
            presupuestos,
            comercios,
            nota=alm.nota(p['usuario_id'], clave_de(p)),
            similares=interprete.similares(alm.cx, p['usuario_id'], p, texto),
            con_prestamos=con_prestamos,
        )
    except Exception as ex:
        registro.aviso(f'  no pude entender la respuesta: {ex}')
        return None
    alm.agregar_turno(p['id'], 'persona', texto)
    lectura = validar(crudo, p, libros, presupuestos, comercios, con_prestamos)
    preguntas = sum(1 for rol, _ in hilo if rol == 'bot')
    if lectura['accion'] == 'preguntar' and preguntas >= MAX_PREGUNTAS:
        lectura['accion'] = 'rendirse'
    return lectura


def validar(
    d: dict[str, Any],
    p: Any,
    libros: list[dict[str, Any]],
    presupuestos: list[str],
    comercios: list[str],
    con_prestamos: bool,
) -> dict[str, Any]:
    """Lo que dijo el modelo, recortado a lo que existe y es de ella."""
    accion = d.get('accion') if d.get('accion') in ACCIONES else 'nada'

    # --- el libro: el que ya tiene, el unico, o el que dijo si es suyo
    por_nombre = {lb['nombre']: lb for lb in libros}
    libro = next((lb for lb in libros if lb['id'] == p['libro_id']), None)
    if libro is None and len(libros) == 1:
        libro = libros[0]
    if libro is None:
        libro = por_nombre.get(d.get('libro') or '')

    # --- la categoria, del libro elegido. Sin libro, si existe en uno solo,
    #     ese es el libro: «Insumos» solo puede ser del estudio.
    categoria = d.get('categoria') or None
    if categoria and libro is None:
        donde = [lb for lb in libros if categoria in lb['categorias']]
        libro = donde[0] if len(donde) == 1 else None
        if libro is None:
            categoria = None
    if categoria and libro and categoria not in libro['categorias']:
        categoria = None

    # Una nueva solo se propone cuando ninguna existente le queda, asi que si
    # el modelo la da, esa es su lectura: la existente que trae al lado es
    # relleno. Paso con «la señora del aseo»: Facturas, y al lado la nueva
    # «Servicio Doméstico», que era la de verdad. Igual solo se crea con el
    # toque.
    nueva = categorias.nombre_valido(d.get('categoria_nueva'))
    if nueva and libro:
        ya = categorias.ya_existe(nueva, libro['categorias'])
        categoria, nueva = (ya, None) if ya else (None, nueva)
    elif nueva:
        nueva = None  # sin libro no hay donde crearla

    prestamo = prestamos.nombre(d.get('prestamo_con')) if con_prestamos else None
    if prestamo:
        categoria, nueva = None, None

    lectura = {
        'accion': accion,
        'libro_id': libro['id'] if libro else None,
        'libro': libro['nombre'] if libro else None,
        'categoria': categoria,
        'categoria_nueva': nueva,
        'presupuesto': d.get('presupuesto')
        if d.get('presupuesto') in presupuestos
        else None,
        'comercio': d.get('comercio') if d.get('comercio') in comercios else None,
        'prestamo_con': prestamo,
        'pregunta': (d.get('pregunta') or '').strip() or None,
        'nota': (d.get('nota') or '').strip() or None,
        'siempre': bool(d.get('siempre')),
        'razon': (d.get('razon') or '').strip(),
        'confianza': float(d.get('confianza') or 0.5),
    }

    # Nombro una categoria que no existe donde la puso: esa lectura esta
    # confundida, y proponer solo el libro seria adivinar sobre ella.
    confundida = bool(d.get('categoria')) and not categoria and not nueva
    if accion == 'proponer' and (
        confundida or not _algo_que_aplicar(lectura, p, libro)
    ):
        lectura['accion'] = 'preguntar'
    if lectura['accion'] == 'preguntar' and not lectura['pregunta']:
        lectura['pregunta'] = _pregunta_por_defecto(lectura, libros)
    return lectura


def _algo_que_aplicar(lectura: dict[str, Any], p: Any, libro: Any) -> bool:
    if libro is None:
        return False
    if lectura['categoria'] or lectura['categoria_nueva'] or lectura['prestamo_con']:
        return True
    # Contesto solo a que libro va: eso ya es una decision, y la categoria se
    # pregunta despues por el camino de siempre.
    return not p['libro_id']


def _pregunta_por_defecto(lectura: dict[str, Any], libros: list[dict[str, Any]]) -> str:
    if lectura['libro_id'] is None and len(libros) > 1:
        return '¿A qué libro va: ' + ' o '.join(lb['nombre'] for lb in libros) + '?'
    return '¿En qué categoría lo pongo?'


# ----------------------------------------------------------- lo mostrado


def guardar(alm: Almacen, pendiente_id: int, lectura: dict[str, Any]) -> None:
    alm.guardar_lectura(pendiente_id, json.dumps(lectura, ensure_ascii=False))


def leer(alm: Almacen, pendiente_id: int) -> dict[str, Any] | None:
    crudo = alm.lectura(pendiente_id)
    return json.loads(crudo) if crudo else None


def en_palabras(lectura: dict[str, Any]) -> str:
    """Lo que se le propuso, en una linea. Va al hilo para que, si corrige, el
    modelo sepa que fue lo que se le mostro."""
    if lectura['accion'] == 'descartar':
        return 'Propuse descartarlo: no fue un movimiento.'
    partes = []
    if lectura['libro']:
        partes.append(f'libro {lectura["libro"]}')
    if lectura['prestamo_con']:
        partes.append(f'préstamo con {lectura["prestamo_con"]}')
    if lectura['categoria']:
        partes.append(f'categoría {lectura["categoria"]}')
    if lectura['categoria_nueva']:
        partes.append(f'categoría nueva {lectura["categoria_nueva"]}')
    if lectura['presupuesto']:
        partes.append(f'presupuesto {lectura["presupuesto"]}')
    if lectura['comercio']:
        partes.append(f'comercio {lectura["comercio"]}')
    return 'Propuse: ' + ', '.join(partes) + '.'


def cerrar(alm: Almacen, p: Any, lectura: dict[str, Any]) -> None:
    """Confirmada: lo que conto de la contraparte queda para la proxima, y el
    hilo se cierra. La nota se guarda aunque sea «solo esta vez»: quien es la
    contraparte no cambia porque esta vez el gasto haya sido otro."""
    clave = clave_de(p)
    if lectura.get('nota') and clave:
        alm.guardar_nota(p['usuario_id'], clave, lectura['nota'])
    alm.cerrar_dialogo(p['id'])
