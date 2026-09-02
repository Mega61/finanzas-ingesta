"""Parsea una factura electronica DIAN (UBL 2.1) a cabecera + lineas.

Las tres cadenas que mandan factura a este buzon — Exito, D1 y Supervaquita —
usan exactamente la misma estructura, porque no es de ellas: es el estandar
DIAN. El correo trae un ZIP, el ZIP trae un XML `AttachedDocument`, y el
documento de verdad viaja como texto XML dentro de un `cbc:Description`. Por eso
hay dos niveles de parseo y no uno.

Verificado contra los 430 correos del archivo local: 430 parseados, 0 fallos.

Cuatro cosas que no son obvias y que estan resueltas aqui:

**El codigo de producto es la llave, no la descripcion.** Exito y D1 truncan el
nombre a ~18-29 caracteres (GTA RELLENA CHOC BIS, Queso Fresco Semib), asi que
categorizar por texto es adivinar. El codigo si es estable. Pero Exito usa PLU
interno y D1/Supervaquita usan EAN, y los espacios de numeracion chocan: la
llave es (nit, codigo), nunca el codigo solo.

**El total de cabecera solo es confiable en PayableAmount.** En una factura de
D1 el TaxExclusiveAmount dice 10.659 con un LineExtensionAmount de 19.559. Los
otros campos de impuesto no cuadran entre cadenas.

**Hay notas credito.** Llegan por el mismo correo, con raiz CreditNote y las
lineas bajo CreditNoteLine. Si se parsean como factura, una devolucion suma en
vez de restar. `signo` es -1 para esas.

**Exito mete el tiquete de caja completo en cbc:Note.** De ahi salen los medios
de pago, los puntos redimidos y los descuentos, que el UBL no modela en ninguna
parte. Ver `anotaciones_exito`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = {
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
}
_CBC = '{' + NS['cbc'] + '}'

# El asunto que pone la DIAN: NIT;RAZON SOCIAL;NUMERO;TIPO;NOMBRE COMERCIAL.
# Los 430 correos del archivo lo cumplen, incluidos los de las tres cadenas.
# Filtrar por esto y no por remitente es lo que hace que un supermercado nuevo
# funcione sin tocar codigo.
ASUNTO_DIAN = re.compile(r'^\s*(\d{6,12});([^;]+);([^;]+);(\d{2});(.*)$')


class NoEsFactura(Exception):
    """El XML no trae ningun documento UBL que sepamos leer."""


@dataclass(frozen=True)
class Linea:
    n: int
    codigo: str
    descripcion: str
    cantidad: float
    unidad: str | None
    precio_unitario: float
    descuento: float
    iva_pct: float | None
    total: float


@dataclass
class Factura:
    nit: str
    proveedor: str
    numero: str
    cufe: str | None
    tipo: str  # factura | nota_credito
    signo: int  # 1 factura, -1 nota credito
    fecha: str
    hora: str | None
    sede: str | None
    direccion: str | None
    moneda: str
    subtotal: float
    descuento: float
    total: float
    lineas: list[Linea] = field(default_factory=list)
    # Solo Exito: lo que sale del tiquete de caja embebido en las notas.
    medios_pago: list[str] = field(default_factory=list)
    puntos_redimidos: float = 0.0
    ahorro: float = 0.0
    pagada_con_puntos: bool = False


def _t(el, ruta: str) -> str | None:
    if el is None:
        return None
    n = el.find(ruta, NS)
    return n.text if n is not None else None


def _f(el, ruta: str, defecto: float = 0.0) -> float:
    v = _t(el, ruta)
    try:
        return float(v)
    except (TypeError, ValueError):
        return defecto


def documento(xml: str) -> ET.Element:
    """El UBL de verdad, desenvuelto del AttachedDocument si hace falta.

    No se busca el CDATA por posicion: hay proveedores que ponen varios
    cbc:Description y solo uno trae el documento.
    """
    raiz = ET.fromstring(xml.encode('utf-8'))
    if raiz.tag.split('}')[-1] != 'AttachedDocument':
        return raiz
    for desc in raiz.iter(_CBC + 'Description'):
        s = (desc.text or '').strip()
        if s.startswith('<?xml') or '<Invoice' in s or '<CreditNote' in s:
            return ET.fromstring(s.encode('utf-8'))
    raise NoEsFactura('AttachedDocument sin documento embebido')


def _numero_co(texto: str) -> float:
    """124.645,00 -> 124645.0. El punto es miles y la coma decimales."""
    t = texto.strip().replace('.', '').replace(',', '.')
    try:
        return float(t)
    except ValueError:
        return 0.0


def anotaciones_exito(notas: list[str]) -> dict:
    """Lo que Exito mete en cbc:Note y el UBL no modela.

    El tiquete de caja viaja en franjas con el formato
    `Franja ----[TITULO]----|campo:valor|campo:valor`. Interesan tres:

      FORMAS DE PAGO    -> MEDIOS DE PAGO:PAGO CON PUNTOS,TARJETA DEBITO,
      PUNTOS REDIMIDOS  -> Pnts redimidos   :124.645,00
      DESCUENTOS        -> AHORRO: $325.479

    `pagada_con_puntos` es la razon por la que esto existe: el reloj y la
    licuadora entraron por redencion de puntos, no por plata que salio de una
    cuenta. Sin la marca, dos compras que nunca fueron gasto de mercado
    inflaban el mes en mas de un millon de pesos cada una.

    Ojo: la marca NO alcanza por si sola para excluir. Hay redenciones
    parciales de 400 puntos sobre un mercado normal de 15.750. Lo que decide si
    algo es mercado es la categoria del producto; esto es la senal de apoyo.
    """
    blob = '\n'.join(n or '' for n in notas)
    fuera: dict = {'medios_pago': [], 'puntos_redimidos': 0.0, 'ahorro': 0.0}

    m = re.search(r'MEDIOS DE PAGO:([^|]*)', blob)
    if m:
        fuera['medios_pago'] = [x.strip() for x in m.group(1).split(',') if x.strip()]

    m = re.search(r'Pnts redimidos\s*:\s*([\d.,]+)', blob)
    if m:
        fuera['puntos_redimidos'] = _numero_co(m.group(1))

    m = re.search(r'AHORRO:\s*\$\s*([\d.,]+)', blob)
    if m:
        fuera['ahorro'] = _numero_co(m.group(1))

    fuera['pagada_con_puntos'] = 'PAGO CON PUNTOS' in blob
    return fuera


def parsear(xml: str) -> Factura:
    """El XML de un correo -> una Factura con sus lineas."""
    doc = documento(xml)
    raiz = doc.tag.split('}')[-1]
    if raiz not in ('Invoice', 'CreditNote'):
        raise NoEsFactura('raiz inesperada: ' + raiz)
    es_nc = raiz == 'CreditNote'

    prov = doc.find('cac:AccountingSupplierParty', NS)
    tot = doc.find('cac:LegalMonetaryTotal', NS)

    # La fecha viene como 2026-08-07 y la hora aparte, pero algunas cadenas
    # mandan el instante completo en IssueDate. Se parte por si acaso.
    crudo = (_t(doc, 'cbc:IssueDate') or '').strip()
    fecha, _, resto = crudo.partition(' ')
    hora = (_t(doc, 'cbc:IssueTime') or resto or '').strip() or None

    f = Factura(
        nit=(_t(prov, './/cbc:CompanyID') or '').strip(),
        # El nombre llega con mojibake en 159 de los 430 correos
        # (ALMACENES ?XITO S.A): se guarda, pero agrupar por nombre es un
        # error. El NIT es la identidad.
        proveedor=(_t(prov, './/cbc:RegistrationName') or '').strip(),
        numero=(_t(doc, 'cbc:ID') or '').strip(),
        cufe=(_t(doc, 'cbc:UUID') or '').strip() or None,
        tipo='nota_credito' if es_nc else 'factura',
        signo=-1 if es_nc else 1,
        fecha=fecha,
        hora=hora,
        sede=(_t(prov, './/cac:PhysicalLocation//cbc:CityName') or '').strip() or None,
        direccion=(_t(prov, './/cac:PhysicalLocation//cbc:Line') or '').strip() or None,
        moneda=(_t(doc, 'cbc:DocumentCurrencyCode') or 'COP').strip(),
        subtotal=_f(tot, 'cbc:LineExtensionAmount'),
        descuento=_f(tot, 'cbc:AllowanceTotalAmount'),
        total=_f(tot, 'cbc:PayableAmount'),
    )

    etiqueta = 'cac:CreditNoteLine' if es_nc else 'cac:InvoiceLine'
    campo_cant = 'cbc:CreditedQuantity' if es_nc else 'cbc:InvoicedQuantity'
    for i, ln in enumerate(doc.findall(etiqueta, NS), 1):
        item = ln.find('cac:Item', NS)
        # StandardItemIdentification es el EAN (D1, Supervaquita).
        # SellersItemIdentification es el PLU interno (Exito).
        codigo = (
            _t(item, 'cac:StandardItemIdentification/cbc:ID')
            or _t(item, 'cac:SellersItemIdentification/cbc:ID')
            or ''
        ).strip()
        qn = ln.find(campo_cant, NS)
        f.lineas.append(
            Linea(
                n=i,
                codigo=codigo,
                descripcion=(_t(item, 'cbc:Description') or '').strip(),
                cantidad=_f(ln, campo_cant, 1.0),
                unidad=(qn.get('unitCode') if qn is not None else None),
                precio_unitario=_f(ln, 'cac:Price/cbc:PriceAmount'),
                # El descuento va por linea como AllowanceCharge, por eso
                # precio * cantidad casi nunca da total.
                descuento=_f(ln, 'cac:AllowanceCharge/cbc:Amount'),
                iva_pct=(
                    _f(ln, 'cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent')
                    if ln.find('cac:TaxTotal', NS) is not None
                    else None
                ),
                total=_f(ln, 'cbc:LineExtensionAmount'),
            )
        )

    notas = [(n.text or '') for n in doc.findall('cbc:Note', NS)]
    if notas:
        a = anotaciones_exito(notas)
        f.medios_pago = a['medios_pago']
        f.puntos_redimidos = a['puntos_redimidos']
        f.ahorro = a['ahorro']
        f.pagada_con_puntos = a['pagada_con_puntos']
    return f
