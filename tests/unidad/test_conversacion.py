"""Lo que dice el modelo, recortado a lo que existe: `conversacion.validar`.

Pura: sin base, sin Telegram, sin Gemini. Cada caso es una lectura real que el
modelo devolvio, o la forma de una que no puede pasar.
"""

from __future__ import annotations

from finanzas.aplicacion.conversacion import validar

PERSONAL = {'id': 1, 'nombre': 'Personal', 'categorias': ['Arriendo', 'Facturas']}
ESTUDIO = {'id': 2, 'nombre': 'Golden Beauty', 'categorias': ['Insumos', 'Otros']}


def _p(libro_id=None):
    return {'libro_id': libro_id}


def _leer(d, libros=(PERSONAL, ESTUDIO), p=None, **k):
    base = {'siempre': False, 'confianza': 0.9, 'razon': ''}
    return validar(
        {**base, **d},
        p or _p(),
        list(libros),
        k.get('presupuestos', []),
        k.get('comercios', []),
        k.get('con_prestamos', False),
    )


def test_la_nueva_gana_sobre_la_existente_de_relleno():
    """Lo que devolvio Gemini con «es doña Marta, la señora del aseo»."""
    lec = _leer(
        {
            'accion': 'proponer',
            'categoria': 'Facturas',
            'categoria_nueva': 'Servicio Doméstico',
        },
        libros=[PERSONAL],
        p=_p(1),
    )
    assert (lec['categoria'], lec['categoria_nueva']) == (None, 'Servicio Doméstico')
    assert lec['accion'] == 'proponer'


def test_una_nueva_que_ya_existe_es_la_existente():
    lec = _leer(
        {'accion': 'proponer', 'categoria_nueva': 'arriendo'},
        libros=[PERSONAL],
        p=_p(1),
    )
    assert (lec['categoria'], lec['categoria_nueva']) == ('Arriendo', None)


def test_sin_libro_no_hay_nueva_y_pregunta_a_cual():
    lec = _leer({'accion': 'proponer', 'categoria_nueva': 'Nómina'})
    assert lec['accion'] == 'preguntar'
    assert lec['pregunta'] == '¿A qué libro va: Personal o Golden Beauty?'


def test_un_libro_que_no_es_suyo_no_pasa():
    lec = _leer(
        {'accion': 'proponer', 'libro': 'Firefly de Juan', 'categoria': 'Otros'}
    )
    assert lec['libro_id'] == 2, 'Otros solo existe en el estudio: ese es el libro'


def test_una_categoria_en_los_dos_libros_sin_libro_pregunta():
    con_otros = {**PERSONAL, 'categorias': ['Otros']}
    lec = _leer(
        {'accion': 'proponer', 'categoria': 'Otros'}, libros=[con_otros, ESTUDIO]
    )
    assert lec['accion'] == 'preguntar' and lec['libro_id'] is None


def test_el_prestamo_solo_si_el_libro_lo_admite():
    d = {'accion': 'proponer', 'libro': 'Personal', 'prestamo_con': 'mi hermana'}
    assert _leer(d, con_prestamos=True)['prestamo_con'] == 'Hermana'
    assert _leer(d, con_prestamos=False)['prestamo_con'] is None


def test_presupuesto_y_comercio_inventados_no_pasan():
    lec = _leer(
        {
            'accion': 'proponer',
            'categoria': 'Arriendo',
            'presupuesto': 'Lujos',
            'comercio': 'Otro',
        },
        libros=[PERSONAL],
        p=_p(1),
        presupuestos=['Esencial'],
        comercios=['Doña Luz'],
    )
    assert (lec['presupuesto'], lec['comercio']) == (None, None)
    assert lec['categoria'] == 'Arriendo'


def test_una_accion_desconocida_es_nada():
    assert _leer({'accion': 'borrar_todo'})['accion'] == 'nada'
