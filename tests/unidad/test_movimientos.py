"""Leer y editar movimientos ya registrados en Firefly.

Este modulo faltaba y se notaba en dos sintomas concretos: preguntarle al bot
«cual fue la ultima transaccion» era imposible —el asesor solo recibia
agregados, ni un movimiento individual— y corregir algo ya cerrado obligaba a
entrar a Firefly a mano.
"""

from __future__ import annotations

import pytest

from finanzas.aplicacion import movimientos, presupuestos


def _tx(tx_id, fecha, monto, tipo='withdrawal', **extra):
    """Una transaccion con la forma que devuelve la API de Firefly."""
    split = {
        'date': f'{fecha}T00:00:00+02:00',
        'amount': f'{abs(monto)}.000000000000',
        'type': tipo,
        'description': extra.get('descripcion', 'ALGO'),
        'category_name': extra.get('categoria'),
        'budget_name': extra.get('presupuesto'),
        'source_name': extra.get('origen', 'VISA BLACK'),
        'destination_name': extra.get('destino'),
        'tags': extra.get('etiquetas', []),
        'currency_code': 'COP',
        'notes': '',
    }
    return {'id': str(tx_id), 'attributes': {'transactions': [split]}}


@pytest.fixture
def firefly_falso(monkeypatch):
    """Firefly de mentiras. Anota los PUT para verificar que se manda bien."""
    estado = {
        'transacciones': [
            _tx(
                1441,
                '2026-09-01',
                151495,
                destino='Tierragro',
                categoria='Gato',
                etiquetas=['sin-confirmar'],
            ),
            _tx(
                1455,
                '2026-09-01',
                212000,
                destino='Etre',
                categoria='Compras Casa',
                etiquetas=['sin-confirmar'],
            ),
            _tx(
                1456,
                '2026-09-01',
                21040,
                destino='Municipio Sabaneta',
                categoria='Mercado',
                etiquetas=['sin-confirmar'],
            ),
            _tx(1400, '2026-08-20', 30000, destino='Tierragro', categoria='Gato'),
            _tx(
                1443,
                '2026-09-01',
                2588065,
                tipo='deposit',
                destino='MASTERCARD BLACK',
                categoria='Abono',
            ),
        ],
        'puts': [],
        'borrados': [],
    }

    def get_all(ruta):
        return estado['transacciones']

    def call(metodo, ruta, payload=None):
        if metodo == 'GET' and '/transactions/' in ruta:
            tid = ruta.rsplit('/', 1)[-1]
            for t in estado['transacciones']:
                if t['id'] == tid:
                    return {'data': t}
            raise movimientos.firefly.ApiError(404, 'no existe')
        if metodo == 'PUT':
            tid = ruta.rsplit('/', 1)[-1]
            estado['puts'].append((tid, payload))
            # se aplica sobre el estado, para que la relectura lo vea
            for t in estado['transacciones']:
                if t['id'] == tid:
                    t['attributes']['transactions'][0].update(
                        {
                            k: v
                            for k, v in payload['transactions'][0].items()
                            if k != 'transaction_journal_id'
                        }
                    )
            return {}
        if metodo == 'DELETE':
            estado['borrados'].append(ruta.rsplit('/', 1)[-1])
            return {}
        return {}

    monkeypatch.setattr(movimientos.firefly, 'get_all', get_all)
    monkeypatch.setattr(movimientos.firefly, 'call', call)
    return estado


class TestUltimos:
    def test_el_mas_nuevo_primero(self, firefly_falso):
        movs = movimientos.ultimos()
        assert movs[0]['fecha'] == '2026-09-01'
        assert movs[-1]['fecha'] == '2026-08-20'

    def test_dos_del_mismo_dia_se_desempatan_por_id(self, firefly_falso):
        """«la ultima» de un dia con varias compras es la que entro despues, y
        Firefly no garantiza el orden de la paginacion."""
        mismos = [m for m in movimientos.ultimos() if m['fecha'] == '2026-09-01']
        ids = [int(m['id']) for m in mismos]
        assert ids == sorted(ids, reverse=True)

    def test_un_gasto_es_negativo_y_un_ingreso_positivo(self, firefly_falso):
        """Firefly manda el monto SIN signo; el signo lo da el tipo. Sin esto un
        abono de 2,5 millones se leia como un gasto."""
        por_id = {m['id']: m for m in movimientos.ultimos()}
        assert por_id['1441']['valor'] < 0, 'withdrawal es negativo'
        assert por_id['1443']['valor'] > 0, 'deposit es positivo'

    def test_el_limite_se_respeta(self, firefly_falso):
        assert len(movimientos.ultimos(limite=2)) == 2


class TestBuscar:
    def test_encuentra_por_comercio(self, firefly_falso):
        r = movimientos.buscar('etre')
        assert [m['id'] for m in r] == ['1455']

    def test_encuentra_las_dos_del_mismo_comercio(self, firefly_falso):
        r = movimientos.buscar('tierragro')
        assert {m['id'] for m in r} == {'1441', '1400'}

    def test_encuentra_por_categoria(self, firefly_falso):
        r = movimientos.buscar(categoria='Mercado')
        assert [m['id'] for m in r] == ['1456']

    def test_sin_coincidencias_devuelve_vacio(self, firefly_falso):
        assert movimientos.buscar('zapateria inexistente') == []

    def test_una_consulta_vacia_devuelve_los_ultimos(self, firefly_falso):
        assert len(movimientos.buscar(None, limite=3)) == 3


class TestEditar:
    def test_cambia_la_categoria(self, firefly_falso):
        m = movimientos.editar('1456', categoria='Mercado del mes')
        assert m['categoria'] == 'Mercado del mes'
        tid, payload = firefly_falso['puts'][-1]
        assert tid == '1456'
        assert payload['transactions'][0]['category_name'] == 'Mercado del mes'

    def test_el_comercio_de_un_gasto_va_al_DESTINO(self, firefly_falso):
        """En un gasto el comercio es la cuenta de destino. Mandarlo al lado
        equivocado mueve la plata de cuenta."""
        movimientos.editar('1455', comercio='Etre Hogar')
        _, payload = firefly_falso['puts'][-1]
        assert payload['transactions'][0]['destination_name'] == 'Etre Hogar'
        assert 'source_name' not in payload['transactions'][0]

    def test_el_comercio_de_un_ingreso_va_al_ORIGEN(self, firefly_falso):
        movimientos.editar('1443', comercio='Otra cuenta')
        _, payload = firefly_falso['puts'][-1]
        assert payload['transactions'][0]['source_name'] == 'Otra cuenta'
        assert 'destination_name' not in payload['transactions'][0]

    def test_varios_cambios_de_una(self, firefly_falso):
        movimientos.editar(
            '1456',
            categoria='Mercado',
            presupuesto='Esencial',
            descripcion='MERCADO DEL MES',
        )
        _, payload = firefly_falso['puts'][-1]
        s = payload['transactions'][0]
        assert s['category_name'] == 'Mercado'
        assert s['budget_name'] == 'Esencial'
        assert s['description'] == 'MERCADO DEL MES'

    def test_un_campo_inventado_falla_en_vez_de_ignorarse(self, firefly_falso):
        """Igual que en el almacen: descartar en silencio un nombre mal escrito
        hace que el cambio se vea aplicado y no haya pasado nada."""
        with pytest.raises(ValueError, match='categoia'):
            movimientos.editar('1456', categoia='Mercado')
        assert firefly_falso['puts'] == []

    def test_un_movimiento_que_no_existe_falla_claro(self, firefly_falso):
        with pytest.raises(ValueError, match='9999'):
            movimientos.editar('9999', categoria='Mercado')

    def test_no_toca_los_de_varias_partes(self, firefly_falso):
        """Un split de varias partes no se puede editar con un solo
        transaction_journal_id: se avisa en vez de corromperlo."""
        firefly_falso['transacciones'][0]['attributes']['transactions'].append(
            dict(firefly_falso['transacciones'][0]['attributes']['transactions'][0])
        )
        with pytest.raises(ValueError, match='partes'):
            movimientos.editar('1441', categoria='Otra')


class TestBorrarYConfirmar:
    def test_borra(self, firefly_falso):
        assert movimientos.borrar('1456') is True
        assert firefly_falso['borrados'] == ['1456']

    def test_confirmar_quita_la_etiqueta(self, firefly_falso, monkeypatch):
        visto = {}
        monkeypatch.setattr(
            movimientos.firefly,
            'quitar_etiqueta',
            lambda tx, etq: visto.update(tx=tx, etq=etq) or True,
        )
        movimientos.confirmar('1441')
        assert visto == {'tx': '1441', 'etq': 'sin-confirmar'}


class TestDescribir:
    def test_una_linea_legible(self, firefly_falso):
        m = movimientos.uno('1455')
        s = movimientos.describir(m)
        assert '2026-09-01' in s
        assert '212.000' in s
        assert 'Etre' in s
        assert '[Compras Casa]' in s

    def test_marca_los_sin_confirmar(self, firefly_falso):
        assert 'sin confirmar' in movimientos.describir(movimientos.uno('1455'))
        assert 'sin confirmar' not in movimientos.describir(movimientos.uno('1400'))

    def test_con_id_para_el_modelo(self, firefly_falso):
        """El asesor necesita el id para que el usuario pueda decir «la de
        212 mil» y el bot sepa cual es."""
        assert '#1455' in movimientos.describir(movimientos.uno('1455'), con_id=True)

    def test_el_bloque_para_el_asesor_los_lista(self, firefly_falso):
        txt = movimientos.en_texto(movimientos.ultimos(limite=3))
        assert txt.count('#') == 3

    def test_sin_movimientos_lo_dice(self):
        assert 'ninguno' in movimientos.en_texto([])


class TestPresupuestoSeguro:
    """De donde sale el presupuesto de un gasto que se clasifica solo.

    El reclamo: «porque la transaccion de D1 no se ingreso en el budget de
    esencial? me toco agregarlo a mano».

    El clasificador sacaba el presupuesto de dos sitios —una lista escrita a
    mano en taxonomia, con UNA entrada, y lo que trajera la regla aprendida— y
    nunca consultaba el mapa categoria->presupuesto del historico. Un gasto con
    categoria Mercado, que en el historico apunta a Esencial 49 de 49 veces,
    entraba a Firefly SIN presupuesto.
    """

    @pytest.fixture
    def mapa(self):
        return {
            'Mercado': {
                'presupuesto': 'Esencial',
                'seguro': True,
                'reparto': {'Esencial': 49},
            },
            'Restaurante': {
                'presupuesto': 'Vivir',
                'seguro': False,
                'reparto': {'Vivir': 10, 'Antojos': 3},
            },
        }

    def test_una_categoria_que_decide_sola_da_su_presupuesto(self, mapa):
        assert presupuestos.presupuesto_seguro('Mercado', mapa) == 'Esencial'

    def test_una_repartida_no_se_adivina(self, mapa):
        """'Restaurante' entre Vivir y Antojos es un juicio de verdad: se
        pregunta, no se inventa."""
        assert presupuestos.presupuesto_seguro('Restaurante', mapa) is None

    def test_una_categoria_sin_historico_da_none(self, mapa):
        assert presupuestos.presupuesto_seguro('GBS Infra', mapa) is None

    @pytest.mark.parametrize('cat', [None, '', 'X'])
    def test_sin_categoria_no_hay_presupuesto(self, mapa, cat):
        assert presupuestos.presupuesto_seguro(cat, mapa) is None
