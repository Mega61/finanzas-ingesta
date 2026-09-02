"""Los esquemas que se le manda a Gemini tienen que ser validos SIEMPRE.

Este archivo existe por un bug que costo caro justamente porque no hacia
ruido. `propertyOrdering` listaba los campos de producto aunque no estuvieran
en `properties` —o sea casi siempre, porque casi nunca hay productos sin
clasificar—. La API contestaba:

    404 {"error": {"message": "Requested entity was not found."}}

que no dice absolutamente nada del esquema, y suena a que el modelo no existe.
El bot no se caia: atrapaba la SinIA y se iba al respaldo por expresiones
regulares. Resultado, el camino inteligente estaba MUERTO en produccion y por
fuera solo se veia un bot tonto.

La regla, entonces: `propertyOrdering` y `required` no pueden nombrar nada que
no este en `properties`, y ningun enum puede ir vacio.
"""

from __future__ import annotations

import pytest

from finanzas.adaptadores import ia

CATS = ['Mercado', 'Compras', 'Gato']
PRES = ['Esencial', 'Vivir', 'Antojos']
MOVS = ['1459', '1458']


def _revisar(esquema, donde=''):
    """Las invariantes que la API exige y no perdona."""
    props = esquema['properties']
    for campo in esquema.get('propertyOrdering', []):
        assert campo in props, f'{donde}: «{campo}» esta en el orden y no en properties'
    for campo in esquema.get('required', []):
        assert campo in props, f'{donde}: «{campo}» es obligatorio y no existe'
    for nombre, spec in props.items():
        for e in (spec, spec.get('items') or {}):
            if 'enum' in e:
                assert e['enum'], f'{donde}: el enum de «{nombre}» esta vacio'


class TestElEsquemaDeOrdenes:
    """El del bot conversacional: `entender_orden`."""

    def test_sin_productos_el_esquema_es_valido(self):
        """El caso de todos los dias, y el que estaba roto."""
        _revisar(ia._esquema_orden(MOVS, CATS, PRES), 'sin productos')

    def test_sin_productos_no_menciona_los_campos_de_producto(self):
        e = ia._esquema_orden(MOVS, CATS, PRES)
        assert 'producto_id' not in e['propertyOrdering']
        assert 'producto_id' not in e['properties']

    def test_con_productos_si_los_incluye(self):
        e = ia._esquema_orden(
            MOVS, CATS, PRES, productos=['7'], grupos=['Mercado'], cats_producto=['Fruta']
        )
        assert 'producto_id' in e['properties']
        assert 'producto_id' in e['propertyOrdering']
        _revisar(e, 'con productos')

    @pytest.mark.parametrize(
        ('ids', 'cats', 'pres'),
        [
            ([], CATS, PRES),  # ningun movimiento reciente
            (MOVS, CATS, []),  # Firefly sin presupuestos
            ([], [], []),  # instalacion nueva, todo vacio
        ],
        ids=['sin movimientos', 'sin presupuestos', 'todo vacio'],
    )
    def test_las_listas_vacias_no_producen_un_esquema_invalido(self, ids, cats, pres):
        """Una instalacion nueva no puede dejar el bot sin cerebro."""
        _revisar(ia._esquema_orden(ids, cats, pres), 'listas vacias')

    def test_sin_movimientos_el_arreglo_no_lleva_enum_vacio(self):
        e = ia._esquema_orden([], CATS, PRES)
        assert 'enum' not in e['properties']['movimientos']['items']

    def test_el_comercio_queda_libre(self):
        """Si fuera un enum el usuario no podria nombrar una tienda nueva."""
        assert 'enum' not in ia._esquema_orden(MOVS, CATS, PRES)['properties']['comercio']

    def test_la_lista_de_movimientos_se_recorta(self):
        """El esquema viaja en cada peticion; 500 ids la inflan sin necesidad."""
        e = ia._esquema_orden([str(i) for i in range(500)], CATS, PRES)
        assert len(e['properties']['movimientos']['items']['enum']) <= 60


class TestElEsquemaDeClasificar:
    """El de `interpretar`, que corre con CADA transaccion que entra."""

    @pytest.mark.parametrize(
        ('pres', 'comercios'),
        [
            (PRES, ['Etre']),
            ([], ['Etre']),
            (PRES, []),
            ([], []),
        ],
        ids=['completo', 'sin presupuestos', 'sin comercios', 'pelado'],
    )
    def test_siempre_es_valido(self, pres, comercios):
        _revisar(ia._esquema(CATS, pres, comercios), f'{pres=} {comercios=}')

    def test_lo_ausente_no_aparece_en_el_orden(self):
        e = ia._esquema(CATS, [], [])
        assert e['propertyOrdering'] == ['categoria', 'confianza', 'razon']


class TestGeneradoresVacios:
    """`if algo` sobre un generador vacio da verdadero. Si el codigo se fia de
    eso, la proteccion contra el enum vacio depende de que el llamador use una
    lista, y eso no es una proteccion."""

    def test_orden_con_generadores_vacios(self):
        _revisar(ia._esquema_orden(iter([]), iter([]), iter([])), 'generadores')

    def test_clasificar_con_generadores_vacios(self):
        _revisar(ia._esquema(CATS, iter([]), iter([])), 'generadores')
