"""Quien pudo ser el comercio de un cargo que no se entiende, mirando el correo
que llego alrededor. Logica pura, sin I/O.

El caso que lo origino: «Compraste COP78.000,00 en RAPYD*MC ... a las 18:13».
Rapyd es una pasarela; el nombre no dice nada y no hay regla que lo arregle,
porque detras de RAPYD*MC hay cien comercios distintos. Pero el buzon si lo
sabia:

    18:14:01  boleteria@corferias.com   Confirmación de Compra - Orden #293088
    18:14:04  (la alerta del banco)
    18:36:01  900525906;CORFERIAS INVERSIONES S A S;BOM18970;01;...

Tres segundos despues de la alerta llego la confirmacion de Comic Con, y veinte
minutos despues la factura electronica por los mismos 78.000. Esto junta esas
pistas y devuelve a los sospechosos para que la persona confirme con un toque.

Las reglas, de mas a menos fuerte:

  la factura DIAN por el mismo monto    es casi una prueba
  un correo que menciona el monto       es una prueba fuerte
  asunto de compra y llego enseguida    es una pista
  solo llego cerca                      no alcanza: ruido del buzon

Nada de esto escribe un libro. Solo ordena candidatos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# El asunto que exige la DIAN, el mismo que filtra la ingesta de facturas.
ASUNTO_DIAN = re.compile(r'^\s*(\d{6,12});([^;]+);([^;]+);(\d{2});(.*)$')

# Palabras de un correo transaccional. Sin tildes y en minuscula: se compara
# contra el asunto ya normalizado.
_DE_COMPRA = re.compile(
    r'compra|orden|pedido|confirmaci|recibo|factura|pago|reserva|boleta|'
    r'ticket|entrada|suscripci|transaccion aprobada|order|receipt|payment|'
    r'invoice|booking',
)

# Lo que no es un comercio aunque llegue pegado a la compra.
_NO_SON_COMERCIO = (
    'notificacionesbancolombia.com',
    'documentosbancolombia.com',
    '@bancolombia.com.co',
)

# Dominios de pasarelas y proveedores de factura electronica: el remitente no
# es el comercio. De ahi se toma el nombre del asunto, no del dominio.
_INTERMEDIARIOS = (
    'wompi',
    'payu',
    'mercadopago',
    'rapyd',
    'thefactoryhka',
    'cen.biz',
    'siesa',
    'facture',
    'carvajal',
)

# Antes de la alerta casi nunca llega nada util: la confirmacion sale cuando
# el cargo ya paso. Despues si hay que esperar: una factura tarda hasta horas.
ANTES_MIN = 20
DESPUES_MIN = 120

FACTURA = 6
MONTO = 4
ASUNTO = 2
INMEDIATO = 2  # dentro de 10 minutos
CERCA = 1  # dentro de 30 minutos
MINIMO = 3  # menos que esto es coincidencia de horario
MAXIMO = 3  # sospechosos que se muestran


@dataclass(frozen=True)
class Correo:
    remitente: str  # la direccion
    nombre: str  # el nombre visible del remitente, si lo hay
    asunto: str
    recibido: datetime  # aware
    cuerpo: str


@dataclass(frozen=True)
class FacturaDelMonto:
    """Una factura electronica ya guardada por el mismo monto."""

    proveedor: str
    nit: str
    numero: str
    resumen: str  # las primeras lineas, para que se reconozca


@dataclass(frozen=True)
class Sospechoso:
    comercio: str
    motivo: str
    puntaje: int


def variantes_del_monto(valor: float) -> set[str]:
    """Como puede aparecer escrito un monto en un correo.

    78000 -> 78.000, 78,000, 78000, 78.000,00, 78,000.00. Los correos de
    Colombia mezclan los dos formatos (los de pasarela vienen en el gringo), asi
    que se buscan todos. Con decimales solo si los tiene.
    """
    v = abs(float(valor))
    entero = round(v)
    con_punto = f'{entero:,}'.replace(',', '.')
    con_coma = f'{entero:,}'
    fuera = {str(entero), con_punto, con_coma}
    if abs(v - entero) < 0.005:
        fuera |= {con_punto + ',00', con_coma + '.00'}
    else:
        cent = f'{v:,.2f}'
        fuera |= {cent, cent.replace(',', '_').replace('.', ',').replace('_', '.')}
    return fuera


def menciona_el_monto(texto: str, valor: float) -> bool:
    """Si el monto esta escrito en el texto, como numero entero y no pegado a
    otros digitos: 78.000 no es 178.000 ni 78.0001."""
    if not texto:
        return False
    for v in sorted(variantes_del_monto(valor), key=len, reverse=True):
        # Un monto de tres cifras sin separador (900) aparece en cualquier
        # parte: una hora, un codigo. No cuenta como prueba.
        if v.isdigit() and len(v) < 4:
            continue
        if re.search(r'(?<![\d.,])' + re.escape(v) + r'(?![\d]|[.,]\d)', texto):
            return True
    return False


def _sin_tildes(texto: str) -> str:
    return (
        texto.lower()
        .replace('á', 'a')
        .replace('é', 'e')
        .replace('í', 'i')
        .replace('ó', 'o')
        .replace('ú', 'u')
        .replace('ñ', 'n')
    )


def nombre_del_comercio(c: Correo) -> str:
    """El nombre que se le muestra a la persona.

    Una factura DIAN trae el nombre comercial en el asunto, y es el mejor. Si
    el correo viene de una pasarela o de un facturador, el remitente no es el
    comercio y se cae al asunto. Si no, el nombre visible o el dominio.
    """
    m = ASUNTO_DIAN.match(c.asunto or '')
    if m:
        comercial = m.group(5).strip(' ;')
        return (comercial or m.group(2)).strip().title()
    dominio = c.remitente.rsplit('@', 1)[-1].lower()
    if any(i in dominio for i in _INTERMEDIARIOS):
        # «Transacción APROBADA en Farmatodo Colombia SA - ref. …»
        m = re.search(r'\ben\s+(.+?)(?:\s+-\s+|$)', c.asunto or '')
        if m:
            return m.group(1).strip()
    if c.nombre and '@' not in c.nombre:
        return c.nombre.strip()
    partes = dominio.split('.')
    base = partes[-3] if len(partes) >= 3 and partes[-2] in ('com', 'co') else partes[0]
    return base.capitalize()


def _de_un_banco(c: Correo) -> bool:
    return any(b in c.remitente.lower() for b in _NO_SON_COMERCIO)


def puntuar(
    valor: float,
    momento: datetime,
    correos: list[Correo],
    facturas: list[FacturaDelMonto] | None = None,
) -> list[Sospechoso]:
    """Los sospechosos, del mas fuerte al mas debil. Vacio si nada alcanza.

    `momento` es la hora del cargo (aware). Los correos fuera de la ventana se
    ignoran; los del banco tambien, que son la alerta misma.
    """
    # clave -> [puntaje, motivo del mas fuerte, nombre mas corto]
    puntos: dict[str, list] = {}

    def sumar(comercio: str, n: int, motivo: str) -> None:
        # La confirmacion dice «Corferias» y la factura «Corferias Inversiones
        # S A S»: es el mismo. Se juntan por la primera palabra con sustancia.
        palabras = re.findall(r'[a-z]{4,}', _sin_tildes(comercio))
        clave = palabras[0] if palabras else _sin_tildes(comercio).strip()
        previo = puntos.get(clave)
        if previo is None:
            puntos[clave] = [n, motivo, comercio]
            return
        if n > previo[0]:
            previo[0], previo[1] = n, motivo
        if len(comercio) < len(previo[2]):
            previo[2] = comercio

    for f in facturas or []:
        nombre = f.proveedor.strip().title()
        detalle = f' ({f.resumen})' if f.resumen else ''
        sumar(
            nombre, FACTURA + MONTO, f'factura {f.numero} por el mismo monto{detalle}'
        )

    for c in correos:
        if _de_un_banco(c):
            continue
        minutos = (c.recibido - momento).total_seconds() / 60
        if minutos < -ANTES_MIN or minutos > DESPUES_MIN:
            continue
        n = 0
        pistas = []
        es_factura = bool(ASUNTO_DIAN.match(c.asunto or ''))
        if menciona_el_monto(f'{c.asunto}\n{c.cuerpo}', valor):
            n += MONTO
            pistas.append('dice el monto')
        if es_factura:
            n += ASUNTO + 1
            pistas.append('factura electrónica')
        elif _DE_COMPRA.search(_sin_tildes(c.asunto or '')):
            n += ASUNTO
        if abs(minutos) <= 10:
            n += INMEDIATO
        elif abs(minutos) <= 30:
            n += CERCA
        if n < MINIMO:
            continue
        hora = c.recibido.astimezone(momento.tzinfo).strftime('%H:%M')
        asunto = (c.asunto or '').strip()
        if es_factura:
            asunto = asunto.split(';')[2] if asunto.count(';') >= 3 else asunto
        motivo = f'«{asunto[:60]}» a las {hora}'
        if pistas:
            motivo += ', ' + ' y '.join(pistas)
        sumar(nombre_del_comercio(c), n, motivo)

    orden = sorted(puntos.values(), key=lambda x: -x[0])
    return [Sospechoso(comercio=c, motivo=m, puntaje=n) for n, m, c in orden[:MAXIMO]]
