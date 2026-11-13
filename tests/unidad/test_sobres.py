"""Los sobres de mercado. El caso: el 13 de noviembre de 2026, con facturas
de la quincena pasada (16-31 oct), de esta (1-15 nov) y una que todavia no
deberia contar (16 nov)."""

from datetime import date

import pytest

from finanzas.aplicacion import sobres as s

HOY = date(2026, 11, 13)


def _l(fecha, total, grupo, categoria, desc='Producto', codigo=None, signo=1):
    return {
        'fecha': fecha,
        'signo': signo,
        'nit': '890900608',
        'codigo': codigo or desc,
        'descripcion': desc,
        'total': total,
        'grupo': grupo,
        'categoria': categoria,
    }


ALIM = 'Alimentacion'
LINEAS = [
    # quincena pasada, y el mes pasado para Aseo
    _l('2026-10-15', 999_000, ALIM, 'Carnes y pollo', 'Lomo de antes'),
    _l('2026-10-20', 120_000, ALIM, 'Carnes y pollo', 'Lomo'),
    _l('2026-10-31', 30_000, ALIM, 'Frutas y verduras', 'Banano'),
    _l('2026-10-05', 45_000, 'Aseo y hogar', 'Limpieza', 'Detergente'),
    # esta quincena
    _l('2026-11-01', 60_000, ALIM, 'Carnes y pollo', 'Pechuga'),
    _l('2026-11-03', 92_560, ALIM, 'Pescados y mariscos', 'Salmon'),
    _l('2026-11-08', 40_000, ALIM, 'Carnes y pollo', 'Pechuga'),
    _l('2026-11-08', 50_000, ALIM, 'Frutas y verduras', 'Aguacate'),
    _l('2026-11-08', 150_000, ALIM, 'Lacteos y huevos', 'Queso'),
    _l('2026-11-08', 40_000, 'Licores', 'Licores', 'Vino'),
    _l('2026-11-08', 15_000, 'Comida preparada', 'Comida preparada', 'Empanada'),
    _l('2026-11-11', 12_000, 'Sin clasificar', 'Sin clasificar', 'Cosa rara'),
    _l('2026-11-11', 9_000, None, None, 'Sin catalogo'),
    _l('2026-11-02', 70_000, 'Cuidado personal', 'Cabello', 'Shampoo'),
    _l('2026-11-02', 900, 'Servicios', 'Bolsas y empaques', 'Bolsa'),
    _l('2026-11-02', 40_000, 'Aseo y hogar', 'Limpieza', 'Jabon'),
    # nota credito: devolvieron el salmon a medias
    _l('2026-11-04', 20_000, ALIM, 'Pescados y mariscos', 'Salmon', signo=-1),
    # no son de ningun sobre
    _l('2026-11-05', 500_000, 'Electrodomesticos', 'Electrodomesticos', 'Licuadora'),
    _l('2026-11-05', 80_000, 'Hogar', 'Hogar', 'Vasos'),
    _l('2026-11-05', 30_000, 'Mascotas', 'Gato', 'Fancy Feast'),
    _l('2026-11-05', 70_000, 'No es mercado', 'No es mercado', 'Gimnasio'),
    # despues de hoy: no deberia estar, y si esta no cuenta
    _l('2026-11-16', 77_000, ALIM, 'Carnes y pollo', 'Futuro'),
]


def _por(x, clave):
    return next(o for o in x.sobres if o.definicion.clave == clave)


@pytest.fixture
def x():
    return s.calcular(LINEAS, HOY)


@pytest.mark.parametrize(
    ('dia', 'desde', 'hasta'),
    [
        (date(2026, 11, 1), date(2026, 11, 1), date(2026, 11, 15)),
        (date(2026, 11, 15), date(2026, 11, 1), date(2026, 11, 15)),
        (date(2026, 11, 16), date(2026, 11, 16), date(2026, 11, 30)),
        (date(2026, 2, 20), date(2026, 2, 16), date(2026, 2, 28)),
    ],
)
def test_la_quincena(dia, desde, hasta):
    assert s.quincena(dia) == (desde, hasta)


def test_la_quincena_anterior_cruza_el_mes_y_el_ano():
    assert s.quincena_anterior(date(2026, 11, 3)) == (
        date(2026, 10, 16),
        date(2026, 10, 31),
    )
    assert s.quincena_anterior(date(2026, 11, 20)) == (
        date(2026, 11, 1),
        date(2026, 11, 15),
    )
    assert s.quincena_anterior(date(2027, 1, 2)) == (
        date(2026, 12, 16),
        date(2026, 12, 31),
    )


def test_carne_suma_pollo_y_pescado_y_la_nota_credito_resta(x):
    carne = _por(x, 'carne')
    assert carne.gastado == 60_000 + 92_560 + 40_000 - 20_000
    assert carne.top() == [('Pechuga', 100_000), ('Salmon', 72_560)]


def test_el_15_de_octubre_es_otra_quincena(x):
    assert _por(x, 'carne').anterior == 120_000
    assert _por(x, 'fruver').anterior == 30_000


def test_alacena_lleva_el_resto_y_lo_sin_clasificar(x):
    alacena = _por(x, 'alacena')
    assert alacena.gastado == 150_000 + 40_000 + 15_000 + 12_000 + 9_000
    assert alacena.sin_clasificar == 2
    assert x.sin_clasificar == 2


def test_aseo_va_por_mes(x):
    aseo = _por(x, 'aseo')
    assert (aseo.desde, aseo.hasta) == (date(2026, 11, 1), date(2026, 11, 30))
    assert aseo.gastado == 70_000 + 900 + 40_000
    assert aseo.anterior == 45_000
    assert aseo.pasado


def test_hogar_mascotas_y_lo_que_no_es_mercado_no_cuentan(x):
    total = sum(o.gastado for o in x.sobres)
    assert total == 172_560 + 50_000 + 226_000 + 110_900


def test_lo_de_despues_de_hoy_no_cuenta_ni_mueve_la_fecha(x):
    assert x.ultima_factura == date(2026, 11, 11)


def test_en_la_segunda_quincena_la_primera_es_la_anterior():
    x = s.calcular(LINEAS, date(2026, 11, 20))
    carne = _por(x, 'carne')
    assert carne.gastado == 77_000
    assert carne.anterior == 60_000 + 92_560 + 40_000 - 20_000
    # Aseo sigue en el mismo mes
    assert _por(x, 'aseo').gastado == 110_900


def test_el_bloque_de_hoy(x):
    t = s.texto_bloque(x)
    assert '🥩 Carne 172.560 / 200.000' in t
    assert '🧴 Aseo y casa 110.900 / 100.000 al mes ⚠️' in t
    assert '🥬 Fruver 50.000 / 200.000\n' in t
    assert 'facturas hasta el 11-nov' in t
    assert '2 sin clasificar' in t


def test_sin_facturas_lo_dice():
    t = s.texto_bloque(s.calcular([], HOY))
    assert 'sin facturas todavía' in t
    assert '⚠️' not in t


def test_mercado_trae_el_top_y_la_quincena_pasada(x):
    t = s.texto_mercado(x)
    assert '· Pechuga 100.000' in t
    assert 'Lomo' not in t.split('Quincena pasada')[0]
    pasada = t.split('Quincena pasada')[1]
    assert '🥩 Carne 120.000 / 200.000' in pasada
    assert '🧴 Aseo y casa 45.000 / 100.000 (mes 1–31 oct)' in pasada  # noqa: RUF001


def test_cada_linea_cae_en_su_sobre():
    assert s.sobre_de('Alimentacion', 'Frutas y verduras') is s.FRUVER
    assert s.sobre_de('Alimentacion', 'Bebidas') is s.ALACENA
    assert s.sobre_de('Servicios', 'Domicilio') is s.ASEO
    assert s.sobre_de(None, None) is s.ALACENA
    assert s.sobre_de('Tecnologia', 'Tecnologia') is None
