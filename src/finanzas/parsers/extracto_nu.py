"""Parsea el extracto mensual de la tarjeta Nu (PDF con clave) a movimientos.

Nu no manda alertas por correo: lo que ella gasta con la Nu entra solo por lo
que cuenta en el chat. El extracto es lo unico que el banco dice por escrito,
y sirve para dos cosas:

  el corte      la deuda al cierre es el saldo inicial de su cuenta Nu en
                Firefly, sin tener que teclearlo
  el cruce      lo que el extracto trae y no esta en el libro es algo que se le
                olvido contar (o un prestamo que no se anoto)

Como se ve el PDF, medido sobre el de septiembre de 2026 (4 paginas):

    26 SEP Saltamontes $9.000,00 1 de 1 $9.000,00 2.13% $0,00 $9.000,00 $0,00
    2026   Coffee
    21 SEP 2026 Didi $4.200,00 1 de 1 $4.200,00 2.13% $0,00 $4.200,00 $0,00
    16 SEP Pago      $999.319,23                          -$116.809,20 -$543.608,08
                     ↪ A capital $969.172,28

- **El ano a veces va en la misma linea y a veces en la siguiente**, pegado al
  resto de la descripcion («2026   Coffee»).
- **El primer monto es el de la compra**; el ultimo, lo que queda por pagar. Las
  columnas del medio (cuotas, interes) pueden faltar.
- **Las compras a cuotas de meses anteriores vuelven a salir** en cada extracto
  hasta pagarse (Smart Fit de junio, Facebook de mayo). No son de este
  periodo: se marcan `en_periodo=False`, igual que en el de Bancolombia.
- **«Pago» es un abono** a la tarjeta: para ella es plata que entra a la
  tarjeta, asi que va positivo. Las lineas «↪ A capital ...» son su desglose y
  no son movimientos.

`pdfplumber` con layout=True, como el de Bancolombia.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
from dataclasses import dataclass, field

from finanzas.parsers.extracto_tarjeta import MESES, Extracto, MovExtracto, num_col

PLATA = r'-?\$[\d.]+,\d{2}'
# «26 SEP Saltamontes $9.000,00 ...» o «21 SEP 2026 Didi $4.200,00 ...»
LINEA = re.compile(
    rf'^\s*(\d{{2}})\s+([A-Z]{{3}})(?:\s+(\d{{4}}))?\s+(.+?)\s+({PLATA})(.*)$'
)
# la linea siguiente: «2026   Coffee», o solo «2026»
ANIO = re.compile(r'^\s*(\d{4})\b\s*(.*?)\s*$')
MONTOS = re.compile(PLATA)
TARJETA = re.compile(r'(?:•\s*){4}(\d{4})')
FECHA = r'(\d{1,2})\s+([A-Z]{3})\s+(\d{4})'
CORTE_Y_LIMITE = re.compile(rf'{FECHA}\s+{FECHA}')
PERIODO = re.compile(r'(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\s*-\s*(\d{1,2})\s+([A-Z]{3})')


def _dinero(s: str) -> float:
    return num_col(s.replace('$', ''))


def _fecha(dia: str, mes: str, anio: str | int) -> datetime.date | None:
    n = MESES.get(mes.lower())
    if not n:
        return None
    try:
        return datetime.date(int(anio), n, int(dia))
    except ValueError:
        return None


def _resumen(txt: str, rotulo: str) -> float | None:
    m = re.search(rf'{rotulo}[^\n$]*?({PLATA})', txt)
    return _dinero(m.group(1)) if m else None


@dataclass
class LineaNu:
    """Una linea de la tabla, con lo que el movimiento comun no guarda."""

    fecha: datetime.date
    descripcion: str
    valor: float  # negativo = compra; positivo = pago a la tarjeta
    restante: float | None  # lo que queda por pagar de esa compra
    a_cuotas: bool  # es una compra a cuotas de un periodo anterior


@dataclass
class ExtractoNu(Extracto):
    corte: datetime.date | None = None
    limite_de_pago: datetime.date | None = None
    deuda_total: float | None = None  # al corte: el saldo inicial en el libro
    pago_minimo: float | None = None
    cupo: float | None = None
    lineas: list[LineaNu] = field(default_factory=list)


def parse_texto(txt: str, archivo: str = '') -> ExtractoNu:
    """El texto de todas las paginas -> el extracto. Separado de `parse_pdf`
    para poder probarlo sin el PDF, que tiene los datos de una persona."""
    tarjeta = TARJETA.search(txt)
    ext = ExtractoNu(
        archivo=archivo,
        instrumento=tarjeta.group(1) if tarjeta else '',
        marca='NU',
        periodo_archivo='',
        desde=None,
        hasta=None,
    )
    fechas = CORTE_Y_LIMITE.search(txt)
    if fechas:
        ext.limite_de_pago = _fecha(*fechas.group(1, 2, 3))
        ext.corte = _fecha(*fechas.group(4, 5, 6))
    periodo = PERIODO.search(txt[fechas.end() :] if fechas else txt)
    if periodo:
        d1, m1, a1, d2, m2 = periodo.groups()
        ext.desde = _fecha(d1, m1, a1)
        # el fin del periodo no trae ano: es el del corte
        anio_fin = ext.corte.year if ext.corte else int(a1)
        ext.hasta = _fecha(d2, m2, anio_fin)
    if ext.corte:
        ext.periodo_archivo = f'{ext.corte:%Y%m}'
    ext.deuda_total = _resumen(txt, r'DEUDA TOTAL HASTA EL')
    ext.pago_minimo = _resumen(txt, r'PAGO MÍNIMO')
    ext.cupo = _resumen(txt, r'Tu cupo definido')

    renglones = txt.split('\n')
    for i, renglon in enumerate(renglones):
        g = LINEA.match(renglon)
        if not g:
            continue
        dia, mes, anio, desc, valor, resto = g.groups()
        if not anio:
            # En un «Pago» el ano va despues del desglose («↪ A capital ...»):
            # se busca en los renglones siguientes, sin pasar al otro movimiento.
            for siguiente in renglones[i + 1 : i + 4]:
                if LINEA.match(siguiente):
                    break
                mas = ANIO.match(siguiente)
                if mas:
                    anio = mas.group(1)
                    if mas.group(2) and not mas.group(2).startswith('↪'):
                        desc = f'{desc} {mas.group(2)}'
                    break
        if not anio:
            continue
        fecha = _fecha(dia, mes, anio)
        if not fecha:
            continue
        desc = re.sub(r'\s{2,}', ' ', desc).strip()
        es_pago = desc.lower() == 'pago'
        monto = abs(_dinero(valor))
        montos = MONTOS.findall(resto)
        restante = _dinero(montos[-1]) if montos else None
        en_periodo = True
        if ext.desde and ext.hasta:
            en_periodo = ext.desde <= fecha <= ext.hasta
        firmado = monto if es_pago else -monto
        ext.lineas.append(
            LineaNu(
                fecha=fecha,
                descripcion=desc,
                valor=firmado,
                restante=restante,
                a_cuotas=not en_periodo,
            )
        )
        ext.movimientos.append(
            MovExtracto(
                fecha=fecha,
                descripcion=desc,
                valor=firmado,
                moneda='COP',
                autorizacion=None,
                instrumento=ext.instrumento,
                archivo=archivo,
                en_periodo=en_periodo,
            )
        )
    if not ext.movimientos:
        ext.error = 'no encontre movimientos: ¿cambio el formato del extracto de Nu?'
    return ext


def parse_pdf(ruta: str, clave: str) -> ExtractoNu:
    import pdfplumber

    base = os.path.basename(ruta)
    try:
        with pdfplumber.open(ruta, password=clave) as pdf:
            txt = '\n'.join((p.extract_text(layout=True) or '') for p in pdf.pages)
    except Exception as ex:
        return ExtractoNu(
            base, '', 'NU', '', None, None, error=f'{type(ex).__name__}: {ex}'
        )
    return parse_texto(txt, base)


# ------------------------------------------------------------------ comando


def _plata(v: float | None) -> str:
    return '—' if v is None else f'{v:>14,.2f}'


def describir(ext: ExtractoNu) -> str:
    if ext.error:
        return f'{ext.archivo}: {ext.error}'
    del_periodo = [m for m in ext.lineas if not m.a_cuotas]
    compras = [m for m in del_periodo if m.valor < 0]
    pagos = [m for m in del_periodo if m.valor > 0]
    lineas = [
        f'Nu *{ext.instrumento}  ·  {ext.archivo}',
        f'  periodo        {ext.desde} a {ext.hasta}',
        f'  corte          {ext.corte}   límite de pago {ext.limite_de_pago}',
        f'  cupo           {_plata(ext.cupo)}',
        f'  pago mínimo    {_plata(ext.pago_minimo)}',
        f'  DEUDA AL CORTE {_plata(ext.deuda_total)}   <- saldo inicial de la Nu en el libro',
        '',
        f'  del periodo: {len(compras)} compras por {_plata(-sum(m.valor for m in compras))}, '
        f'{len(pagos)} pagos por {_plata(sum(m.valor for m in pagos))}',
    ]
    for m in del_periodo:
        lineas.append(f'    {m.fecha}  {m.valor:>13,.2f}  {m.descripcion}')
    cuotas = [m for m in ext.lineas if m.a_cuotas]
    if cuotas:
        lineas += [
            '',
            f'  a cuotas de periodos anteriores ({len(cuotas)}), '
            f'quedan {_plata(sum(m.restante or 0 for m in cuotas))}:',
        ]
        for m in cuotas:
            lineas.append(
                f'    {m.fecha}  {m.valor:>13,.2f}  {m.descripcion}  (quedan {m.restante:,.2f})'
            )
    return '\n'.join(lineas)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog='finanzas extracto-nu',
        description='Lee un extracto de la Nu y muestra lo que trae, y la deuda al corte.',
    )
    ap.add_argument('pdf', nargs='+', help='uno o varios PDF del extracto de Nu')
    ap.add_argument(
        '--clave',
        default=os.environ.get('EXTRACTO_NU_CLAVE', ''),
        help='la clave del PDF (la cédula). Por defecto, EXTRACTO_NU_CLAVE',
    )
    a = ap.parse_args(argv)
    malos = 0
    for ruta in a.pdf:
        ext = parse_pdf(ruta, a.clave)
        malos += bool(ext.error)
        print(describir(ext))
        print()
    return 1 if malos else 0
