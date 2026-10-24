"""Que categoria dice la factura. Cada caso es una factura real de octubre de
2026, con los grupos que les dio el catalogo."""

from __future__ import annotations

from finanzas.dominio.por_factura import Linea, categoria

COMIDA = ('Comida preparada', 'Comida preparada')
DULCE = ('Alimentacion', 'Snacks y dulces')
SIN = ('Sin clasificar', 'Sin clasificar')


def _l(desc, grupo_cat, total):
    return Linea(desc, grupo_cat[0], grupo_cat[1], total)


def test_el_almuerzo_es_restaurante():
    assert categoria([_l('Menu Casero', COMIDA, 16204)]) == (
        'Restaurante',
        'Menu Casero',
    )


def test_el_almuerzo_con_donas_tambien():
    r = categoria([_l('Menu Casero', COMIDA, 16204), _l('DONA CUBIERTA', COMIDA, 2315)])
    assert r[0] == 'Restaurante'


def test_crispetas_gaseosa_y_ponque_es_mecato_aunque_la_gaseosa_no_este_clasificada():
    r = categoria(
        [
            _l('Crispetas Caramelo', DULCE, 4445),
            _l('Soda Frambuesa Ros', SIN, 2975),
            _l('Ponque Cubiert De', DULCE, 2622),
        ]
    )
    assert r[0] == 'Mecato'


def test_un_mercado_con_un_almuerzo_adentro_sigue_siendo_mercado():
    assert (
        categoria(
            [
                _l('Menu Casero', COMIDA, 16204),
                _l('Leche', ('Alimentacion', 'Lacteos y huevos'), 30000),
                _l('Arroz', ('Alimentacion', 'Abarrotes'), 40000),
            ]
        )
        is None
    )


def test_pan_y_bebidas_sin_dulce_no_es_mecato():
    """Bebidas de hidratacion el dia del trasteo: no hay con que decir."""
    assert (
        categoria(
            [
                _l('Bebida Hidra', ('Alimentacion', 'Bebidas'), 2933),
                _l('Bebida Jugo', ('Alimentacion', 'Bebidas'), 2857),
                _l('Soda', ('Alimentacion', 'Abarrotes'), 2100),
            ]
        )
        is None
    )


def test_si_casi_nada_esta_clasificado_no_decide():
    assert categoria([_l('Galleta', DULCE, 1000), _l('Cosa', SIN, 9000)]) is None


def test_lo_de_la_gata():
    assert categoria([_l('Arena', ('Mascotas', 'Gato'), 50000)])[0] == 'Gato'


def test_factura_vacia():
    assert categoria([]) is None
