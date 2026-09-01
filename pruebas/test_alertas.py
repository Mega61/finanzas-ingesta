"""Prueba del parser de alertas contra el archivo real de correos.

Uso:  python automatizacion/pruebas/test_alertas.py

No usa pytest a proposito: se corre a mano y se lee el reporte. Lo que importa
es la ultima linea: si aparece un solo correo NO RECONOCIDO, hay una plantilla
nueva de Bancolombia y el parser se quedo corto.
"""
import collections
import glob
import os
import re
import sys

# El paquete es la carpeta que contiene a pruebas/. Se calcula asi y no
# contando niveles hacia arriba, porque segun el contexto esa carpeta es la raiz
# del repo (en CI) o una subcarpeta de la carpeta de trabajo (en desarrollo).
AQUI = os.path.dirname(os.path.abspath(__file__))
PAQUETE = os.path.dirname(AQUI)
sys.path.insert(0, PAQUETE)

from parsers import bancolombia_alertas as B  # noqa: E402

# El archivo de correos no esta en el repo. Se busca en los sitios donde puede
# estar; si no aparece, la prueba de cobertura se salta y corren las unitarias.
CANDIDATOS = [
    os.path.join(os.path.dirname(PAQUETE), 'Mensajes de Bancolombia'),
    os.path.join(PAQUETE, 'Mensajes de Bancolombia'),
    os.environ.get('ARCHIVO_CORREOS', ''),
]
ARCHIVO = ''
for _c in CANDIDATOS:
    if _c and os.path.isdir(_c):
        ARCHIVO = os.path.join(_c, '*.eml')
        break


def test_parse_monto():
    """Los dos formatos convivien en el mismo buzon."""
    casos = [
        ('178.679,08', 178679.08),   # colombiano
        ('205,967.00', 205967.00),   # gringo
        ('9,000',        9000.0),    # sin decimales, NO son 9
        ('2.301.652,00', 2301652.00),
        ('3,05',            3.05),   # USD
        ('500',           500.0),
        ('39.166,74',   39166.74),
    ]
    for texto, esperado in casos:
        got = B.parse_monto(texto)
        assert abs(got - esperado) < 0.005, f"parse_monto({texto!r}) = {got}, esperaba {esperado}"
    print(f"  parse_monto: {len(casos)}/{len(casos)} ok")


def test_fecha_hora_invertidas():
    """Varias plantillas traen 'el 07:57 a las 10/12/2025'."""
    t = ("Bancolombia: Compraste COP208.203,45 en AMAZON MARKEPLACE NA, el 07:57 "
         "a las 10/12/2025. Esta compra esta asociada a T.Cred *1111.")
    ev = B.parse_texto(t)
    assert ev is not None, "no cazo la plantilla invertida"
    assert ev.fecha.isoformat() == '2025-12-10', ev.fecha
    assert ev.hora == '07:57', ev.hora
    assert ev.valor == -208203.45, ev.valor
    print("  fecha/hora invertidas: ok")


def test_compra_rechazada_se_descarta():
    """Una compra rechazada trae monto pero nunca llega al extracto."""
    t = ("NotificacionTransaccionalBancolombia: tu compra con T.cred *2222 por "
         "COP392.333,00 no fue exitosa, los datos de tu t.cred estan incorrectos. "
         "14:25 12/12/2025.")
    try:
        ev = B.parse_texto(t)
    except B.Descartado as d:
        assert d.motivo == 'compra_rechazada', d.motivo
        print("  compra rechazada se descarta: ok")
        return
    raise AssertionError(f"deberia descartarse, devolvio {ev}")


def test_signo():
    """Negativo = sale plata. Siempre desde tu punto de vista."""
    compra = B.parse_texto("Bancolombia: Compraste COP22.800,00 en DLO*Didi con tu "
                           "T.Cred *2222, el 11/10/2025 a las 03:38.")
    assert compra.valor < 0, compra.valor
    ingreso = B.parse_texto("Bancolombia: Recibiste un pago de Nomina de EMPRESA EJEMPLO "
                            "por $3,819,700.00 en tu cuenta de Ahorros el 27/02/2026 a las 01:24.")
    assert ingreso.valor > 0, ingreso.valor
    print("  signos: ok")


def test_archivo_completo():
    files = sorted(glob.glob(ARCHIVO)) if ARCHIVO else []
    if not files:
        print("  (no encontre el archivo de correos: me salto la prueba de")
        print("   cobertura y corro solo las unitarias. Es lo normal en CI.)")
        return
    ok, desc, fail = [], [], []
    for f in files:
        try:
            ev = B.parse_eml(f)
        except B.Descartado as d:
            desc.append(d.motivo)
            continue
        except Exception as e:
            fail.append((f, f'EXCEPCION {type(e).__name__}: {e}'))
            continue
        if ev is None:
            _, plano = B.cuerpo_texto(f)
            fail.append((f, B._normalizar(plano)[:180]))
        else:
            ok.append(ev)

    tot = len(files)
    print(f"\n  correos en el archivo   : {tot}")
    print(f"  movimientos parseados   : {len(ok)}")
    print(f"  descartados a proposito : {len(desc)}")
    print(f"  NO RECONOCIDOS          : {len(fail)}")

    print("\n  por familia:")
    for p, c in collections.Counter(e.plantilla for e in ok).most_common():
        print(f"    {c:4d}  {p}")

    # invariantes
    sin_fecha = [e for e in ok if e.fecha is None and e.plantilla != 'debito_tarjeta']
    assert not sin_fecha, f"{len(sin_fecha)} eventos sin fecha"
    cero = [e for e in ok if e.valor == 0]
    assert not cero, f"{len(cero)} eventos con monto 0"
    print("\n  invariantes: 0 sin fecha, 0 en cero  ok")

    if fail:
        print(f"\n  !!! {len(fail)} correos sin reconocer:")
        for _, t in fail[:15]:
            print(f"    * {re.sub(r'\\[https?://[^]]+\\]', '', t)[:160]}")
        raise AssertionError(f"{len(fail)} correos sin reconocer de {tot}")
    print(f"\n  COBERTURA: {(len(ok)+len(desc))/tot*100:.1f}%  ({tot} correos, 0 sin clasificar)")


if __name__ == '__main__':
    print("unitarias:")
    test_parse_monto()
    test_fecha_hora_invertidas()
    test_compra_rechazada_se_descarta()
    test_signo()
    print("\narchivo real:")
    test_archivo_completo()
    print("\nTODO OK")
