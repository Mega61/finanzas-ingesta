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
        for entrada in [
            'MERCADO PAGO*ZONAFIT',
            'UBER RIDES*DL',
            'Grupo Éxito',
            'CYCLE GEAR N169',
            '6985',
        ]:
            una = texto.normalizar(entrada)
            assert texto.normalizar(una) == una, entrada


class TestPasarelasSinAsterisco:
    """El banco manda la pasarela de DOS formas y solo se manejaba una.

    'MERCADO PAGO*TIERRAG' se limpiaba, pero 'BOLD CO ONLINE RTFE' no, porque
    no traia asterisco. Consecuencia: el sembrador aprendio del historico la
    regla 'BOLD -> Inversion' con 9 aciertos, y desde ahi TODA compra por Bold
    —que puede ser cualquier cosa— entraba como inversion con 0.88 de
    confianza, o sea sin preguntar.
    """

    def test_la_pasarela_se_quita_tambien_sin_asterisco(self):
        assert texto.normalizar('BOLD CO ONLINE RTFE') == 'ONLINE RTFE'

    def test_y_con_asterisco_como_siempre(self):
        assert texto.normalizar('BOLD*ETRE') == 'ETRE'

    @pytest.mark.parametrize(
        ('crudo', 'limpio'),
        [
            ('MERCADO PAGO*TIERRAG', 'TIERRAG'),
            ('MERCADOPAGO*ZONAFIT', 'ZONAFIT'),
            ('MERCADO PAGO*ZONAFIT', 'ZONAFIT'),
            ('PAYU*CINEMARK', 'CINEMARK'),
            ('DLO*Didi', 'DIDI'),
            ('UBER RIDES*DL', 'UBER RIDES'),
            ('MP*ALGO', 'ALGO'),
            ('WOMPI TIENDA DE LA ESQUINA', 'TIENDA DE LA ESQUINA'),
        ],
    )
    def test_las_dos_formas_de_todas_las_puras(self, crudo, limpio):
        assert texto.normalizar(crudo) == limpio

    def test_mercado_libre_no_es_mercado_pago(self):
        """El orden de la alternancia importa: con las cortas primero, 'MP'
        cazaba dentro de 'MERCADO PAGO' y dejaba 'ERCADO PAGO'."""
        assert texto.normalizar('MERCADO LIBRE') == 'MERCADO LIBRE'

    @pytest.mark.parametrize(
        'crudo',
        [
            'DOMICILIO RAPPI',
            'SACAR NEQUI',
            'PAGO PRUEBA PSE',
            'ABONO TC RAPPI',
        ],
    )
    def test_las_ambiguas_se_conservan_como_palabra(self, crudo):
        """RAPPI, NEQUI y PSE son pasarela CON asterisco, pero por si solas
        significan algo: 'DOMICILIO RAPPI' y 'SACAR NEQUI' los escribio el
        usuario y quieren decir justo eso. Borrarlas dejaba el movimiento sin
        identidad."""
        assert texto.normalizar(crudo) == crudo.replace('  ', ' ')

    def test_si_solo_queda_la_pasarela_no_se_borra(self):
        """Un movimiento que es SOLO 'BOLD' no tiene nada detras. Vaciarlo
        dejaria la clave en blanco y no habria con que preguntar."""
        assert texto.normalizar('BOLD') == 'BOLD'
        assert texto.normalizar('WOMPI') == 'WOMPI'

    @pytest.mark.parametrize(
        ('nombre', 'es'),
        [
            ('BOLD', True),
            ('bold', True),
            ('  BOLD  ', True),
            ('MERCADO PAGO', True),
            ('MERCADOPAGO', True),
            ('WOMPI', True),
            ('BOLD*ETRE', False),
            ('ETRE', False),
            ('RAPPI', False),
            ('NEQUI', False),
            ('MERCADO LIBRE', False),
            ('', False),
            (None, False),
        ],
    )
    def test_es_pasarela_pura(self, nombre, es):
        """Lo que decide si se puede aprender una regla con ese patron. RAPPI da
        False a proposito: los domicilios de Rappi SI son un comercio."""
        assert texto.es_pasarela_pura(nombre) is es


class TestElNumeroDeLocalNoSeComeLasMarcas:
    r"""El patron era `[A-Z]?\d{1,5}` y se comia el «D1» de TIENDA D1 SABANETA.

    D1 es una de las cadenas de supermercado mas grandes del pais: cada compra
    perdia su identidad y quedaba como 'TIENDA SABANETA', que no distingue nada
    porque TIENDA es una palabra vaga. Y «Mercado D1» quedaba en 'MERCADO',
    peor todavia.

    Medido contra los 1129 nombres distintos que hay en Firefly: cambian 8,
    todos de D1, y en los 8 el resultado nuevo es el correcto.
    """

    @pytest.mark.parametrize(
        ('crudo', 'limpio'),
        [
            ('TIENDA D1 SABANETA S', 'TIENDA D1 SABANETA S'),
            ('Mercado D1', 'MERCADO D1'),
            ('Mecato D1', 'MECATO D1'),
            ('Compra en D1', 'COMPRA EN D1'),
            ('Abastecimiento d1', 'ABASTECIMIENTO D1'),
        ],
    )
    def test_la_marca_de_letra_y_un_digito_se_conserva(self, crudo, limpio):
        assert texto.normalizar(crudo) == limpio

    @pytest.mark.parametrize(
        ('crudo', 'limpio'),
        [
            ('CYCLE GEAR N169', 'CYCLE GEAR'),
            ('DROGUERIA ALEMANA 47', 'DROGUERIA ALEMANA'),
            ('EXITO 1234', 'EXITO'),
            ('FARMACIA A25', 'FARMACIA'),
        ],
    )
    def test_el_numero_de_local_si_se_quita(self, crudo, limpio):
        """Numero suelto siempre; letra+numero solo con DOS o mas digitos."""
        assert texto.normalizar(crudo) == limpio

    def test_un_numero_de_cuenta_sigue_intacto(self):
        """En una transferencia a la cuenta '6985' el numero ES la identidad."""
        assert texto.normalizar('6985') == '6985'
