"""Que categoria dice la factura. Cada caso es una factura real de octubre de
2026, con los grupos que les dio el catalogo."""

from __future__ import annotations

from finanzas.dominio.por_factura import Linea, categoria, mezcla

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


MEDICINA = ('Cuidado personal', 'Salud')
SERVICIO = ('Servicios', 'Domicilio')


def test_la_drogueria_es_medicamentos():
    r = categoria(
        [
            _l('Fraccion Ibuprofeno Genfar 800Mg', MEDICINA, 4250),
            _l('Fraccion Amoxicilina 500Mg', MEDICINA, 4950),
            _l('Servicio Domicilio Web', SERVICIO, 9000),
        ]
    )
    assert r[0] == 'Medicamentos'


def test_farmatodo_con_mecato_y_pastillas_no_se_toca():
    """La factura real del 1-nov: galletas, chocolatina, ibuprofeno,
    amoxicilina y el domicilio. Mezclada: se queda como estaba."""
    r = categoria(
        [
            _l('Servicio Domicilio Web', SERVICIO, 9000),
            _l('Fraccion Ibuprofeno Genfar 800Mg', MEDICINA, 4250),
            _l('$Galletas Oreo Cobertura', DULCE, 15924),
            _l('Fraccion Amoxicilina 500Mg', MEDICINA, 4950),
            _l('Chocolatina Jumbo Mani', DULCE, 7941),
        ]
    )
    assert r is None


def test_la_mezcla_de_farmatodo_trae_sus_opciones():
    """La del 1-nov otra vez: no se decide, pero se puede preguntar con lo que
    trae, de lo que mas pesa a lo que menos. El domicilio no vota."""
    r = mezcla(
        [
            _l('Servicio Domicilio Web', SERVICIO, 9000),
            _l('Fraccion Ibuprofeno Genfar 800Mg', MEDICINA, 4250),
            _l('$Galletas Oreo Cobertura', DULCE, 15924),
            _l('Fraccion Amoxicilina 500Mg', MEDICINA, 4950),
            _l('Chocolatina Jumbo Mani', DULCE, 7941),
        ]
    )
    assert [c for c, _p, _r in r] == ['Mecato', 'Medicamentos']
    assert round(r[0][1], 2) == 0.72


def test_lo_que_pesa_poco_no_sale_de_opcion():
    r = mezcla(
        [
            _l('Shampoo Fructis', ('Cuidado personal', 'Cabello'), 28000),
            _l('Protector Solar Fps 50', ('Cuidado personal', 'Piel'), 52000),
            _l('Toallas de Cocina', ('Aseo y hogar', 'Limpieza'), 12000),
            _l('Chicle', DULCE, 2000),
        ]
    )
    assert [c for c, _p, _r in r] == ['Cuidado personal'], 'el chicle no es opcion'
