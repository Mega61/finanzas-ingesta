"""Parsea un extracto de tarjeta de Bancolombia (PDF cifrado) a movimientos.

Los PDF vienen protegidos con la cedula como clave. Hay dos formatos segun la
epoca, y la moneda cambia por seccion dentro del mismo archivo:

  2022-2025:  <auth6>  dd/mm/yyyy DESCRIPCION 1,204,158.00-      (formato anglo)
  2026:       <auth6>  dd/mm/yyyy DESCRIPCION $ 14.400,00 1/1    (formato colombiano)

Portado de reconciliacion/api/21_parse_tarjetas.py, que ya estaba validado
contra los xlsx de mayo, julio y agosto de 2026 verificados a mano. Se trae
aqui para que automatizacion/ sea autocontenida.

`pdfplumber` con layout=True es lo unico que parsea bien las columnas.
"""
import datetime
import os
import re
from dataclasses import dataclass, field

# formato viejo: auth, fecha, desc, monto anglo con '-' opcional al final
VIEJO = re.compile(
    r'^\s*([A-Z0-9]{6})?\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+([\d,]+\.\d{2})(-?)(?:\s|$)')
# formato 2026: con $ y separador decimal colombiano
NUEVO = re.compile(
    r'^\s*([A-Z0-9]{6})?\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+\$\s*(-?[\d.]+,\d{2})(-?)(?:\s|$)')
# Periodo, dos formatos. El viejo es explicito:
PERIODO = re.compile(r'Desde:\s*(\d{2}/\d{2}/\d{4})\s*Hasta:\s*(\d{2}/\d{2}/\d{4})')
# El de 2026 viene en espanol abreviado: '30 jul - 30 ago. 2026'
MESES = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
         'jul': 7, 'ago': 8, 'sep': 9, 'set': 9, 'oct': 10, 'nov': 11, 'dic': 12}
PERIODO_ES = re.compile(
    r'(\d{1,2})\s+([a-z]{3})\.?\s*[-–]\s*(\d{1,2})\s+([a-z]{3})\.?\s*(\d{4})', re.I)  # noqa: RUF001


def _periodo_es(txt):
    """'30 jul - 30 ago. 2026' -> (2026-07-30, 2026-08-30).

    Si el primer mes es mayor que el segundo, el periodo cruza el ano nuevo y
    la fecha inicial es del ano anterior."""
    m = PERIODO_ES.search(txt)
    if not m:
        return None, None
    d1, m1, d2, m2, anio = m.groups()
    n1, n2 = MESES.get(m1.lower()), MESES.get(m2.lower())
    if not n1 or not n2:
        return None, None
    anio = int(anio)
    try:
        hasta = datetime.date(anio, n2, int(d2))
        desde = datetime.date(anio - 1 if n1 > n2 else anio, n1, int(d1))
    except ValueError:
        return None, None
    return desde, hasta

# Nombre del archivo: Extracto_<id>_<YYYYMM>_TARJETA_<MARCA>_<L4>.pdf
NOMBRE = re.compile(r'_(\d{6})_TARJETA_([A-Z]+)_(\d{3,4})\.pdf$', re.I)


@dataclass
class MovExtracto:
    fecha: datetime.date
    descripcion: str
    valor: float          # negativo = sale plata (igual que en las alertas)
    moneda: str
    autorizacion: str | None
    instrumento: str      # ultimos 4 digitos
    archivo: str
    en_periodo: bool


@dataclass
class Extracto:
    archivo: str
    instrumento: str
    marca: str
    periodo_archivo: str
    desde: datetime.date | None
    hasta: datetime.date | None
    movimientos: list[MovExtracto] = field(default_factory=list)
    error: str | None = None


def num_anglo(s):
    return float(s.replace(',', ''))


def num_col(s):
    neg = s.startswith('-')
    s = s.lstrip('-')
    v = float(s.replace('.', '').replace(',', '.'))
    return -v if neg else v


def _fecha(s):
    # El extracto trae dd/mm/aaaa, sin hora ni zona: solo interesa el dia.
    return datetime.datetime.strptime(s, '%d/%m/%Y').date()  # noqa: DTZ007


def parse_pdf(ruta, clave):
    """Devuelve un Extracto. Si el PDF no se puede abrir, con `error` puesto."""
    import pdfplumber

    base = os.path.basename(ruta)
    m = NOMBRE.search(base)
    if not m:
        return Extracto(base, '', '', '', None, None, error='nombre no reconocido')
    periodo_archivo, marca, l4 = m.groups()
    ext = Extracto(base, l4, marca.upper(), periodo_archivo, None, None)

    try:
        with pdfplumber.open(ruta, password=clave) as pdf:
            txt = '\n'.join((p.extract_text(layout=True) or '') for p in pdf.pages)
    except Exception as ex:
        ext.error = f"{type(ex).__name__}: {ex}"
        return ext

    pm = PERIODO.search(txt)
    if pm:
        ext.desde, ext.hasta = _fecha(pm.group(1)), _fecha(pm.group(2))
    else:
        ext.desde, ext.hasta = _periodo_es(txt)

    moneda = 'COP'
    for linea in txt.split('\n'):
        U = linea.upper()
        # la moneda cambia por seccion dentro del mismo extracto
        if 'ESTADO DE CUENTA EN' in U or 'MONEDA:' in U:
            moneda = 'USD' if 'DOLAR' in U else 'COP'
            continue

        # Los dos formatos marcan los abonos de forma distinta, y confundirlos
        # invierte el signo de cada pago de tarjeta:
        #   2026:      el signo va DENTRO del monto -> '$ -3.605.583,00'
        #   2022-2025: el signo va como sufijo      -> '1,204,158.00-'
        g = NUEVO.match(linea)
        if g:
            auth, f, desc, monto, signo = g.groups()
            # en el extracto un cargo es positivo; para el usuario es plata que
            # sale, asi que se invierte y con eso los abonos quedan positivos
            valor = -num_col(monto)
        else:
            g = VIEJO.match(linea)
            if not g:
                continue
            auth, f, desc, monto, signo = g.groups()
            v = num_anglo(monto)
            valor = v if signo == '-' else -v

        try:
            fecha = _fecha(f)
        except ValueError:
            continue

        desc = re.sub(r'\s{2,}', ' ', desc).strip()
        if not desc or len(desc) < 2:
            continue

        en_periodo = True
        if ext.desde and ext.hasta:
            en_periodo = ext.desde <= fecha <= ext.hasta

        ext.movimientos.append(MovExtracto(
            fecha=fecha, descripcion=desc, valor=valor, moneda=moneda,
            autorizacion=auth, instrumento=l4, archivo=base,
            en_periodo=en_periodo))
    return ext


def parse_carpeta(carpeta, clave, patron='*TARJETA*.pdf'):
    import glob
    salida = []
    for f in sorted(glob.glob(os.path.join(carpeta, patron))):
        salida.append(parse_pdf(f, clave))
    return salida


if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import config

    clave = config.get('EXTRACTO_CLAVE') or config.get('CLAVE')
    if not clave:
        sys.exit('falta EXTRACTO_CLAVE (la cedula) en el .env')
    carpeta = (sys.argv[1] if len(sys.argv) > 1
               else os.path.join(config.RAIZ, 'Extractos Bancolombia', '_pdf'))
    exts = parse_carpeta(carpeta, clave)
    ok = [e for e in exts if not e.error]
    print(f"{len(exts)} extractos, {len(ok)} abiertos, "
          f"{sum(len(e.movimientos) for e in ok)} movimientos\n")
    for e in sorted(ok, key=lambda x: x.periodo_archivo)[-6:]:
        print(f"  {e.archivo}")
        print(f"    {e.marca} *{e.instrumento}  periodo {e.desde} -> {e.hasta}  "
              f"{len(e.movimientos)} movs")
        for mv in e.movimientos[:3]:
            print(f"      {mv.fecha} {mv.moneda} {mv.valor:>13,.2f} "
                  f"{mv.descripcion[:40]}")
    malos = [e for e in exts if e.error]
    if malos:
        print(f"\n{len(malos)} con error:")
        for e in malos[:5]:
            print(f"  {e.archivo}: {e.error}")
