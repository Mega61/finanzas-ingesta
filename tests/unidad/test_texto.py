"""Pruebas de normalizacion de comercios.

Casi todos estos casos son bugs que ya ocurrieron en produccion. Cada uno tiene
el sintoma escrito, para que se entienda que se pierde si la regla se quita.
"""

from __future__ import annotations

import pytest

from finanzas.dominio import texto


class TestPasarelas:
    def test_mercado_pago_con_espacio(self):
        """EL bug: la regex tenia 'MERCADOPAGO' sin espacio, y el banco manda
        'MERCADO PAGO*ZONAFIT'. La palabra MERCADO se colaba como comercio y
        cazaba con 'MERCADO LIBRE', asi que un gimnasio quedo clasificado como
        comida de gato, con confianza 0.80 o sea SIN preguntar.
        """
        assert texto.normalizar('MERCADO PAGO*ZONAFIT') == 'ZONAFIT'
        assert texto.normalizar('MERCADO PAGO*TIERRAG') == 'TIERRAG'

    def test_mercado_libre_no_es_una_pasarela(self):
        """No se puede quitar 'MERCADO' a lo bruto: Mercado Libre es un
        comercio real."""
        assert 'LIBRE' in texto.normalizar('MERCADO LIBRE')

    def test_pasarela_como_prefijo(self):
        assert texto.normalizar('DLO*Didi') == 'DIDI'
        assert texto.normalizar('PAYU*CINEMARK') == 'CINEMARK'

    def test_pasarela_como_sufijo(self):
        """El sufijo es igual de comun que el prefijo, y solo se manejaba uno."""
        assert texto.normalizar('UBER RIDES*DL') == 'UBER RIDES'


class TestRuido:
    def test_numero_de_local(self):
        assert texto.normalizar('CYCLE GEAR N169') == 'CYCLE GEAR'
        assert texto.normalizar('DROGUERIA ALEMANA 47') == 'DROGUERIA ALEMANA'

    def test_un_numero_solo_NO_se_borra(self):
        """En una transferencia a la cuenta '6985' el numero ES la identidad.
        Borrarlo dejaba la clave vacia y el movimiento sin nada con que
        emparejarse.
        """
        assert texto.normalizar('6985') == '6985'
        assert texto.normalizar('0089201610') == '0089201610'

    def test_sufijos_de_sociedad(self):
        assert 'SAS' not in texto.normalizar('NEUROMEDICA S A S')

    def test_tildes(self):
        assert texto.normalizar('Grupo Éxito') == 'GRUPO EXITO'
        assert texto.sin_tildes('Transporte Aplicación') == 'Transporte Aplicacion'


class TestTokens:
    def test_ignora_palabras_cortas(self):
        assert texto.tokens('LA DE EL SUSHI') == {'SUSHI'}

    def test_distintivos_excluye_las_vagas(self):
        """'MERCADO' y 'TIENDA' aparecen en cualquier comercio: compartirlas no
        dice nada. Sin este filtro, cualquier 'TIENDA X' cazaba con cualquier
        'TIENDA Y'."""
        assert texto.tokens_distintivos('TIENDA D1 SABANETA') == {'SABANETA'}
        assert 'MERCADO' not in texto.tokens_distintivos('MERCADO LIBRE')
        assert 'LIBRE' in texto.tokens_distintivos('MERCADO LIBRE')


class TestEsNumerico:
    @pytest.mark.parametrize('t', ['6985', '0089201610', '1000366000'])
    def test_llaves_qr_y_cuentas(self, t):
        assert texto.es_numerico(t)

    @pytest.mark.parametrize('t', ['ZONAFIT', 'TIENDA D1 47', ''])
    def test_lo_demas_no(self, t):
        assert not texto.es_numerico(t)


class TestInvariantes:
    @pytest.mark.parametrize(
        'entrada',
        ['', None, '   ', '***', '$$$', 'MERCADO PAGO*', '*DL'],
    )
    def test_nunca_estalla(self, entrada):
        """normalizar corre sobre texto que manda el banco, que a veces viene
        raro. Nunca debe lanzar: devuelve cadena vacia y el movimiento se
        pregunta."""
        assert isinstance(texto.normalizar(entrada), str)

    def test_es_idempotente(self):
        """Normalizar dos veces tiene que dar lo mismo. Si no, cualquier
        comparacion depende de cuantas veces se paso por la funcion."""
        for entrada in ['MERCADO PAGO*ZONAFIT', 'UBER RIDES*DL', 'Grupo Éxito',
                        'CYCLE GEAR N169', '6985']:
            una = texto.normalizar(entrada)
            assert texto.normalizar(una) == una, entrada
