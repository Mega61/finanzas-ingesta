"""Pruebas del emparejamiento libro vs extracto.

La prueba que importa es `test_no_corrige_cuando_hay_varios_candidatos`: es el
bug que casi cambio montos al azar en el libro contable. Si alguien "mejora" el
emparejador para que sea mas agresivo, esa prueba lo detiene.

Todo aqui corre sin red y sin disco: son listas de datos.
"""

from __future__ import annotations

from datetime import date

import pytest

from finanzas.dominio.conciliacion import (
    Clase,
    Linea,
    emparejar,
    es_fantasma,
    tasa_implicita,
)


def L(
    dia: int,
    valor: float,
    desc: str = 'UBER RIDES',
    moneda: str = 'COP',
    mes: int = 8,
    ref=None,
) -> Linea:
    """Atajo para armar lineas de prueba."""
    return Linea(date(2026, mes, dia), valor, moneda, desc, ref)


class TestMontoExacto:
    def test_empareja_el_mismo_dia(self):
        r = emparejar([L(19, -17442)], [L(19, -17442)])
        assert len(r.pares) == 1
        assert r.pares[0].clase is Clase.IGUAL
        assert not r.sin_pareja

    def test_tolera_centavos(self):
        """La alerta y el extracto difieren en centavos por redondeo."""
        r = emparejar([L(19, -17442.00)], [L(19, -17442.35)])
        assert r.pares[0].clase is Clase.IGUAL

    def test_tolerancia_de_fecha_creciente(self):
        """El cargo puede caer dias despues de la alerta."""
        r = emparejar([L(19, -17442)], [L(24, -17442)])
        assert r.pares[0].clase is Clase.IGUAL

    def test_mas_alla_de_45_dias_no_empareja(self):
        r = emparejar([L(1, -17442, mes=6)], [L(30, -17442, mes=8)])
        assert not r.pares
        assert len(r.sin_pareja) == 1

    def test_gana_el_mas_cercano_en_fecha(self):
        cerca, lejos = L(20, -17442, ref='cerca'), L(28, -17442, ref='lejos')
        r = emparejar([L(19, -17442)], [lejos, cerca])
        assert r.pares[0].extracto.ref == 'cerca'

    def test_cada_linea_del_extracto_se_consume_una_vez(self):
        """Dos alertas iguales no pueden emparejarse al mismo cargo: eso
        esconderia un cargo duplicado de verdad."""
        r = emparejar([L(19, -17442), L(19, -17442)], [L(19, -17442)])
        iguales = [p for p in r.pares if p.clase is Clase.IGUAL]
        assert len(iguales) == 1
        assert len(r.sin_pareja) == 1


class TestElBugDeLaCascada:
    def test_no_corrige_cuando_hay_varios_candidatos(self):
        """EL BUG. Cuatro Ubers en cuatro dias con montos distintos.

        La version vieja emparejaba cada uno con "el mas parecido" y encadenaba
        correcciones equivocadas, cambiando montos al azar en el libro. Ahora
        todos tienen que salir AMBIGUO, no corregidos.
        """
        libro = [L(19, -19457), L(20, -18828), L(21, -23440)]
        extracto = [L(19, -18828), L(20, -23440), L(21, -64617)]
        r = emparejar(libro, extracto)

        corregidos = [p for p in r.pares if p.clase is Clase.OTRO_MONTO]
        assert not corregidos, (
            'no puede corregir montos cuando hay varios candidatos del mismo '
            f'comercio; corrigio {len(corregidos)}'
        )

    def test_si_corrige_cuando_el_candidato_es_unico(self):
        """Una preautorizacion sola, con su cargo real cerca: eso si se
        corrige, es el caso legitimo."""
        r = emparejar([L(19, -13888, 'UBER RIDES')], [L(19, -13354, 'UBER RIDES')])
        assert len(r.pares) == 1
        assert r.pares[0].clase is Clase.OTRO_MONTO

    def test_no_corrige_si_la_diferencia_es_absurda(self):
        """Un candidato unico pero cinco veces mas grande no es el mismo
        consumo: eso es otra compra."""
        r = emparejar([L(19, -13888, 'UBER RIDES')], [L(19, -64617, 'UBER RIDES')])
        assert all(p.clase is not Clase.OTRO_MONTO for p in r.pares)

    def test_comercio_distinto_no_se_empareja_por_monto_parecido(self):
        r = emparejar(
            [L(19, -13888, 'UBER RIDES')], [L(19, -13354, 'FARMATODO SABANETA')]
        )
        assert not r.pares
        assert len(r.sin_pareja) == 1


class TestCruceDeMoneda:
    def test_sin_tasa_no_cruza_moneda(self):
        """Sin con que deducir la tasa, mejor no emparejar que emparejar con
        una tasa inventada."""
        r = emparejar(
            [L(11, -111083.54, 'AMAZON', 'COP')], [L(11, -34.7, 'AMAZON', 'USD')]
        )
        assert not r.pares
        assert len(r.sin_pareja) == 1

    def test_con_tasa_de_respaldo_si_cruza(self):
        r = emparejar(
            [L(11, -111083.54, 'AMAZON', 'COP')],
            [L(11, -34.7, 'AMAZON', 'USD')],
            tasa_respaldo=3202.0,
        )
        assert len(r.pares) == 1
        assert r.pares[0].clase is Clase.IGUAL

    def test_deduce_la_tasa_de_los_pares_que_coinciden(self):
        libro = [L(11, -111083.54, 'AMAZON'), L(12, -64040.00, 'MACYS')]
        extracto = [L(11, -34.7, 'AMAZON', 'USD'), L(12, -20.0, 'MACYS', 'USD')]
        tasa = tasa_implicita(libro, extracto)
        assert tasa is not None
        assert 3000 < tasa < 3400

    def test_una_tasa_fuera_de_banda_se_ignora(self):
        """Si la razon da 50.000 COP por dolar, no es una tasa: es que los dos
        montos no tienen nada que ver."""
        libro = [L(11, -1_000_000, 'AMAZON'), L(12, -2_000_000, 'MACYS')]
        extracto = [L(11, -20.0, 'AMAZON', 'USD'), L(12, -40.0, 'MACYS', 'USD')]
        assert tasa_implicita(libro, extracto) is None


class TestFantasma:
    HOY = date(2026, 9, 1)
    CIERRE = date(2026, 6, 30)

    def test_un_cargo_viejo_que_no_aparecio_es_fantasma(self):
        assert es_fantasma(L(1, -17442, mes=6), self.CIERRE, self.HOY)

    def test_un_cargo_reciente_todavia_no(self):
        """Puede aparecer en el extracto siguiente. Declararlo fantasma antes
        de tiempo borra plata que si se gasto."""
        assert not es_fantasma(L(28, -17442, mes=8), date(2026, 8, 30), self.HOY)

    def test_posterior_al_cierre_no_es_fantasma(self):
        """Si el cargo es de despues de que cerro el extracto, ese extracto no
        tenia por que traerlo."""
        assert not es_fantasma(L(15, -17442, mes=7), self.CIERRE, self.HOY)

    def test_sin_fecha_no_se_puede_decidir(self):
        assert not es_fantasma(Linea(None, -17442), self.CIERRE, self.HOY)


class TestInvariantes:
    def test_listas_vacias(self):
        r = emparejar([], [])
        assert not r.pares and not r.sin_pareja and not r.solo_en_extracto

    def test_solo_extracto(self):
        r = emparejar([], [L(19, -17442)])
        assert len(r.solo_en_extracto) == 1

    def test_no_muta_las_entradas(self):
        libro, extracto = [L(19, -17442)], [L(19, -17442)]
        copia_libro, copia_extracto = list(libro), list(extracto)
        emparejar(libro, extracto)
        assert libro == copia_libro
        assert extracto == copia_extracto

    def test_todo_movimiento_del_libro_termina_en_algun_lado(self):
        """Ninguna linea puede desaparecer: o empareja o queda sin pareja."""
        libro = [L(d, -1000 * d, f'COMERCIO {d}') for d in range(1, 12)]
        extracto = [L(d, -1000 * d, f'COMERCIO {d}') for d in range(1, 6)]
        r = emparejar(libro, extracto)
        assert len(r.pares) + len(r.sin_pareja) == len(libro)

    @pytest.mark.parametrize('monto', [0.0, -0.01, -1e9])
    def test_montos_extremos_no_estallan(self, monto):
        r = emparejar([L(19, monto)], [L(19, monto)])
        assert isinstance(r.pares, list)
