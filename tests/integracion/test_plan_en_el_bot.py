"""El plan de caja vive en el bot y se cambia hablando.

Antes era una variable PLAN_JSON en Portainer: cambiar la fecha de pago de la
Rappi era editar un JSON de una linea, pegarlo y redesplegar. Ahora cada
version queda en la base, la primera se importa sola, un cambio rige solo con
un toque, y la meta y lo de cada mes se ven en Firefly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest

from finanzas.adaptadores import db, firefly, ia
from finanzas.adaptadores.almacen import Almacen
from finanzas.aplicacion import plan, plan_edicion, plan_firefly

BASE = {
    'cuenta': 'Bancolombia',
    'reserva': 'Nu',
    'colchon': 500000,
    'horizonte_dias': 60,
    'meta': {'nombre': 'Fondo de emergencia', 'cuenta': 'Nu', 'objetivo': 25000000},
    'ingreso': [{'concepto': 'Quincena 1', 'dia': 15, 'monto': 4211025}],
    'compromiso': [
        {
            'concepto': 'Arriendo nuevo',
            'dia': 23,
            'desde': '2026-11-01',
            'monto': 2750000,
        },
        {'concepto': 'Contadora', 'fecha': '2026-10-24', 'monto': 200000},
    ],
    'tarjeta': [
        {'nombre': 'MASTERCARD BLACK', 'corte': 30, 'pago': 19, 'gasto_diario': 129000}
    ],
}


@pytest.fixture
def alm(monkeypatch):
    cx = sqlite3.connect(':memory:')
    cx.row_factory = sqlite3.Row
    a = Almacen(cx)
    a.inicializar(db.ESQUEMA, db.MIGRACIONES)
    uid = a.guardar_usuario('Juan', 'https://ff', '', '555')
    monkeypatch.setenv('PLAN_JSON', json.dumps(BASE))
    return a, uid


def _gemini(monkeypatch, respuesta):
    monkeypatch.setattr(ia, 'disponible', lambda: True)
    llamadas = []

    def falso(texto, plan_actual, cuentas, hoy):
        llamadas.append((texto, json.loads(plan_actual)))
        return respuesta

    monkeypatch.setattr(ia, 'entender_cambio_de_plan', falso)
    return llamadas


class TestElPlanVigente:
    def test_la_primera_vez_importa_el_de_portainer(self, alm, monkeypatch):
        a, uid = alm
        p, _datos = plan.vigente(a, uid)
        assert p.meta == plan.Meta('Fondo de emergencia', 'Nu', 25000000)
        assert a.plan_vigente(uid)['motivo'] == 'importado'
        # Ya importado, la variable no se vuelve a leer.
        monkeypatch.setenv('PLAN_JSON', json.dumps({**BASE, 'colchon': 1}))
        assert plan.vigente(a, uid)[0].colchon == 500000

    def test_sin_plan_en_ningun_lado_no_hay(self, alm, monkeypatch):
        a, uid = alm
        monkeypatch.delenv('PLAN_JSON')
        monkeypatch.setattr(plan, '_texto_crudo', lambda: None)
        assert plan.vigente(a, uid) is None

    def test_una_meta_sin_objetivo_no_pasa(self):
        with pytest.raises(plan.PlanInvalido):
            plan.interpretar({**BASE, 'meta': {'cuenta': 'Nu'}})


class TestCambiarloHablando:
    def test_propone_y_no_rige_hasta_el_toque(self, alm, monkeypatch):
        a, uid = alm
        plan.vigente(a, uid)
        nuevo = {
            **BASE,
            'tarjeta': [
                *BASE['tarjeta'],
                {
                    'nombre': 'RAPPICARD BLACK',
                    'corte': 29,
                    'pago': 10,
                    'gasto_diario': 6700,
                },
            ],
        }
        _gemini(
            monkeypatch,
            {
                'accion': 'cambiar',
                'plan': json.dumps(nuevo),
                'resumen': 'agregue la Rappi',
            },
        )
        prop = plan_edicion.proponer(
            a,
            uid,
            'la rappi paga el 10 y corta el 29',
            ['RAPPICARD BLACK'],
            date(2026, 10, 24),
        )
        assert prop.accion == 'cambiar'
        assert prop.cambios == [
            '+ tarjeta RAPPICARD BLACK: corte 29, pago 10, gasto_diario $6.700'
        ]
        assert len(plan.vigente(a, uid)[0].tarjetas) == 1, 'sin toque no rige'

        p = plan_edicion.confirmar(a, uid, prop.plan_id)
        assert [t.nombre for t in p.tarjetas] == ['MASTERCARD BLACK', 'RAPPICARD BLACK']
        assert len(plan.vigente(a, uid)[0].tarjetas) == 2
        assert plan_edicion.confirmar(a, uid, prop.plan_id) is None, 'una sola vez'

    def test_la_propuesta_de_otro_no_se_confirma(self, alm, monkeypatch):
        a, uid = alm
        plan.vigente(a, uid)
        otro = a.guardar_usuario('Mariana', 'https://ff', '', '777')
        _gemini(
            monkeypatch,
            {
                'accion': 'cambiar',
                'plan': json.dumps({**BASE, 'colchon': 1}),
                'resumen': '',
            },
        )
        prop = plan_edicion.proponer(
            a, uid, 'colchon de 1 peso', [], date(2026, 10, 24)
        )
        assert plan_edicion.confirmar(a, otro, prop.plan_id) is None
        assert plan.vigente(a, uid)[0].colchon == 500000

    def test_un_plan_roto_no_se_propone(self, alm, monkeypatch):
        """El modelo dijo un medio que no es ni la cuenta ni una tarjeta."""
        a, uid = alm
        plan.vigente(a, uid)
        roto = {
            **BASE,
            'compromiso': [
                {'concepto': 'X', 'dia': 5, 'monto': 1, 'medio': 'Davivienda'}
            ],
        }
        _gemini(
            monkeypatch, {'accion': 'cambiar', 'plan': json.dumps(roto), 'resumen': ''}
        )
        prop = plan_edicion.proponer(a, uid, 'algo', [], date(2026, 10, 24))
        assert prop.accion == 'error' and 'no cuadra' in prop.resumen

    def test_si_falta_un_dato_pregunta(self, alm, monkeypatch):
        a, uid = alm
        plan.vigente(a, uid)
        _gemini(
            monkeypatch,
            {'accion': 'preguntar', 'pregunta': '¿Qué día paga?', 'resumen': ''},
        )
        prop = plan_edicion.proponer(a, uid, 'agrega la rappi', [], date(2026, 10, 24))
        assert (prop.accion, prop.pregunta) == ('preguntar', '¿Qué día paga?')


def test_las_diferencias_se_leen():
    nuevo = json.loads(json.dumps(BASE))
    nuevo['compromiso'][0]['monto'] = 2800000
    del nuevo['compromiso'][1]
    nuevo['colchon'] = 600000
    assert plan_edicion.diferencias(BASE, nuevo) == [
        '~ colchon: $500.000 → $600.000',
        f'{plan_edicion.MENOS} compromiso Contadora',
        '~ compromiso Arriendo nuevo: monto $2.750.000 → $2.800.000',
    ]


# --------------------------------------------------------------- en Firefly


class FireflyFalso:
    def __init__(self):
        self.alcancia = None
        self.facturas = {
            '1': {
                'id': '1',
                'attributes': {
                    'name': 'Factura EPM',
                    'notes': None,
                    'active': True,
                    'amount_min': '250000',
                    'date': '2026-10-01',
                },
            }
        }

    def accounts_index(self):
        return {'Nu': {'id': '24'}, 'Bancolombia': {'id': '1'}}

    def call(self, metodo, ruta, *a, **k):
        assert ruta == '/api/v1/accounts/24'
        return {'data': {'attributes': {'current_balance': '22091619.00'}}}

    def alcancias(self):
        return [self.alcancia] if self.alcancia else []

    def guardar_alcancia(
        self, nombre, objetivo, cuenta_id, actual, alcancia_id=None, notas=None
    ):
        self.alcancia = {
            'id': alcancia_id or '9',
            'attributes': {'name': nombre},
            'obj': objetivo,
            'actual': actual,
            'cuenta': cuenta_id,
        }
        return self.alcancia['id']

    def facturas_fijas(self):
        return list(self.facturas.values())

    def guardar_factura_fija(
        self, nombre, monto, desde, activa=True, factura_id=None, notas=None
    ):
        fid = factura_id or str(len(self.facturas) + 1)
        previa = self.facturas.get(fid, {}).get('attributes', {})
        self.facturas[fid] = {
            'id': fid,
            'attributes': {
                'name': nombre,
                'notes': notas if notas is not None else previa.get('notes'),
                'active': activa,
                'amount_min': str(monto),
                'date': str(desde),
            },
        }
        return fid


def test_la_meta_y_lo_de_cada_mes_quedan_en_firefly(monkeypatch):
    ff = FireflyFalso()
    for n in (
        'accounts_index',
        'call',
        'alcancias',
        'guardar_alcancia',
        'facturas_fijas',
        'guardar_factura_fija',
    ):
        monkeypatch.setattr(plan_firefly.firefly, n, getattr(ff, n))
    p = plan.interpretar(BASE)

    h = plan_firefly.sincronizar(p, hoy=date(2026, 10, 24))
    assert (ff.alcancia['obj'], ff.alcancia['actual'], ff.alcancia['cuenta']) == (
        25000000,
        22091619.0,
        '24',
    )
    assert h['facturas'] == ['Arriendo nuevo'], (
        'la contadora es de una vez: no es factura fija'
    )
    arriendo = next(
        f for f in ff.facturas.values() if f['attributes']['name'] == 'Arriendo nuevo'
    )
    assert arriendo['attributes']['date'] == '2026-11-23'

    # Sale del plan: se desactiva la del bot, y la de EPM no se toca.
    sin_arriendo = plan.interpretar({**BASE, 'compromiso': [BASE['compromiso'][1]]})
    h = plan_firefly.sincronizar(sin_arriendo, hoy=date(2026, 10, 24))
    assert h['desactivadas'] == ['Arriendo nuevo']
    assert ff.facturas['1']['attributes']['active'] is True
    assert ff.alcancia['id'] == '9', 'la misma alcancia, no otra'


def _partido():
    """Lo que devolvio Gemini de verdad con «el arriendo nuevo es 2,8
    millones desde diciembre»."""
    nuevo = json.loads(json.dumps(BASE))
    nuevo['compromiso'][0] = {
        'concepto': 'Arriendo nuevo',
        'dia': 23,
        'desde': '2026-11-01',
        'hasta': '2026-11-30',
        'monto': 2750000,
    }
    nuevo['compromiso'].append(
        {
            'concepto': 'Arriendo nuevo',
            'dia': 23,
            'desde': '2026-12-01',
            'monto': 2800000,
        }
    )
    return nuevo


def test_un_concepto_partido_en_el_tiempo_se_ve_completo():
    """Por nombre solo, noviembre desaparecia del diff sin que se viera."""
    assert plan_edicion.diferencias(BASE, _partido()) == [
        '+ compromiso Arriendo nuevo: dia 23, desde 2026-12-01, monto $2.800.000',
        '~ compromiso Arriendo nuevo (desde 2026-11-01): hasta None → 2026-11-30',
    ]


def test_cambiar_solo_el_desde_es_un_cambio():
    nuevo = json.loads(json.dumps(BASE))
    nuevo['compromiso'][0]['desde'] = '2026-12-01'
    assert plan_edicion.diferencias(BASE, nuevo) == [
        '~ compromiso Arriendo nuevo: desde 2026-11-01 → 2026-12-01'
    ]


def test_la_factura_fija_es_la_del_tramo_que_toca_primero(monkeypatch):
    ff = FireflyFalso()
    for n in (
        'accounts_index',
        'call',
        'alcancias',
        'guardar_alcancia',
        'facturas_fijas',
        'guardar_factura_fija',
    ):
        monkeypatch.setattr(plan_firefly.firefly, n, getattr(ff, n))
    plan_firefly.sincronizar(plan.interpretar(_partido()), hoy=date(2026, 10, 24))
    (arriendo,) = [
        f for f in ff.facturas.values() if f['attributes']['name'] == 'Arriendo nuevo'
    ]
    assert (arriendo['attributes']['amount_min'], arriendo['attributes']['date']) == (
        '2750000.0',
        '2026-11-23',
    )
    plan_firefly.sincronizar(plan.interpretar(_partido()), hoy=date(2026, 12, 2))
    (arriendo,) = [
        f for f in ff.facturas.values() if f['attributes']['name'] == 'Arriendo nuevo'
    ]
    assert (arriendo['attributes']['amount_min'], arriendo['attributes']['date']) == (
        '2800000.0',
        '2026-12-23',
    )


def test_firefly_recibe_la_moneda(monkeypatch):
    """Firefly 6.6 respondio 422 al crear la alcancia sin moneda: la prueba
    con el Firefly falso no lo vio, porque el falso no la exigia."""
    enviados = []

    def call(metodo, ruta, cuerpo=None, conexion=None):
        enviados.append((metodo, ruta, cuerpo))
        return {'data': {'id': '1'}}

    monkeypatch.setattr(firefly, 'call', call)
    firefly.guardar_alcancia('Fondo de emergencia', 25000000, '24', 22091619)
    firefly.guardar_factura_fija('Arriendo nuevo', 2750000, date(2026, 11, 23))
    assert enviados[0][2]['transaction_currency_code'] == 'COP'
    assert enviados[1][2]['currency_code'] == 'COP'
