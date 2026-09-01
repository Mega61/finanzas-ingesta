"""Pruebas de montos.

Cada caso raro de aqui salio de un correo real del banco. Si alguien
"simplifica" parse_monto, estas pruebas dicen exactamente que se rompe.
"""

from __future__ import annotations

import pytest

from finanzas.dominio import dinero


class TestParseMonto:
    @pytest.mark.parametrize(
        ('texto', 'esperado'),
        [
            # formato colombiano: punto de miles, coma decimal
            ('178.679,08', 178679.08),
            ('2.301.652,00', 2301652.00),
            ('39.166,74', 39166.74),
            ('3,05', 3.05),
            # formato gringo: coma de miles, punto decimal
            ('205,967.00', 205967.00),
            ('3,819,700.00', 3819700.00),
            # sin decimales
            ('500', 500.0),
            ('9,000', 9000.0),
            ('2.000.000', 2000000.0),
            # con adornos
            ('$ 342.990,00', 342990.00),
            (' 1.000,50 ', 1000.50),
        ],
    )
    def test_los_dos_formatos(self, texto, esperado):
        assert dinero.parse_monto(texto) == pytest.approx(esperado)

    def test_nueve_mil_no_son_nueve(self):
        """El caso que mas duele: '9,000' con coma es nueve mil.

        Si se toma la coma como decimal quedan 9 pesos, y un gasto de nueve mil
        entra al libro como nueve.
        """
        assert dinero.parse_monto('9,000') == 9000.0
        assert dinero.parse_monto('9,00') == 9.0

    def test_negativos(self):
        """El formato 2026 de los extractos trae el signo DENTRO del monto:
        '$ -3.605.583,00' es un abono, no un cargo."""
        assert dinero.parse_monto('-3.605.583,00') == -3605583.00
        assert dinero.parse_monto('-2.000.000') == -2000000.0

    @pytest.mark.parametrize('malo', ['', '   ', 'abc', '$', None])
    def test_lo_que_no_es_un_monto_falla_claro(self, malo):
        with pytest.raises(ValueError):
            dinero.parse_monto(malo)


class TestFormatear:
    def test_pesos_sin_decimales_con_punto_de_miles(self):
        assert dinero.formatear(1234567.89) == '$1.234.568'
        assert dinero.formatear(0) == '$0'

    def test_dolares_con_dos_decimales(self):
        assert dinero.formatear(12.3456, 'USD') == 'US$12.35'

    def test_signo_explicito(self):
        assert dinero.formatear(-1234.5, con_signo=True) == '-$1.235'
        assert dinero.formatear(1234.5, con_signo=True) == '+$1.235'

    def test_el_medio_peso_sube(self):
        """Python redondea al par mas cercano, asi que f'{1234.5:,.0f}' da 1234.
        Para plata eso esta mal: el medio sube, y ademas con el redondeo bancario
        la misma cifra se ve distinta segun si termina en par o impar."""
        assert dinero.formatear(1234.5) == '$1.235'
        assert dinero.formatear(1235.5) == '$1.236'
        assert dinero.formatear(0.5) == '$1'

    def test_sin_signo_el_valor_va_en_absoluto(self):
        assert dinero.formatear(-500) == dinero.formatear(500)


class TestMismoMonto:
    def test_tolera_centavos(self):
        """La alerta y el extracto difieren en centavos por redondeo. Comparar
        con igualdad exacta hace que nada cuadre."""
        assert dinero.mismo_monto(17442.00, 17442.35)
        assert dinero.mismo_monto(-17442.00, 17442.00), 'el signo no importa'

    def test_no_tolera_diferencias_reales(self):
        assert not dinero.mismo_monto(17442.00, 18432.00)

    def test_tolerancia_configurable(self):
        assert dinero.mismo_monto(100, 150, tolerancia=60)
        assert not dinero.mismo_monto(100, 150, tolerancia=10)
