"""El extracto de la Nu, sobre un texto con la misma forma que el PDF.

No se usa el PDF de verdad: trae el nombre, la cedula y la direccion de una
persona. Las lineas de aqui copian su forma -- el ano a veces en la misma
linea y a veces en la siguiente, el desglose de un pago, una compra a cuotas
de un periodo anterior -- con montos inventados.
"""

from __future__ import annotations

from datetime import date

from finanzas.parsers import extracto_nu

TEXTO = """
    Hola,  Persona
    Llegó tu extracto de Septiembre
    Persona De Prueba                  Tus tarjetas
    Calle falsa 123                    • • • • 1234    • • • • 9999
                     Fecha límite de pago Fecha de corte   Periodo facturado
                     19 OCT 2026       28 SEP 2026         29 AGO 2026 - 27 SEP
      Tu cupo definido $1.000.000,00
                                        PAGO MÍNIMO                    $120.500,00
                                        DEUDA TOTAL HASTA EL 27 SEPTIEMBRE $300.250,50
=====
    Fecha  Descripción Valor Cuotas Valor   Interés del mes Total a pagar Restante
    27 SEP Pago      $90.000,00                           -$90.000,00 $0,00
                     ↪ A capital $90.000,00
    2026
                     ↪ A intereses $0,00
    26 SEP Saltamontes $9.000,00 1 de 1 $9.000,00 2.13% $0,00 $9.000,00 $0,00
    2026   Coffee
    21 SEP 2026 Didi $4.200,00 1 de 1 $4.200,00 2.13% $0,00 $4.200,00 $0,00
    30 AGO Didi      $4.500,00      $4.500,00 2.16% $0,00 $4.500,00   $0,00
    2026
    21 JUN Gimnasio  $94.900,00     $7.908,33 2.10% $897,25 $8.805,58 $63.266,68
    2026   Sede Sur
"""


def _ext():
    return extracto_nu.parse_texto(TEXTO, 'nu.pdf')


def test_el_resumen_trae_la_deuda_al_corte():
    e = _ext()
    assert e.instrumento == '1234', 'la fisica, no la virtual'
    assert (e.corte, e.limite_de_pago) == (date(2026, 9, 28), date(2026, 10, 19))
    assert (e.desde, e.hasta) == (date(2026, 8, 29), date(2026, 9, 27))
    assert e.deuda_total == 300250.50
    assert e.pago_minimo == 120500.0
    assert e.cupo == 1000000.0
    assert e.error is None


def test_el_ano_en_la_misma_linea_o_en_la_siguiente():
    movs = [(m.fecha, m.descripcion) for m in _ext().movimientos]
    assert (date(2026, 9, 26), 'Saltamontes Coffee') in movs, (
        'la descripcion sigue en el renglon del ano'
    )
    assert (date(2026, 9, 21), 'Didi') in movs
    assert (date(2026, 8, 30), 'Didi') in movs


def test_un_pago_es_positivo_y_su_desglose_no_es_movimiento():
    movs = _ext().movimientos
    (pago,) = [m for m in movs if m.descripcion == 'Pago']
    assert (pago.fecha, pago.valor) == (date(2026, 9, 27), 90000.0)
    assert not any('capital' in m.descripcion for m in movs)
    assert all(m.valor < 0 for m in movs if m.descripcion != 'Pago')


def test_la_compra_a_cuotas_de_junio_no_es_del_periodo():
    e = _ext()
    (gym,) = [m for m in e.lineas if m.descripcion.startswith('Gimnasio')]
    assert (gym.valor, gym.restante, gym.a_cuotas) == (-94900.0, 63266.68, True)
    assert [m.en_periodo for m in e.movimientos] == [True, True, True, True, False]


def test_sin_movimientos_lo_dice():
    assert extracto_nu.parse_texto('otra cosa', 'x.pdf').error
