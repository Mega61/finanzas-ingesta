"""El orquestador. Una pasada completa: bajar, parsear, clasificar, publicar.

    python -m automatizacion.demonio estado          # que hay ahora mismo
    python -m automatizacion.demonio sembrar         # aprender del historico
    python -m automatizacion.demonio bajar           # traer correo nuevo
    python -m automatizacion.demonio procesar        # parsear y clasificar
    python -m automatizacion.demonio publicar        # SECO por defecto
    python -m automatizacion.demonio publicar --en-serio
    python -m automatizacion.demonio ciclo           # todo lo de arriba

La marca de agua importa: el archivo de correos va de sep-2025 a hoy y ese
rango YA esta en Firefly a mano. Sin marca de agua se crearian ~845
duplicados. Por defecto la marca es hoy; se cambia con INGESTA_DESDE.
"""

import argparse

# Funciona corriendo `python demonio.py` desde la carpeta, y tambien
# `python -m automatizacion.demonio` desde arriba. En el contenedor esto ya
# viene por PYTHONPATH, pero no estorba.
import email as _email
import os
import sys
import traceback
from datetime import UTC, datetime, timedelta
from email import policy

from finanzas import config
from finanzas.adaptadores import db, graph
from finanzas.aplicacion import clasificador, conciliador, publicador
from finanzas.dominio import fechas
from finanzas.parsers import bancolombia_alertas as alertas


def marca_de_agua():
    """Solo se publica lo que sea de esta fecha en adelante."""
    v = config.get('INGESTA_DESDE')
    if v:
        return str(v)[:10]
    return str(fechas.hoy())


# --------------------------------------------------------------------- pasos


def paso_estado(cx):
    alm = db.almacen(cx)
    u = alm.usuarios()
    print(f'base            : {db.ruta()}')
    print(f'marca de agua   : {marca_de_agua()}  (no se publica nada anterior)')
    print(f'usuarios        : {len(u)}')
    for x in u:
        print(
            f'   #{x["id"]} {x["nombre"]}  telegram={x["telegram_chat_id"] or "sin vincular"}'
        )
    for tabla in ('buzones', 'correos_crudos', 'pendientes', 'reglas', 'bitacora'):
        n = alm.contar_por_tabla(tabla)
        print(f'{tabla:16}: {n}')
    sin = alm.contar_correos_sin_procesar()
    print(f'{"sin procesar":16}: {sin}')
    filas = db.resumen(cx)
    if filas:
        print('\ncola:')
        for f in filas:
            print(
                f'   {f["estado"]:12} pregunta={f["pregunta"]:10} n={f["n"]:5} '
                f'total={f["total"] or 0:>14,.0f}'
            )
    ori = alm.reglas_por_origen()
    if ori:
        print(
            '\nreglas por origen: ' + ', '.join(f'{r["origen"]}={r["n"]}' for r in ori)
        )


def paso_asegurar_usuario(cx):
    """Crea el usuario y el buzon a partir del .env si no existen."""
    url, tok = config.requerir('FIREFLY_URL', 'FIREFLY_TOKEN')
    uid = db.usuario_upsert(cx, 'Juan', url, tok, config.get('TELEGRAM_CHAT_ID_JUAN'))
    cuenta = config.get('GRAPH_CUENTA')
    bid = None
    if cuenta and config.get('GRAPH_CLIENT_ID'):
        bid = db.buzon_upsert(cx, uid, 'graph', cuenta)
    if config.get('GMAIL_USUARIO') and config.get('GMAIL_APP_PASSWORD'):
        db.buzon_upsert(
            cx, uid, 'imap', config.get('GMAIL_USUARIO'), imap_host='imap.gmail.com:993'
        )
    return uid, bid


def paso_sembrar(cx, uid):
    n, splits = clasificador.sembrar_desde_firefly(cx, uid)
    print(f'  {splits} movimientos leidos de Firefly -> {n} reglas')
    ori = db.almacen(cx).reglas_por_origen()
    print('  por origen: ' + ', '.join(f'{r["origen"]}={r["n"]}' for r in ori))
    return n


def paso_bajar(cx, tope=None, interactivo=False, dias=None):
    buzones = db.almacen(cx).buzones('graph')
    if not buzones:
        print('  no hay buzones de Graph configurados')
        return 0
    total_n = 0
    for b in buzones:
        # Sin ventana, la primera bajada en un contenedor nuevo se trae el
        # buzon completo: fueron 1819 correos de anos atras, con 770 plantillas
        # viejas que el parser no conoce. No hace dano pero ensucia el log y
        # tarda. Por defecto se limita a INGESTA_DIAS_INICIAL.
        atras = dias or int(config.get('INGESTA_DIAS_INICIAL', '30'))
        if b['ultimo_sync'] and not dias:
            atras = 2  # ya sincronizo antes: solo lo reciente, con holgura
        # Graph filtra en UTC. utcnow() esta deprecado en 3.12 y devuelve un
        # naive que solo por convencion se lee como UTC.
        desde = (datetime.now(UTC) - timedelta(days=atras)).strftime(
            '%Y-%m-%dT%H:%M:%SZ'
        )
        try:
            nuevos, rep = graph.bajar(
                cx, b['id'], desde=desde, tope=tope, interactivo=interactivo
            )
            db.buzon_guardar_delta(cx, b['id'], None)
            print(f'  {b["direccion"]}: {nuevos} nuevos, {rep} ya estaban')
            total_n += nuevos
        except graph.SinAutorizacion as ex:
            db.buzon_error(cx, b['id'], str(ex))
            print(f'  {b["direccion"]}: SIN AUTORIZACION — {ex}')
        except Exception as ex:
            db.buzon_error(cx, b['id'], str(ex))
            print(f'  {b["direccion"]}: error — {ex}')
    return total_n


def paso_importar(cx, uid, carpeta=None):
    """Carga el archivo local de .eml a correos_crudos.

    Es solo para rellenar el historico desde este PC: en el servidor los
    correos entran por Graph. Sirve para tener con que probar la
    conciliacion, y para que el parser se pueda re-correr sobre todo el
    historico sin volver al buzon.
    """
    import glob

    carpeta = carpeta or config.ruta_personal('Mensajes de Bancolombia')
    if not os.path.isdir(carpeta):
        print(f'  no existe {carpeta}')
        return 0
    b = db.almacen(cx).primer_buzon(uid)
    if not b:
        print('  no hay buzon; corre primero cualquier otra accion')
        return 0
    archivos = sorted(glob.glob(os.path.join(carpeta, '*.eml')))
    nuevos = rep = 0
    for f in archivos:
        with open(f, encoding='utf-8', errors='replace') as fh:
            msg = _email.message_from_file(fh, policy=policy.default)
        cuerpo = alertas.cuerpo_mensaje(msg)
        if not cuerpo.strip():
            continue
        mid = msg.get('message-id') or os.path.basename(f)
        _, era_nuevo = db.correo_guardar(
            cx,
            b['id'],
            mid,
            str(msg.get('from') or ''),
            str(msg.get('subject') or ''),
            str(msg.get('date') or ''),
            cuerpo,
        )
        nuevos += 1 if era_nuevo else 0
        rep += 0 if era_nuevo else 1
    print(f'  {len(archivos)} archivos: {nuevos} nuevos, {rep} ya estaban')
    return nuevos


def paso_procesar(cx, uid):
    """correos_crudos -> pendientes, ya clasificados."""
    filas = db.correos_sin_procesar(cx, limite=2000)
    if not filas:
        print('  no hay correos sin procesar')
        return {}
    idx = clasificador.Indice(cx, uid)
    marca = marca_de_agua()
    conteo = {'movimiento': 0, 'descartado': 0, 'sin_reconocer': 0, 'repetido': 0}
    for c in filas:
        try:
            ev = alertas.parse_texto(c['cuerpo'], asunto=c['asunto'])
        except alertas.Descartado:
            conteo['descartado'] += 1
            db.correo_marcar_procesado(cx, c['id'])
            continue
        except Exception:
            conteo['sin_reconocer'] += 1
            traceback.print_exc()
            continue
        if ev is None:
            conteo['sin_reconocer'] += 1
            db.correo_marcar_procesado(cx, c['id'])
            continue

        # La marca de agua se aplica AQUI, al nacer el movimiento, no al
        # publicarlo. Antes se aplicaba en el publicador, que solo mira los que
        # ya tienen cuenta resuelta: los viejos sin cuenta se quedaban en
        # 'nuevo' para siempre y el bot preguntaba por transacciones de hace
        # meses. Un movimiento anterior a la marca no se publica NI se pregunta.
        fecha_ev = str(ev.fecha) if ev.fecha else None
        if fecha_ev is None:
            # sin fecha no se puede ubicar en el tiempo ni emparejar con un
            # extracto; se guarda para poder mirarlo, pero no entra a la cola
            db.pendiente_crear(
                cx,
                correo_id=c['id'],
                usuario_id=c['usuario_id'],
                tipo=ev.tipo,
                fecha=None,
                hora=ev.hora,
                moneda=ev.moneda,
                valor=ev.valor,
                instrumento=ev.instrumento,
                clase_instrumento=ev.clase_instrumento,
                traslado_a=ev.traslado_a,
                contraparte=ev.contraparte,
                descripcion=ev.descripcion,
                plantilla=ev.plantilla,
                external_id=publicador.external_id(c['message_id']),
                estado='descartado',
                decidido_por='sin_fecha',
            )
            conteo['sin_fecha'] = conteo.get('sin_fecha', 0) + 1
            db.correo_marcar_procesado(cx, c['id'])
            continue

        if fecha_ev < marca:
            db.pendiente_crear(
                cx,
                correo_id=c['id'],
                usuario_id=c['usuario_id'],
                tipo=ev.tipo,
                fecha=fecha_ev,
                hora=ev.hora,
                moneda=ev.moneda,
                valor=ev.valor,
                instrumento=ev.instrumento,
                clase_instrumento=ev.clase_instrumento,
                traslado_a=ev.traslado_a,
                contraparte=ev.contraparte,
                descripcion=ev.descripcion,
                plantilla=ev.plantilla,
                external_id=publicador.external_id(c['message_id']),
                estado='descartado',
                decidido_por='anterior_a_la_marca_de_agua',
            )
            conteo['historico'] = conteo.get('historico', 0) + 1
            db.correo_marcar_procesado(cx, c['id'])
            continue

        d = clasificador.clasificar(
            cx,
            c['usuario_id'],
            {
                'tipo': ev.tipo,
                'fecha': ev.fecha,
                'instrumento': ev.instrumento,
                'clase_instrumento': ev.clase_instrumento,
                'traslado_a': ev.traslado_a,
                'contraparte': ev.contraparte,
                'descripcion': ev.descripcion,
            },
            indice=idx,
        )

        _, era_nuevo = db.pendiente_crear(
            cx,
            correo_id=c['id'],
            usuario_id=c['usuario_id'],
            tipo=ev.tipo,
            fecha=str(ev.fecha) if ev.fecha else None,
            hora=ev.hora,
            moneda=ev.moneda,
            valor=ev.valor,
            instrumento=ev.instrumento,
            clase_instrumento=ev.clase_instrumento,
            traslado_a=ev.traslado_a,
            contraparte=ev.contraparte,
            descripcion=ev.descripcion,
            plantilla=ev.plantilla,
            external_id=publicador.external_id(c['message_id']),
            **{
                k: d[k]
                for k in (
                    'cuenta_firefly',
                    'cuenta_destino',
                    'categoria',
                    'presupuesto',
                    'confianza',
                    'decidido_por',
                    'pregunta',
                )
            },
        )
        conteo['movimiento' if era_nuevo else 'repetido'] += 1
        db.correo_marcar_procesado(cx, c['id'])
    cx.commit()
    print(
        f'  {len(filas)} correos: '
        + ', '.join(f'{k}={v}' for k, v in conteo.items() if v)
    )
    return conteo


def paso_reclasificar(cx, uid):
    """Vuelve a clasificar lo que todavia no esta en Firefly.

    Sirve cuando el clasificador mejora: lo ya publicado no se toca, pero lo
    que sigue abierto se reevalua con las reglas nuevas.
    """
    filas = db.almacen(cx).pendientes_por_estado('nuevo', 'error')
    if not filas:
        print('  nada por reclasificar')
        return 0
    idx = clasificador.Indice(cx, uid)
    cambios = 0
    for p in filas:
        d = clasificador.clasificar(
            cx,
            p['usuario_id'],
            {
                'tipo': p['tipo'],
                'fecha': p['fecha'],
                'instrumento': p['instrumento'],
                'clase_instrumento': p['clase_instrumento'],
                'traslado_a': p['traslado_a'],
                'contraparte': p['contraparte'],
                'descripcion': p['descripcion'],
            },
            indice=idx,
        )
        antes = (p['categoria'], p['cuenta_firefly'], p['pregunta'])
        ahora = (d['categoria'], d['cuenta_firefly'], d['pregunta'])
        if antes != ahora:
            db.pendiente_actualizar(
                cx,
                p['id'],
                **{
                    k: d[k]
                    for k in (
                        'cuenta_firefly',
                        'cuenta_destino',
                        'categoria',
                        'presupuesto',
                        'confianza',
                        'decidido_por',
                        'pregunta',
                    )
                },
            )
            cambios += 1
            print(
                f'    #{p["id"]} {(p["contraparte"] or "")[:24]:26} '
                f'{antes[0]} -> {ahora[0]}'
                + ('  (ahora se pregunta)' if ahora[2] and not antes[2] else '')
            )
    cx.commit()
    print(f'  {len(filas)} revisados, {cambios} cambiaron')
    return cambios


def paso_publicar(cx, en_serio=False, desde=None):
    desde = desde or marca_de_agua()
    print(f'  marca de agua: {desde}' + ('' if en_serio else '   [SECO, no escribe]'))
    conteo = publicador.publicar_pendientes(cx, desde=desde, dry_run=not en_serio)
    if conteo:
        print('  ' + ', '.join(f'{k}={v}' for k, v in conteo.items()))
    else:
        print('  nada por publicar')
    return conteo


# ---------------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='finanzas',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        'accion',
        choices=[
            'estado',
            'sembrar',
            'bajar',
            'importar',
            'procesar',
            'reclasificar',
            'publicar',
            'conciliar',
            'ciclo',
        ],
    )
    ap.add_argument(
        '--en-serio',
        action='store_true',
        help='publicar de verdad en Firefly (por defecto es seco)',
    )
    ap.add_argument('--tope', type=int, help='maximo de correos a bajar')
    ap.add_argument('--dias', type=int, help='bajar los ultimos N dias')
    ap.add_argument('--desde', help='marca de agua YYYY-MM-DD')
    ap.add_argument('--carpeta', help='carpeta de .eml o de PDF de extracto')
    ap.add_argument(
        '--interactivo',
        action='store_true',
        help='permitir el device flow de Graph si falta el token',
    )
    a = ap.parse_args(argv)

    db.inicializar()
    cx = db.conectar()
    try:
        if a.accion == 'estado':
            paso_estado(cx)
            return 0

        uid, _ = paso_asegurar_usuario(cx)

        if a.accion == 'sembrar':
            paso_sembrar(cx, uid)
        elif a.accion == 'bajar':
            paso_bajar(cx, tope=a.tope, interactivo=a.interactivo, dias=a.dias)
        elif a.accion == 'importar':
            paso_importar(cx, uid, carpeta=a.carpeta)
        elif a.accion == 'procesar':
            paso_procesar(cx, uid)
        elif a.accion == 'reclasificar':
            paso_reclasificar(cx, uid)
        elif a.accion == 'conciliar':
            conciliador.correr(cx, carpeta=a.carpeta, dry_run=not a.en_serio)
        elif a.accion == 'publicar':
            paso_publicar(cx, en_serio=a.en_serio, desde=a.desde)
        elif a.accion == 'ciclo':
            n = db.almacen(cx).contar_reglas()
            if n == 0:
                print('sembrando reglas (primera vez)...')
                paso_sembrar(cx, uid)
            print('bajando correo...')
            paso_bajar(cx, tope=a.tope, interactivo=a.interactivo, dias=a.dias)
            print('procesando...')
            paso_procesar(cx, uid)
            print('publicando...')
            paso_publicar(cx, en_serio=a.en_serio, desde=a.desde)
            print('\nestado final:')
            paso_estado(cx)
        return 0
    finally:
        cx.close()


if __name__ == '__main__':
    sys.exit(main())
