"""A que se refiere un mensaje escrito a mano.

Cada caso raro sale de una frase real del chat. El sesgo del ruteo es
deliberado y se verifica: mandar una respuesta al asesor solo deja el
movimiento sin resolver, mientras interpretar una pregunta como respuesta le
mete una categoria inventada a una compra.
"""

from __future__ import annotations

import pytest

from finanzas.dominio import intencion

# Los tres que el bot preguntaba a la vez cuando se reporto el problema.
TRES = [
    {
        'id': 849,
        'valor': -151495.0,
        'contraparte': 'MERCADO PAGO*TIERRAG',
        'categoria': 'Gato',
    },
    {
        'id': 848,
        'valor': -457000.0,
        'contraparte': 'MERCADO PAGO*ZONAFIT',
        'categoria': 'Gimnasio',
    },
    {
        'id': 850,
        'valor': -212000.0,
        'contraparte': 'BOLD CO ONLINE RTFE',
        'categoria': None,
    },
]


class TestAsesorORespuesta:
    @pytest.mark.parametrize(
        'txt',
        [
            '¿me alcanza para una bici?',
            'me alcanza para una bici',
            'deberia comprar un conjunto de ropa deportiva',
            'quiero comprar una bici, deberia?',
            'vale la pena la suscripcion?',
            'que opinas de cambiar de moto',
            'conviene pagar la tarjeta completa',
            'estoy pensando en un mercado grande',
        ],
    )
    def test_deliberar_es_del_asesor(self, txt):
        """«deberia comprar...» va al asesor incluso sin signo de pregunta:
        nadie responde a que categoria va una compra deliberando sobre ella."""
        assert intencion.es_para_el_asesor(txt)

    @pytest.mark.parametrize(
        'txt',
        [
            'cuanto llevo gastado este mes',
            'cuanta plata me queda',
            'como voy con los presupuestos',
            'que me queda en antojos',
            'cual es mi deuda de tarjetas',
            'por que se me fue tanto en mercado',
        ],
    )
    def test_consultar_es_del_asesor(self, txt):
        """Una consulta gana al pasado: «cuanto gaste en mercado» habla en
        pasado y sigue siendo una pregunta."""
        assert intencion.es_para_el_asesor(txt)

    @pytest.mark.parametrize(
        'txt',
        [
            'fue la comida de la gata en tierragro',
            'era Etre, una empresa que vende cosas para la casa',
            'esto fue el gym',
            'compras',
            'mercado del mes',
            'gasolina de la moto',
            'le compre granos a la michina',
            'suplementos de zona fit',
        ],
    )
    def test_describir_una_compra_es_una_respuesta(self, txt):
        assert not intencion.es_para_el_asesor(txt)

    def test_dudar_en_pasado_sigue_siendo_respuesta(self):
        """«esto fue el gym?» es alguien contestando con duda, no preguntando
        por sus finanzas."""
        assert not intencion.es_para_el_asesor('esto fue el gym?')

    @pytest.mark.parametrize('txt', ['', None, '   '])
    def test_vacio_no_es_del_asesor(self, txt):
        assert not intencion.es_para_el_asesor(txt)


class TestMontosMencionados:
    @pytest.mark.parametrize(
        ('txt', 'esperado'),
        [
            ('los 212 mil de bold', {212000.0}),
            ('el de 457.000', {457000.0}),
            ('el de 212000', {212000.0}),
            ('212k', {212000.0}),
            ('el de 1 millon', {1000000.0}),
            ('los 151.495', {151495.0}),
            ('sin numeros', set()),
        ],
    )
    def test_lee_los_montos(self, txt, esperado):
        assert intencion.montos_mencionados(txt) == esperado

    def test_un_numero_grande_ya_viene_en_pesos(self):
        """«212000 mil» no son doscientos doce millones: escribio las dos
        cosas."""
        assert intencion.montos_mencionados('212000 mil') == {212000.0}


class TestAQueMovimiento:
    def test_nombrar_el_comercio_lo_identifica(self):
        cs = intencion.a_que_movimiento('zonafit es el gimnasio', TRES)
        g = intencion.hay_un_ganador(cs)
        assert g and g.id == 848

    def test_el_banco_trunca_y_aun_asi_caza(self):
        """El banco manda 'TIERRAG' por 'TIERRAGRO'. Sin la coincidencia por
        prefijo, decir el nombre completo del comercio no serviria de nada."""
        cs = intencion.a_que_movimiento('fue la comida de la gata en tierragro', TRES)
        g = intencion.hay_un_ganador(cs)
        assert g and g.id == 849

    def test_el_monto_tambien_identifica(self):
        cs = intencion.a_que_movimiento('los 212 mil fueron para la casa', TRES)
        g = intencion.hay_un_ganador(cs)
        assert g and g.id == 850

    def test_la_categoria_que_ya_traia_desempata(self):
        """«esto fue el gym» no nombra ZONAFIT ni su monto, pero ZONAFIT ya
        venia clasificado como Gimnasio y ninguno de los otros lo esta."""
        cs = intencion.a_que_movimiento(
            'esto fue el gym', TRES, categoria_implicada='Gimnasio'
        )
        g = intencion.hay_un_ganador(cs)
        assert g and g.id == 848

    def test_un_texto_que_no_senala_nada_no_tiene_ganador(self):
        """Y esto es lo que NO se puede aflojar: sin senal, no se adivina en
        silencio. El bot lo aplica al ultimo preguntado pero diciendolo."""
        cs = intencion.a_que_movimiento('era Etre, venden cosas para la casa', TRES)
        assert intencion.hay_un_ganador(cs) is None
        assert all(c.puntaje == 0 for c in cs)

    def test_dos_movimientos_del_mismo_comercio_no_se_resuelven_a_la_suerte(self):
        """El bug original: con dos iguales, cualquier eleccion es una moneda al
        aire y la categoria acaba en el equivocado."""
        dos = [
            {'id': 1, 'valor': -30000.0, 'contraparte': 'MERCADO PAGO*TIERRAG'},
            {'id': 2, 'valor': -40000.0, 'contraparte': 'MERCADO PAGO*TIERRAG'},
        ]
        cs = intencion.a_que_movimiento('lo de tierragro', dos)
        assert intencion.hay_un_ganador(cs) is None, 'empate: hay que preguntar'

    def test_el_monto_desempata_dos_del_mismo_comercio(self):
        dos = [
            {'id': 1, 'valor': -30000.0, 'contraparte': 'MERCADO PAGO*TIERRAG'},
            {'id': 2, 'valor': -40000.0, 'contraparte': 'MERCADO PAGO*TIERRAG'},
        ]
        cs = intencion.a_que_movimiento('los 40 mil de tierragro', dos)
        g = intencion.hay_un_ganador(cs)
        assert g and g.id == 2

    def test_las_palabras_vagas_no_cuentan(self):
        """'compra' y 'mercado' aparecen en cualquier cosa. Si contaran,
        'mercado del mes' cazaria con 'MERCADO PAGO*ZONAFIT'."""
        cs = intencion.a_que_movimiento('mercado del mes', TRES)
        assert intencion.hay_un_ganador(cs) is None

    def test_sin_movimientos_no_hay_nada_que_decidir(self):
        assert intencion.a_que_movimiento('lo que sea', []) == []
        assert intencion.hay_un_ganador([]) is None

    def test_la_razon_dice_por_que(self):
        """El bot muestra la razon en el chat. Si no dijera por que lo aplico
        ahi, no habria como notar que se equivoco."""
        cs = intencion.a_que_movimiento('zonafit', TRES)
        g = intencion.hay_un_ganador(cs)
        assert g and 'ZONAFIT' in ' '.join(g.razones)

    def test_ordena_de_mas_probable_a_menos(self):
        cs = intencion.a_que_movimiento('zonafit', TRES)
        assert cs[0].id == 848
        assert cs[0].puntaje > cs[-1].puntaje


class TestSeguirLaConversacion:
    """El caso que rompio la paciencia: preguntó por la ultima transaccion, bien,
    y despues «y la anterior a esa» — y el bot le saco el movimiento de Google
    Workspace con botones de categoria.

    «y la anterior a esa» no lleva verbo ni signo de interrogacion, asi que no
    parecia una pregunta y caia en el camino de las respuestas. Y peor: la
    palabra «esa» cuenta como pasado y anulaba la regla del signo de
    interrogacion, asi que ni escribiendo «?» se salvaba.
    """

    @pytest.mark.parametrize(
        'txt',
        [
            'y la anterior a esa',
            'y la anterior?',
            'y la anterior',
            'la anterior',
            'y antes de esa',
            'y antes de esa?',
            'la penultima',
            'y la otra',
            'y la de antes?',
            'cual mas',
            'algo mas',
            'explicame mas',
            'dame mas detalles',
            'ok y la otra',
            'y esa?',
            'y eso?',
        ],
    )
    def test_esto_continua_la_conversacion(self, txt):
        assert intencion.es_seguimiento(txt)

    @pytest.mark.parametrize(
        'txt',
        [
            'fue la comida de la gata en tierragro',
            'era Etre, una empresa que vende cosas para la casa',
            'esto fue el gym',
            'mercado del mes',
            'gasolina de la moto',
            'suplementos de zona fit',
        ],
    )
    def test_describir_una_compra_no_es_seguimiento(self, txt):
        assert not intencion.es_seguimiento(txt)

    def test_empezar_por_y_no_basta(self):
        """«y fue en tierragro» y «ya te dije, es mercado» empiezan como un
        seguimiento y son respuestas: nombran algo. Se exige que el mensaje sea
        corto Y sin contenido propio."""
        assert not intencion.es_seguimiento('y fue en tierragro')
        assert not intencion.es_seguimiento('ya te dije, es mercado')

    def test_un_mensaje_largo_que_empieza_por_y_describe(self):
        assert not intencion.es_seguimiento(
            'y ese fue el mercado del mes que hicimos en el exito'
        )

    @pytest.mark.parametrize('txt', ['', None, '   '])
    def test_vacio_no_es_seguimiento(self, txt):
        assert not intencion.es_seguimiento(txt)

    def test_ninguna_forma_de_seguimiento_se_toma_como_edicion(self):
        """Si «y la anterior» se leyera como una orden de cambio, preguntar por
        un movimiento acabaria modificando otro."""
        for txt in (
            'y la anterior a esa',
            'la penultima',
            'y la otra',
            'cual mas',
            'explicame mas',
        ):
            assert not intencion.es_edicion(txt).pide_cambio, txt


class TestEtiquetasYLote:
    """«las ultimas 2 estan en compras, agregales la etiqueta Ropa».

    No se podia. Las etiquetas no eran editables —`CAMBIABLES` no las incluia—,
    los verbos de etiquetar no se reconocian, y todo el camino de edicion
    resolvia UN objetivo, asi que una orden en plural acababa aplicada a uno o
    a ninguno.
    """

    def test_la_frase_completa_se_entiende(self):
        e = intencion.es_edicion(
            'las ultimas 2 estan en compras, agregales la etiqueta Ropa'
        )
        assert e.pide_cambio
        assert e.etiqueta_agregar == 'Ropa'
        assert e.cuantas == 2
        assert e.categoria_filtro.lower() == 'compras'
        assert not e.borrar, 'agregar no es borrar'

    @pytest.mark.parametrize(
        ('txt', 'etq'),
        [
            ('ponle el tag Ropa', 'Ropa'),
            ('agrega la etiqueta Viaje', 'Viaje'),
            ('etiquetalas como Ropa', 'Ropa'),
            ('marcalas como reembolsable', 'reembolsable'),
            ('ponles la etiqueta Comida Gata', 'Comida Gata'),
        ],
    )
    def test_saca_el_nombre_de_la_etiqueta(self, txt, etq):
        assert intencion.es_edicion(txt).etiqueta_agregar == etq

    def test_quitar_una_etiqueta_es_lo_contrario(self):
        e = intencion.es_edicion('quita la etiqueta Ropa')
        assert e.etiqueta_quitar == 'Ropa'
        assert e.etiqueta_agregar is None
        assert not e.borrar, 'quitar una etiqueta no borra el movimiento'

    def test_el_nombre_no_se_traga_lo_que_sigue(self):
        """«la etiqueta Ropa a las ultimas dos»: el nombre se corta en «a las»."""
        assert (
            intencion.es_edicion(
                'agrega la etiqueta Ropa a las ultimas dos'
            ).etiqueta_agregar
            == 'Ropa'
        )

    @pytest.mark.parametrize(
        ('txt', 'n'),
        [
            ('las ultimas 2', 2),
            ('las 2 ultimas', 2),
            ('las ultimas dos', 2),
            ('a las 3 ultimas', 3),
            ('las ultimas cinco', 5),
            ('la ultima', None),
            ('cambiala', None),
        ],
    )
    def test_cuantas_pide(self, txt, n):
        assert intencion.cuantas_pide(txt) == n

    def test_un_numero_absurdo_no_cuenta(self):
        """«las ultimas 500» no es una orden razonable; se trata como una sola
        para no editar media contabilidad por un dedazo."""
        assert intencion.cuantas_pide('las ultimas 500') is None

    def test_borrar_sigue_siendo_borrar(self):
        e = intencion.es_edicion('borra la ultima')
        assert e.borrar and e.etiqueta_agregar is None


class TestLasEtiquetasLasPoneElUsuario:
    """El modelo agregaba etiquetas que nadie pidio: a «es lo de google, eso es
    del trabajo» le ponia `reembolsable`. Una etiqueta es justo el dato que
    solo aporta el usuario, asi que si no la escribio, no va."""

    def test_una_etiqueta_que_no_esta_en_el_mensaje_se_cae(self):
        assert intencion.etiquetas_respaldadas(
            'es lo de google, eso es del trabajo', ['reembolsable', 'trabajo']
        ) == ['trabajo']

    def test_la_que_si_escribio_pasa(self):
        assert intencion.etiquetas_respaldadas(
            'las ultimas 2 estan en compras y quiero agregar el tag Ropa', ['Ropa']
        ) == ['Ropa']

    def test_el_plural_y_las_tildes_son_la_misma_intencion(self):
        assert intencion.etiquetas_respaldadas('etiquetalo como cenas', ['Cena']) == [
            'Cena'
        ]
        assert intencion.etiquetas_respaldadas('ponle almuerzo', ['Almuerzo']) == [
            'Almuerzo'
        ]

    def test_no_respalda_por_dos_letras(self):
        """Con menos de cuatro, «uber» respaldaria cualquier «ub»."""
        assert intencion.etiquetas_respaldadas('ponle uber', ['Ubicacion']) == []

    def test_sin_texto_no_filtra(self):
        """El respaldo sin IA no pasa texto, y ahi las etiquetas ya vienen de
        una regla que las leyo del mensaje."""
        assert intencion.etiquetas_respaldadas('', ['Ropa']) == []
