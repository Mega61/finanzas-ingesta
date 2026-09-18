"""Solo el supermercado es mercado.

Al buzon de facturacion electronica llega toda factura con NIT, no solo la del
super. Antes todas entraban al mismo clasificador por palabras y el resultado
no se sostenia:

    TAC5PA SAS  "PLAN DE SALUD Y BIENESTAR MES TRAINING PRE VENTA 1.0"
                -> Carnes y pollo, Consumible, 70.000 al mercado de septiembre

Era la mensualidad del gimnasio. Ya estaba bien puesta en Firefly como
«Membresia Gimnasio» contra la cuenta Athletic; lo que sobraba era su copia en
la canasta.

Y las once lineas de la factura de EPM (energia, gas, alumbrado, aseo) se
quedaban en 'Sin clasificar', que es justo lo que el bot pregunta. Como
'saltar' solo aplaza tres dias, volvian para siempre.
"""

from __future__ import annotations

import pytest

from finanzas.adaptadores import almacen, db
from finanzas.aplicacion import catalogo as cat

EXITO = '890900608'
D1 = '900276962'
SUPERVAQUITA = '900522508'


@pytest.mark.parametrize(
    'nit,desc,iva',
    [
        ('890904996', 'ENERGÍA MDO REGULADO', 0.0),
        ('890904996', 'GAS NATURAL REGULADO', 0.0),
        ('890904996', 'ALUMBRADO PÚBLICO SABANETA', 0.0),
        ('901867500', 'PLAN DE SALUD Y BIENESTAR MES TRAINING PRE VENTA 1.0', 0.0),
        ('830129327', 'BUSCAPINA COMPOSITUM NF CAJA X10', 19.0),
        ('890912426', 'NUPEC FELINO INDOOR X 3KG', 19.0),
    ],
)
def test_lo_que_no_es_super_no_entra_al_mercado(nit, desc, iva):
    tipo, grupo, _cat, origen = cat.clasificar(nit, '1', desc, iva)
    assert grupo == cat.NO_ES_MERCADO
    assert origen == 'no_mercado'
    # Las dos consecuencias que importan, y por las dos razones distintas:
    assert tipo != 'Consumible', 'seguiria sumando en v_canasta'
    assert grupo != 'Sin clasificar', 'el bot seguiria preguntando por el'


@pytest.mark.parametrize('nit', [EXITO, D1, SUPERVAQUITA])
def test_el_super_sigue_clasificandose_como_siempre(nit):
    tipo, grupo, _c, origen = cat.clasificar(nit, '1', 'LECHE ENTERA X 1000ML', 0.0)
    assert grupo != cat.NO_ES_MERCADO
    assert origen != 'no_mercado'
    assert tipo in ('Consumible', 'No consumible')


def test_el_gimnasio_no_se_cuela_por_una_palabra():
    """El caso exacto: 'PRE VENTA' cazaba una regla de carnes."""
    _t, grupo, categoria, _o = cat.clasificar(
        '901867500', '1', 'PLAN DE SALUD Y BIENESTAR MES TRAINING PRE VENTA 1.0', 0.0
    )
    assert (grupo, categoria) != ('Alimentacion', 'Carnes y pollo')
    assert grupo == cat.NO_ES_MERCADO


def test_tipo_de_no_asciende_a_consumible():
    """tipo_de() la usa el bot cuando respondes a mano: no puede revivirlo."""
    assert cat.tipo_de(cat.NO_ES_MERCADO) == cat.NO_ES_MERCADO


def test_los_tres_nits_son_los_del_dashboard():
    """Si alguien agrega una cadena, que sea aqui y no en cuatro sitios."""
    assert {EXITO, D1, SUPERVAQUITA} == cat.NITS_MERCADO


def test_no_mercado_le_gana_a_una_respuesta_tuya(tmp_path):
    """Contestar «Mascotas / Gato» dice QUE es, no que sea un supermercado.

    Los tres productos de la farmacia y la veterinaria que alcanzaste a
    contestar por Telegram se quedaban en la canasta para siempre, porque
    `origen = 'usuario'` le gana a todo. Son 175.245 que ninguna
    reclasificacion sacaba. 'no_mercado' es la unica excepcion: no opina del
    producto, opina del NIT.
    """
    a = almacen.Almacen.abrir(str(tmp_path / 'x.db'), db.ESQUEMA)
    vet = '890912426'

    # lo contestaste por Telegram
    a.catalogo_upsert(
        vet, 'C1', 'NUPEC FELINO', 'Consumible', 'Mascotas', 'Gato', 'usuario'
    )
    assert a.catalogo_ver(vet, 'C1')['grupo'] == 'Mascotas'

    # una regla automatica NO puede pisarlo
    a.catalogo_upsert(
        vet, 'C1', 'NUPEC FELINO', 'Consumible', 'Alimentacion', 'Abarrotes', 'palabra'
    )
    assert a.catalogo_ver(vet, 'C1')['grupo'] == 'Mascotas'

    # 'no_mercado' si, porque habla del NIT y no del producto
    a.catalogo_upsert(
        vet,
        'C1',
        'NUPEC FELINO',
        cat.NO_ES_MERCADO,
        cat.NO_ES_MERCADO,
        cat.NO_ES_MERCADO,
        'no_mercado',
    )
    fila = a.catalogo_ver(vet, 'C1')
    assert fila['grupo'] == cat.NO_ES_MERCADO
    assert fila['tipo'] != 'Consumible'
