# -*- coding: utf-8 -*-
"""Que toda variable que el codigo lee de verdad llegue al contenedor.

    python pruebas/test_config.py

Existe por un bug concreto: se agrego GEMINI_API_KEY a .env.ejemplo y al codigo,
pero no a `stack.portainer.yml`. El stack pasa SOLO las variables listadas
explicitamente en `environment:`, asi que la key estaba puesta en Portainer y
nunca llegaba al contenedor. Desde afuera se veia como "sin API key" sin ninguna
pista de por que.

Explicitar las variables es lo correcto por seguridad (no se filtra el entorno
del host al contenedor), pero entonces la lista hay que mantenerla, y eso lo
verifica esta prueba.
"""
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
PAQUETE = os.path.dirname(AQUI)
sys.path.insert(0, PAQUETE)

# Estas NO tienen que estar en el stack, y por que:
EXENTAS = {
    # las pone el Dockerfile como ENV, con el commit del build
    'GIT_SHA', 'BUILD_FECHA',
    # tienen default en el codigo y no hay razon para tocarlas por stack
    'FIREFLY_UA', 'FIREFLY_TIMEOUT', 'GEMINI_TIMEOUT',
    # nombre viejo, se lee por compatibilidad con .bancolombia.env
    'CLAVE',
    # las declara el stack aparte, no dentro de environment
    'FINANZAS_DATOS',
}

MODULOS = ('config.py', 'ia.py', 'clasificador.py', 'demonio.py', 'servicio.py',
           'firefly.py', 'telegram.py', 'conciliador.py', 'bot.py', 'asesor.py',
           'interprete.py', 'presupuestos.py',
           os.path.join('ingesta', 'graph.py'),
           os.path.join('parsers', 'extracto_tarjeta.py'))


def _leer(nombre):
    ruta = os.path.join(PAQUETE, nombre)
    if not os.path.exists(ruta):
        return ''
    with open(ruta, encoding='utf-8') as fh:
        return fh.read()


def variables_del_codigo():
    """Las que el codigo lee con config.get(...)."""
    todo = '\n'.join(_leer(m) for m in MODULOS)
    return set(re.findall(r"""config\.get\(\s*['"]([A-Z][A-Z0-9_]+)['"]""", todo))


def variables_documentadas():
    return set(re.findall(r'^([A-Z][A-Z0-9_]+)=', _leer('.env.ejemplo'), re.M))


def variables_del_stack():
    texto = _leer('stack.portainer.yml')
    try:
        import yaml
        d = yaml.safe_load(texto)
        return set(d['services']['ingesta']['environment'].keys())
    except ImportError:
        # sin pyyaml se saca a mano: en el bloque environment las claves son
        # `NOMBRE:` con dos niveles de indentacion
        dentro = False
        salida = set()
        for linea in texto.split('\n'):
            if re.match(r'^\s{4}environment:', linea):
                dentro = True
                continue
            if dentro:
                if re.match(r'^\s{0,4}\S', linea):
                    break
                m = re.match(r'^\s{6}([A-Z][A-Z0-9_]+):', linea)
                if m:
                    salida.add(m.group(1))
        return salida


def test_stack_pasa_todo():
    codigo = variables_del_codigo()
    doc = variables_documentadas()
    stack = variables_del_stack()

    assert stack, "no pude leer las variables de stack.portainer.yml"

    faltan = (codigo | doc) - stack - EXENTAS
    if faltan:
        print("  Variables que el codigo lee o que estan documentadas, pero que")
        print("  stack.portainer.yml NO le pasa al contenedor:")
        for f in sorted(faltan):
            de_donde = []
            if f in codigo:
                de_donde.append('la lee el codigo')
            if f in doc:
                de_donde.append('esta en .env.ejemplo')
            print(f"    {f:26} ({', '.join(de_donde)})")
        raise AssertionError(
            f"{len(faltan)} variables no llegan al contenedor. Agregalas al "
            f"bloque environment: de stack.portainer.yml, o a EXENTAS aqui si "
            f"de verdad no hacen falta.")
    print(f"  el stack pasa las {len(stack)} variables necesarias  ok")

    # al reves: algo en el stack que nadie lee es basura acumulada
    sobran = stack - codigo - doc - {'TZ', 'FINANZAS_DATOS', 'FIREFLY_URL'}
    if sobran:
        print(f"  aviso: el stack pasa {len(sobran)} que nadie lee: "
              f"{', '.join(sorted(sobran))}")


def test_ejemplo_documenta_lo_que_el_codigo_lee():
    codigo = variables_del_codigo()
    doc = variables_documentadas()
    sin_documentar = codigo - doc - EXENTAS
    if sin_documentar:
        print(f"  aviso: {len(sin_documentar)} variables que el codigo lee no "
              f"estan en .env.ejemplo:")
        for f in sorted(sin_documentar):
            print(f"    {f}")
    else:
        print("  .env.ejemplo documenta todo lo que el codigo lee  ok")


if __name__ == '__main__':
    print("configuracion:")
    test_stack_pasa_todo()
    test_ejemplo_documenta_lo_que_el_codigo_lee()
    print("\nTODO OK")
