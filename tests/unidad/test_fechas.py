"""Pruebas de fechas y horas.

El riesgo real de este modulo son las zonas horarias: el banco manda hora local
de Colombia, Graph devuelve UTC, y el contenedor corre en America/Bogota. Un
naive comparado con un aware lanza; dos naive de relojes distintos dan un
resultado corrido un dia, en silencio.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from finanzas.dominio import fechas


class TestAFecha:
    @pytest.mark.parametrize(
        ('entrada', 'esperado'),
        [
            ('2026-09-01', date(2026, 9, 1)),
            ('01/09/2026', date(2026, 9, 1)),
            ('01/09/26', date(2026, 9, 1)),
            ('2026/09/01', date(2026, 9, 1)),
            ('2026-09-01T15:11:00Z', date(2026, 9, 1)),
            ('2026-09-01T15:11:00+02:00', date(2026, 9, 1)),
            (date(2026, 9, 1), date(2026, 9, 1)),
            (datetime(2026, 9, 1, 23, 30), date(2026, 9, 1)),
        ],
    )
    def test_formatos_que_manda_el_banco(self, entrada, esperado):
        assert fechas.a_fecha(entrada) == esperado

    @pytest.mark.parametrize('malo', [None, '', 'nada', '99/99/9999', '   '])
    def test_lo_que_no_es_fecha_devuelve_None(self, malo):
        """Devuelve None en vez de lanzar: el texto viene del banco y a veces
        viene raro. El movimiento se pregunta, no se tumba la pasada."""
        assert fechas.a_fecha(malo) is None

    def test_dia_y_mes_no_se_confunden(self):
        """'01/09/2026' es 1 de septiembre en formato colombiano, no 9 de enero."""
        f = fechas.a_fecha('01/09/2026')
        assert (f.day, f.month) == (1, 9)


class TestAInstante:
    def test_siempre_devuelve_aware(self):
        """Es el punto del modulo: nada naive sale de aqui."""
        for entrada in [
            '2026-09-01T15:11:00Z',
            '2026-09-01T15:11:00',
            '2026-09-01',
            datetime(2026, 9, 1, 10, 0),
        ]:
            d = fechas.a_instante(entrada)
            assert d is not None and d.tzinfo is not None, entrada

    def test_respeta_la_zona_que_ya_traia(self):
        d = fechas.a_instante('2026-09-01T15:11:00Z')
        assert d.utcoffset() == timedelta(0)

    def test_sin_zona_asume_bogota(self):
        d = fechas.a_instante('2026-09-01T15:11:00')
        assert d.utcoffset() == timedelta(hours=-5)

    def test_se_pueden_comparar_entre_si(self):
        """Un naive y un aware no se pueden comparar: lanza TypeError. Si todo
        sale aware, comparar siempre funciona."""
        de_graph = fechas.a_instante('2026-09-01T20:11:00Z')
        del_banco = fechas.a_instante('2026-09-01T15:11:00')
        assert de_graph == del_banco, 'son el mismo instante en zonas distintas'

    def test_las_22_en_bogota_son_del_dia_siguiente_en_utc(self):
        """El caso que corre las fechas un dia si se ignora la zona."""
        d = fechas.a_instante('2026-09-01T22:00:00')
        assert d.astimezone(UTC).date() == date(2026, 9, 2)
        assert d.date() == date(2026, 9, 1), 'en Bogota sigue siendo el 1'


class TestOrdenarFechaHora:
    def test_invertidas(self):
        """Varias plantillas traen 'el 07:57 a las 10/12/2025'. Confiar en la
        posicion pone la hora como fecha."""
        assert fechas.ordenar_fecha_hora('07:57', '10/12/2025') == (
            '10/12/2025',
            '07:57',
        )

    def test_en_orden_normal(self):
        assert fechas.ordenar_fecha_hora('10/12/2025', '07:57') == (
            '10/12/2025',
            '07:57',
        )

    def test_con_segundos(self):
        assert fechas.ordenar_fecha_hora('10:25:05', '21/11/2025') == (
            '21/11/2025',
            '10:25:05',
        )


class TestPeriodoEspanol:
    def test_formato_de_los_extractos_2026(self):
        """Los extractos de 2026 no usan 'Desde:/Hasta:' sino espanol
        abreviado. Sin esto no habia periodo y no se podia decidir si un cargo
        era fantasma."""
        assert fechas.periodo_espanol('30 jul - 30 ago. 2026') == (
            date(2026, 7, 30),
            date(2026, 8, 30),
        )

    def test_cruzando_el_ano_nuevo(self):
        """Si el primer mes es mayor que el segundo, el periodo empieza el ano
        anterior."""
        assert fechas.periodo_espanol('30 dic - 30 ene. 2027') == (
            date(2026, 12, 30),
            date(2027, 1, 30),
        )

    def test_dentro_de_texto_largo(self):
        txt = 'Deuda a la fecha de corte: 15 jul - 17 ago. 2026  Pago Total:'
        assert fechas.periodo_espanol(txt) == (date(2026, 7, 15), date(2026, 8, 17))

    @pytest.mark.parametrize('malo', ['', 'sin periodo', '30 xyz - 30 abc. 2026'])
    def test_sin_periodo(self, malo):
        assert fechas.periodo_espanol(malo) == (None, None)


class TestLimitesDeMes:
    @pytest.mark.parametrize(
        ('d', 'esperado'),
        [
            (date(2026, 2, 10), date(2026, 2, 28)),
            (date(2024, 2, 10), date(2024, 2, 29)),  # bisiesto
            (date(2026, 12, 5), date(2026, 12, 31)),
            (date(2026, 4, 30), date(2026, 4, 30)),
        ],
    )
    def test_fin_de_mes(self, d, esperado):
        assert fechas.fin_de_mes(d) == esperado

    def test_inicio_de_mes(self):
        assert fechas.inicio_de_mes(date(2026, 9, 17)) == date(2026, 9, 1)


class TestDiasEntre:
    def test_es_absoluto(self):
        assert fechas.dias_entre('2026-09-01', '2026-08-30') == 2
        assert fechas.dias_entre('2026-08-30', '2026-09-01') == 2

    def test_con_alguna_invalida(self):
        assert fechas.dias_entre('2026-09-01', None) is None


class TestReloj:
    def test_hoy_usa_la_zona_de_colombia(self):
        """Si el servidor esta en otra zona, `hoy()` tiene que dar el dia de
        Colombia, no el del servidor."""
        assert fechas.hoy() == datetime.now(fechas.BOGOTA).date()

    def test_ahora_es_aware(self):
        assert fechas.ahora().tzinfo is not None

    def test_bogota_no_tiene_horario_de_verano(self):
        """Colombia esta en UTC-5 todo el ano. Si esto cambia, media logica de
        fechas hay que revisarla."""
        for mes in (1, 4, 7, 10):
            d = datetime(2026, mes, 15, tzinfo=fechas.BOGOTA)
            assert d.utcoffset() == timedelta(hours=-5), mes
