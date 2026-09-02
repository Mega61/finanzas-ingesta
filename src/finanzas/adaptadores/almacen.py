"""El unico lugar donde hay SQL.

Antes habia 40 consultas repartidas en seis archivos, 23 de ellas dentro de
`bot.py`. Con eso, cambiar una columna del esquema obligaba a buscar por todo
el repo, y nada garantizaba que la busqueda fuera completa.

El resto del sistema le habla a este objeto con metodos que dicen QUE se quiere,
no COMO se consulta. Eso tambien es lo que permite, mas adelante, cambiar SQLite
por otra cosa sin tocar la logica: el `Protocol` de abajo es el contrato.

Convenciones:
- Los metodos que leen devuelven filas de sqlite3 (acceso por nombre) o dicts.
- Los que escriben hacen commit, porque cada uno es una operacion completa.
- Ninguno imprime ni lanza excepciones propias: si SQLite falla, sube tal cual.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from finanzas.dominio import texto

CAMPOS_PENDIENTE = (
    'correo_id',
    'usuario_id',
    'tipo',
    'fecha',
    'hora',
    'moneda',
    'valor',
    'instrumento',
    'clase_instrumento',
    'traslado_a',
    'contraparte',
    'descripcion',
    'plantilla',
    'cuenta_firefly',
    'cuenta_destino',
    'categoria',
    'presupuesto',
    'etiquetas',
    'confianza',
    'decidido_por',
    'estado',
    'pregunta',
    'external_id',
)

# Lo que se puede ACTUALIZAR de un pendiente: los campos de creacion mas los
# que se llenan despues. Se valida contra esta lista porque el nombre de una
# columna no se puede parametrizar en SQL, hay que interpolarlo.
COLUMNAS_PENDIENTE = (
    *CAMPOS_PENDIENTE,
    'firefly_id',
    'visto_en',
    'valor_confirmado',
    'preguntado_en',
)

# Los estados en los que un movimiento todavia esta abierto: no llego a Firefly
# o llego pero falta confirmarlo.
ESTADOS_ABIERTOS = ('nuevo', 'publicado', 'error')


@runtime_checkable
class Repositorio(Protocol):
    """Lo que el resto del sistema necesita de un almacen.

    Es un Protocol y no una clase base a proposito: no obliga a heredar, y una
    implementacion falsa para pruebas solo tiene que tener estos metodos.
    """

    def pendiente(self, pendiente_id: int) -> sqlite3.Row | None: ...
    def pendientes_por_publicar(self, limite: int = 200) -> list[sqlite3.Row]: ...
    def pendientes_por_preguntar(self, limite: int = 50) -> list[sqlite3.Row]: ...
    def actualizar_pendiente(self, pendiente_id: int, **campos: Any) -> None: ...


def _a_json(valor: Any) -> str | None:
    """La bitacora es el unico rastro de lo que se le mando a Firefly, asi que
    tiene que quedar como JSON de verdad y no como repr de Python: con
    str({'a': 1}) queda {'a': 1}, con comillas simples, que json.loads no lee.
    Cuando algo no es serializable se cae a str antes que perder la anotacion,
    que es justo la que se necesita en el camino del error."""
    if valor is None:
        return None
    if isinstance(valor, str):
        return valor[:4000]
    try:
        return json.dumps(valor, ensure_ascii=False, default=str)[:4000]
    except (TypeError, ValueError):
        return str(valor)[:4000]


class Almacen:
    """SQLite. Recibe la conexion ya abierta: no decide donde vive la base."""

    def __init__(self, cx: sqlite3.Connection):
        self.cx = cx

    # ------------------------------------------------------------- esquema

    @classmethod
    def abrir(cls, ruta: str | Path, esquema: str | Path | None = None) -> Almacen:
        cx = sqlite3.connect(str(ruta), timeout=30)
        cx.row_factory = sqlite3.Row
        cx.execute('PRAGMA foreign_keys = ON')
        alm = cls(cx)
        if esquema:
            alm.inicializar(esquema)
        return alm

    def inicializar(self, esquema: str | Path) -> None:
        """Crea el esquema. Es idempotente."""
        self.cx.executescript(Path(esquema).read_text(encoding='utf-8'))
        self.cx.commit()

    def cerrar(self) -> None:
        self.cx.close()

    # ------------------------------------------------------------ usuarios

    def usuario_por_nombre(self, nombre: str) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM usuarios WHERE nombre = ?', (nombre,)
        ).fetchone()

    def usuarios(self) -> list[sqlite3.Row]:
        return self.cx.execute(
            'SELECT id, nombre, telegram_chat_id FROM usuarios ORDER BY id'
        ).fetchall()

    def guardar_usuario(
        self,
        nombre: str,
        firefly_url: str,
        firefly_token: str,
        telegram_chat_id: str | None = None,
    ) -> int:
        fila = self.usuario_por_nombre(nombre)
        if fila:
            self.cx.execute(
                """UPDATE usuarios SET firefly_url = ?, firefly_token_enc = ?,
                   telegram_chat_id = COALESCE(?, telegram_chat_id)
                   WHERE id = ?""",
                (firefly_url, firefly_token, telegram_chat_id, fila['id']),
            )
            self.cx.commit()
            return fila['id']
        cur = self.cx.execute(
            """INSERT INTO usuarios (nombre, firefly_url, firefly_token_enc,
               telegram_chat_id) VALUES (?, ?, ?, ?)""",
            (nombre, firefly_url, firefly_token, telegram_chat_id),
        )
        self.cx.commit()
        return cur.lastrowid

    def vincular_chat(self, chat_id: str) -> int | None:
        """Ata un chat de Telegram al primer usuario que no tenga uno.

        Es lo que pasa en el primer /start: el usuario existe (lo creo el
        arranque desde el .env) pero todavia no sabe a que chat escribirle.
        """
        fila = self.cx.execute(
            'SELECT id FROM usuarios WHERE telegram_chat_id IS NULL ORDER BY id LIMIT 1'
        ).fetchone()
        if not fila:
            return None
        self.cx.execute(
            'UPDATE usuarios SET telegram_chat_id = ? WHERE id = ?',
            (str(chat_id), fila['id']),
        )
        self.cx.commit()
        return fila['id']

    # -------------------------------------------------------------- buzones

    def buzones(self, proveedor: str | None = None) -> list[sqlite3.Row]:
        if proveedor:
            return self.cx.execute(
                'SELECT * FROM buzones WHERE proveedor = ? AND activo = 1', (proveedor,)
            ).fetchall()
        return self.cx.execute('SELECT * FROM buzones WHERE activo = 1').fetchall()

    def primer_buzon(self, usuario_id: int) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM buzones WHERE usuario_id = ? ORDER BY id LIMIT 1',
            (usuario_id,),
        ).fetchone()

    def guardar_buzon(
        self,
        usuario_id: int,
        proveedor: str,
        direccion: str,
        secreto: str | None = None,
        imap_host: str | None = None,
    ) -> int:
        fila = self.cx.execute(
            'SELECT id FROM buzones WHERE usuario_id = ? AND direccion = ?',
            (usuario_id, direccion),
        ).fetchone()
        if fila:
            return fila['id']
        cur = self.cx.execute(
            """INSERT INTO buzones (usuario_id, proveedor, direccion,
               secreto_enc, imap_host) VALUES (?, ?, ?, ?, ?)""",
            (usuario_id, proveedor, direccion, secreto, imap_host),
        )
        self.cx.commit()
        return cur.lastrowid

    def marcar_sync(self, buzon_id: int) -> None:
        self.cx.execute(
            """UPDATE buzones SET ultimo_sync = datetime('now'),
               ultimo_error = NULL WHERE id = ?""",
            (buzon_id,),
        )
        self.cx.commit()

    def marcar_error_buzon(self, buzon_id: int, mensaje: str) -> None:
        self.cx.execute(
            'UPDATE buzones SET ultimo_error = ? WHERE id = ?',
            (str(mensaje)[:500], buzon_id),
        )
        self.cx.commit()

    # --------------------------------------------------------- correo crudo

    def guardar_correo(
        self,
        buzon_id: int,
        message_id: str,
        remitente: str,
        asunto: str,
        fecha_correo: str,
        cuerpo: str,
    ) -> tuple[int, bool]:
        """Devuelve (id, era_nuevo). El UNIQUE(buzon, message_id) es el dedupe."""
        fila = self.cx.execute(
            'SELECT id FROM correos_crudos WHERE buzon_id = ? AND message_id = ?',
            (buzon_id, message_id),
        ).fetchone()
        if fila:
            return fila['id'], False
        cur = self.cx.execute(
            """INSERT INTO correos_crudos
               (buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (buzon_id, message_id, remitente, asunto, fecha_correo, cuerpo),
        )
        self.cx.commit()
        return cur.lastrowid, True

    def correos_sin_procesar(self, limite: int = 500) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT c.*, b.usuario_id FROM correos_crudos c
               JOIN buzones b ON b.id = c.buzon_id
               WHERE c.procesado_en IS NULL
               ORDER BY c.fecha_correo LIMIT ?""",
            (limite,),
        ).fetchall()

    def contar_correos_sin_procesar(self) -> int:
        return self.cx.execute(
            'SELECT count(*) FROM correos_crudos WHERE procesado_en IS NULL'
        ).fetchone()[0]

    def marcar_correo_procesado(self, correo_id: int) -> None:
        self.cx.execute(
            "UPDATE correos_crudos SET procesado_en = datetime('now') WHERE id = ?",
            (correo_id,),
        )

    # ------------------------------------------------------------- la cola

    def crear_pendiente(self, **campos: Any) -> tuple[int, bool]:
        """Devuelve (id, era_nuevo).

        Si el external_id ya existe no inserta y devuelve el id que ya estaba:
        eso es la idempotencia, y es lo que hace seguro reintentar.
        """
        ext = campos.get('external_id')
        if ext:
            fila = self.cx.execute(
                'SELECT id FROM pendientes WHERE external_id = ?', (ext,)
            ).fetchone()
            if fila:
                return fila['id'], False
        # Un campo que no existe se DESCARTABA en silencio. Un nombre mal
        # escrito no guardaba el dato y nada lo decia: aparecia semanas despues
        # como una columna vacia sin explicacion.
        desconocidos = sorted(set(campos) - set(CAMPOS_PENDIENTE))
        if desconocidos:
            raise ValueError(
                f'crear_pendiente no conoce {desconocidos}. '
                f'Los campos validos son {CAMPOS_PENDIENTE}. '
                f'Los que se llenan despues (firefly_id, visto_en...) van por '
                f'actualizar_pendiente.'
            )
        usados = [c for c in CAMPOS_PENDIENTE if c in campos]
        marcas = ', '.join('?' for _ in usados)
        cur = self.cx.execute(
            f'INSERT INTO pendientes ({", ".join(usados)}) VALUES ({marcas})',
            [campos[c] for c in usados],
        )
        return cur.lastrowid, True

    def pendiente(self, pendiente_id: int) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM pendientes WHERE id = ?', (pendiente_id,)
        ).fetchone()

    def actualizar_pendiente(self, pendiente_id: int, **campos: Any) -> None:
        if not campos:
            return
        desconocidos = sorted(set(campos) - set(COLUMNAS_PENDIENTE))
        if desconocidos:
            raise ValueError(
                f'actualizar_pendiente no conoce {desconocidos}. '
                f'Validos: {COLUMNAS_PENDIENTE}'
            )
        sets = ', '.join(f'{k} = ?' for k in campos)
        self.cx.execute(
            f"UPDATE pendientes SET {sets}, actualizado_en = datetime('now') "
            f'WHERE id = ?',
            [*campos.values(), pendiente_id],
        )

    def pendientes_por_publicar(self, limite: int = 200) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT * FROM pendientes
               WHERE estado IN ('nuevo', 'error') AND cuenta_firefly IS NOT NULL
               ORDER BY fecha, id LIMIT ?""",
            (limite,),
        ).fetchall()

    def pendientes_por_preguntar(self, limite: int = 50) -> list[sqlite3.Row]:
        """Los que toca preguntar AHORA. La vista ya excluye los preguntados
        hace menos de un dia, para no repetir la misma pregunta cada pasada."""
        return self.cx.execute(
            'SELECT * FROM v_por_preguntar LIMIT ?', (limite,)
        ).fetchall()

    def pendientes_abiertos(self, limite: int = 50) -> list[sqlite3.Row]:
        """Todos los que tienen pregunta abierta, aunque se hayan preguntado
        hace un rato. Es la lista que se muestra cuando el USUARIO la pide."""
        return self.cx.execute('SELECT * FROM v_abiertos LIMIT ?', (limite,)).fetchall()

    def contar_por_preguntar(self) -> int:
        return self.cx.execute('SELECT count(*) FROM v_por_preguntar').fetchone()[0]

    def pendientes_abiertos_de_chat(self, chat_id: str) -> list[sqlite3.Row]:
        marcas = ', '.join('?' for _ in ESTADOS_ABIERTOS)
        return self.cx.execute(
            f"""SELECT p.* FROM pendientes p JOIN usuarios u ON u.id = p.usuario_id
                WHERE p.pregunta IS NOT NULL AND p.estado IN ({marcas})
                  AND u.telegram_chat_id = ?
                ORDER BY p.preguntado_en DESC, p.id DESC""",
            (*ESTADOS_ABIERTOS, str(chat_id)),
        ).fetchall()

    def marcar_preguntado(self, pendiente_id: int) -> None:
        self.cx.execute(
            "UPDATE pendientes SET preguntado_en = datetime('now') WHERE id = ?",
            (pendiente_id,),
        )
        self.cx.commit()

    def pendientes_del_instrumento(
        self,
        instrumento: str,
        desde: str,
        hasta: str,
        estado: str | None = None,
        moneda: str | None = None,
    ) -> list[sqlite3.Row]:
        """Lo que hay en la cola para una tarjeta en un periodo. Es lo que el
        conciliador cruza contra el extracto."""
        sql = [
            'SELECT * FROM pendientes WHERE instrumento = ?',
            'AND fecha BETWEEN ? AND ?',
        ]
        args: list[Any] = [instrumento, desde, hasta]
        if estado:
            sql.append('AND estado = ?')
            args.append(estado)
        if moneda:
            sql.append('AND moneda = ?')
            args.append(moneda)
        sql.append('ORDER BY fecha')
        return self.cx.execute(' '.join(sql), args).fetchall()

    def pendientes_por_estado(self, *estados: str) -> list[sqlite3.Row]:
        """Todo lo que esta en alguno de esos estados. Lo usa la reclasificacion
        para volver a pasar las reglas nuevas sobre lo que sigue abierto."""
        marcas = ', '.join('?' * len(estados))
        return self.cx.execute(
            f'SELECT * FROM pendientes WHERE estado IN ({marcas}) ORDER BY id', estados
        ).fetchall()

    def sin_confirmar(self, limite: int = 20) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT * FROM pendientes
               WHERE estado = 'publicado' AND visto_en IS NULL
               ORDER BY fecha DESC LIMIT ?""",
            (limite,),
        ).fetchall()

    def total_sin_confirmar(self) -> sqlite3.Row:
        return self.cx.execute(
            """SELECT count(*) n, sum(valor) t FROM pendientes
               WHERE estado = 'publicado' AND visto_en IS NULL"""
        ).fetchone()

    def contar_sospechosos(self) -> int:
        """Tarjetas publicadas hace mas de 45 dias que ningun extracto
        confirmo: los candidatos a fantasma."""
        return self.cx.execute('SELECT count(*) FROM v_sospechosos').fetchone()[0]

    def resumen(self, usuario_id: int | None = None) -> list[sqlite3.Row]:
        if usuario_id:
            return self.cx.execute(
                'SELECT * FROM v_sin_conciliar WHERE usuario_id = ?', (usuario_id,)
            ).fetchall()
        return self.cx.execute('SELECT * FROM v_sin_conciliar').fetchall()

    def contar_por_tabla(self, tabla: str) -> int:
        """Solo para el comando de estado. La tabla se valida contra una lista
        blanca: interpolar un nombre de tabla que venga de afuera seria una
        inyeccion."""
        permitidas = {
            'usuarios',
            'buzones',
            'correos_crudos',
            'pendientes',
            'reglas',
            'bitacora',
            'sugerencias',
            'propuestas',
            'preguntas_enviadas',
        }
        if tabla not in permitidas:
            raise ValueError(f'tabla no permitida: {tabla!r}')
        return self.cx.execute(f'SELECT count(*) FROM {tabla}').fetchone()[0]

    # ----------------------------------------------------- limpieza de cola

    def descartar_anteriores_a(self, marca: str) -> int:
        """Saca de la cola lo anterior a la marca de agua.

        La marca se aplica al crear el movimiento; esto arregla las filas que
        quedaron mal de una version anterior, donde se aplicaba al publicar y
        los movimientos sin cuenta resuelta nunca llegaban al filtro.
        """
        n = self.cx.execute(
            """UPDATE pendientes SET estado = 'descartado', pregunta = NULL,
               decidido_por = 'anterior_a_la_marca_de_agua'
               WHERE estado IN ('nuevo', 'error') AND fecha IS NOT NULL
                 AND fecha < ?""",
            (marca,),
        ).rowcount
        self.cx.commit()
        return n

    def descartar_sin_fecha(self) -> int:
        n = self.cx.execute(
            """UPDATE pendientes SET estado = 'descartado', pregunta = NULL,
               decidido_por = 'sin_fecha'
               WHERE estado IN ('nuevo', 'error') AND fecha IS NULL"""
        ).rowcount
        self.cx.commit()
        return n

    # -------------------------------------------------------------- reglas

    def reglas(
        self, usuario_id: int | None = None, solo_texto: bool = True
    ) -> list[sqlite3.Row]:
        sql = ['SELECT * FROM reglas WHERE 1=1']
        args: list[Any] = []
        if usuario_id is not None:
            sql.append('AND (usuario_id = ? OR usuario_id IS NULL)')
            args.append(usuario_id)
        if solo_texto:
            sql.append("AND es_regex = 0 AND patron <> ''")
        return self.cx.execute(' '.join(sql), args).fetchall()

    def guardar_regla(
        self,
        usuario_id: int | None,
        patron: str,
        cuenta_firefly: str | None = None,
        categoria: str | None = None,
        presupuesto: str | None = None,
        etiquetas: str | None = None,
        origen: str = 'usuario',
        direccion: str | None = None,
        aciertos: int | None = None,
    ) -> bool:
        """Guarda o actualiza una regla. Devuelve False si la rechazo.

        Rechaza los patrones que son SOLO el nombre de una pasarela de pago. El
        sembrador aprendio del historico la regla 'BOLD -> Inversion' con 9
        aciertos, y desde ahi toda compra hecha por Bold —que puede ser
        cualquier cosa— entraba como inversion con 0.88 de confianza, o sea sin
        preguntar. El guardian va aqui y no en los llamadores porque son tres y
        basta con que uno se olvide.
        """
        if texto.es_pasarela_pura(patron):
            return False
        self.cx.execute(
            """INSERT INTO reglas (usuario_id, patron, cuenta_firefly, categoria,
                  presupuesto, etiquetas, origen, direccion, aciertos)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0))
               ON CONFLICT (usuario_id, patron) DO UPDATE SET
                  cuenta_firefly = COALESCE(excluded.cuenta_firefly, cuenta_firefly),
                  categoria      = COALESCE(excluded.categoria, categoria),
                  presupuesto    = COALESCE(excluded.presupuesto, presupuesto),
                  etiquetas      = COALESCE(excluded.etiquetas, etiquetas),
                  direccion      = COALESCE(excluded.direccion, direccion),
                  aciertos       = MAX(reglas.aciertos, excluded.aciertos)""",
            (
                usuario_id,
                patron,
                cuenta_firefly,
                categoria,
                presupuesto,
                etiquetas,
                origen,
                direccion,
                aciertos,
            ),
        )
        self.cx.commit()
        return True

    def reglas_de_pasarela(self) -> list[sqlite3.Row]:
        """Las reglas ya guardadas cuyo patron es solo una pasarela. Existieron
        antes del guardian y hay que borrarlas: mientras esten, siguen
        clasificando mal todo lo que pase por esa pasarela."""
        return [
            r
            for r in self.cx.execute('SELECT * FROM reglas').fetchall()
            if texto.es_pasarela_pura(r['patron'])
        ]

    def borrar_regla(self, regla_id: int) -> None:
        self.cx.execute('DELETE FROM reglas WHERE id = ?', (regla_id,))
        self.cx.commit()

    def borrar_reglas(self) -> int:
        n = self.cx.execute('DELETE FROM reglas').rowcount
        self.cx.commit()
        return n

    def contar_reglas(self) -> int:
        return self.cx.execute('SELECT count(*) FROM reglas').fetchone()[0]

    def reglas_por_origen(self) -> list[sqlite3.Row]:
        return self.cx.execute(
            'SELECT origen, count(*) n FROM reglas GROUP BY origen'
        ).fetchall()

    def categorias_usadas(self, direccion: str) -> list[sqlite3.Row]:
        """Las categorias mas usadas en una direccion, para ofrecerlas primero.

        Filtrar por direccion es lo que evita ofrecerle 'Mecato' a una nomina
        que ENTRA.
        """
        return self.cx.execute(
            """SELECT categoria, count(*) n FROM reglas
               WHERE categoria IS NOT NULL AND categoria <> '' AND direccion = ?
               GROUP BY categoria ORDER BY n DESC""",
            (direccion,),
        ).fetchall()

    def reglas_con_categoria(self, usuario_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            """SELECT patron, categoria, presupuesto FROM reglas
               WHERE categoria IS NOT NULL AND categoria <> ''
                 AND (usuario_id = ? OR usuario_id IS NULL)""",
            (usuario_id,),
        ).fetchall()

    # --------------------------------- estado de la conversacion de Telegram

    def guardar_sugerencias(self, pendiente_id: int, opciones: list[str]) -> None:
        self.cx.execute(
            'INSERT OR REPLACE INTO sugerencias (pendiente_id, opciones) VALUES (?, ?)',
            (pendiente_id, '\n'.join(opciones)),
        )
        self.cx.commit()

    def sugerencias(self, pendiente_id: int) -> list[str]:
        r = self.cx.execute(
            'SELECT opciones FROM sugerencias WHERE pendiente_id = ?', (pendiente_id,)
        ).fetchone()
        return r['opciones'].split('\n') if r else []

    def guardar_propuesta(
        self,
        pendiente_id: int,
        categoria: str | None,
        presupuesto: str | None,
        comercio: str | None,
        pedir_presupuesto: bool,
    ) -> None:
        self.cx.execute(
            """INSERT OR REPLACE INTO propuestas
               (pendiente_id, categoria, presupuesto, comercio, pedir_presupuesto)
               VALUES (?, ?, ?, ?, ?)""",
            (
                pendiente_id,
                categoria,
                presupuesto,
                comercio,
                1 if pedir_presupuesto else 0,
            ),
        )
        self.cx.commit()

    def propuesta(self, pendiente_id: int) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM propuestas WHERE pendiente_id = ?', (pendiente_id,)
        ).fetchone()

    # ----------------------------------- categoria -> presupuesto, a mano

    def fijar_presupuesto_de_categoria(self, categoria: str, presupuesto: str) -> None:
        """La decision del usuario, que gana sobre lo que diga el historico."""
        self.cx.execute(
            """INSERT INTO presupuesto_por_categoria (categoria, presupuesto)
               VALUES (?, ?)
               ON CONFLICT (categoria) DO UPDATE SET
                  presupuesto = excluded.presupuesto,
                  puesto_en   = datetime('now')""",
            (categoria.strip(), presupuesto.strip()),
        )
        self.cx.commit()

    def presupuesto_fijado(self, categoria: str) -> str | None:
        if not categoria:
            return None
        r = self.cx.execute(
            'SELECT presupuesto FROM presupuesto_por_categoria WHERE categoria = ?',
            (categoria.strip(),),
        ).fetchone()
        return r['presupuesto'] if r else None

    def presupuestos_fijados(self) -> dict[str, str]:
        return {
            r['categoria']: r['presupuesto']
            for r in self.cx.execute(
                'SELECT categoria, presupuesto FROM presupuesto_por_categoria'
            )
        }

    def olvidar_presupuesto_de_categoria(self, categoria: str) -> None:
        self.cx.execute(
            'DELETE FROM presupuesto_por_categoria WHERE categoria = ?',
            (categoria.strip(),),
        )
        self.cx.commit()

    def usuario_por_chat(self, chat_id: str) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM usuarios WHERE telegram_chat_id = ?', (str(chat_id),)
        ).fetchone()

    # ------------------------------------------- edicion de un movimiento

    def abrir_edicion(
        self,
        chat_id: str,
        firefly_id: str,
        mensaje_id: int | None = None,
        campo: str | None = None,
    ) -> None:
        """Anota que este chat esta editando ese movimiento de Firefly.

        `campo` dice QUE se esta pidiendo. Sin eso, escribir «Ropa» despues de
        tocar «etiqueta» se interpretaba como una categoria.
        """
        self.cx.execute(
            """INSERT INTO edicion_en_curso
                  (chat_id, firefly_id, mensaje_id, campo)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (chat_id) DO UPDATE SET
                  firefly_id = excluded.firefly_id,
                  mensaje_id = excluded.mensaje_id,
                  campo      = excluded.campo,
                  creado_en  = datetime('now')""",
            (str(chat_id), str(firefly_id), mensaje_id, campo),
        )
        self.cx.commit()

    def edicion_en_curso(self, chat_id: str) -> sqlite3.Row | None:
        return self.cx.execute(
            'SELECT * FROM edicion_en_curso WHERE chat_id = ?', (str(chat_id),)
        ).fetchone()

    def cerrar_edicion(self, chat_id: str) -> None:
        self.cx.execute(
            'DELETE FROM edicion_en_curso WHERE chat_id = ?', (str(chat_id),)
        )
        self.cx.commit()

    def cerrar_preguntas_del_chat(self, chat_id: str, motivo: str) -> int:
        """Cierra TODAS las preguntas abiertas de un chat de una vez.

        Es lo que hace `/listo`. Sin esto, el bot volvia a preguntar cada 24h
        por cada movimiento abierto y no habia forma de decirle que ya estaba
        resuelto salvo contestar uno por uno.
        """
        cur = self.cx.execute(
            """UPDATE pendientes SET pregunta = NULL, decidido_por = ?,
                  actualizado_en = datetime('now')
               WHERE pregunta IS NOT NULL AND usuario_id IN (
                   SELECT id FROM usuarios WHERE telegram_chat_id = ?)""",
            (motivo, str(chat_id)),
        )
        self.cx.commit()
        return cur.rowcount

    def guardar_texto_en_espera(self, chat_id: str, txt: str, plan: Any = None) -> None:
        """El texto libre que el bot resolvio por su cuenta, para que siga vivo
        si el usuario toca «era otro».

        Con el PLAN que se le mostro, si habia: al confirmar se ejecuta ese y
        no se le vuelve a preguntar al modelo. Ver la nota en el esquema.
        """
        self.cx.execute(
            """INSERT INTO textos_en_espera (chat_id, texto, plan)
               VALUES (?, ?, ?)
               ON CONFLICT (chat_id) DO UPDATE SET
                  texto = excluded.texto, plan = excluded.plan,
                  creado_en = datetime('now')""",
            (str(chat_id), txt, _a_json(plan) if plan is not None else None),
        )
        self.cx.commit()

    def texto_en_espera(self, chat_id: str) -> str | None:
        r = self.cx.execute(
            'SELECT texto FROM textos_en_espera WHERE chat_id = ?', (str(chat_id),)
        ).fetchone()
        return r['texto'] if r else None

    def plan_en_espera(self, chat_id: str) -> dict[str, Any] | None:
        r = self.cx.execute(
            'SELECT plan FROM textos_en_espera WHERE chat_id = ?', (str(chat_id),)
        ).fetchone()
        if not r or not r['plan']:
            return None
        try:
            return json.loads(r['plan'])
        except (TypeError, ValueError):
            return None

    def olvidar_texto_en_espera(self, chat_id: str) -> None:
        self.cx.execute(
            'DELETE FROM textos_en_espera WHERE chat_id = ?', (str(chat_id),)
        )
        self.cx.commit()

    def guardar_mensaje(self, chat_id: str, mensaje_id: int, pendiente_id: int) -> None:
        self.cx.execute(
            """INSERT OR REPLACE INTO preguntas_enviadas
               (chat_id, mensaje_id, pendiente_id) VALUES (?, ?, ?)""",
            (str(chat_id), int(mensaje_id), int(pendiente_id)),
        )
        self.cx.commit()

    def pendiente_de_mensaje(self, chat_id: str, mensaje_id: int) -> int | None:
        r = self.cx.execute(
            """SELECT pendiente_id FROM preguntas_enviadas
               WHERE chat_id = ? AND mensaje_id = ?""",
            (str(chat_id), int(mensaje_id)),
        ).fetchone()
        return r['pendiente_id'] if r else None

    # ------------------------------------------------------------ bitacora

    def anotar(
        self,
        accion: str,
        usuario_id: int | None = None,
        pendiente_id: int | None = None,
        firefly_id: str | None = None,
        payload: Any = None,
        respuesta: Any = None,
        ok: bool = True,
    ) -> None:
        """Todo lo que se le escribe a Firefly queda registrado, para poder
        reconstruir que paso y deshacerlo."""
        self.cx.execute(
            """INSERT INTO bitacora (usuario_id, pendiente_id, accion, firefly_id,
                  payload, respuesta, ok) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                usuario_id,
                pendiente_id,
                accion,
                firefly_id,
                _a_json(payload),
                _a_json(respuesta),
                1 if ok else 0,
            ),
        )
        self.cx.commit()

    # ------------------------------------------------- facturas de mercado

    def guardar_factura_cruda(self, correo_id: int, archivo: str, xml: str) -> int:
        """El XML tal como venia. Devuelve el id; si ya estaba, el que habia."""
        fila = self.cx.execute(
            'SELECT id FROM facturas_crudas WHERE correo_id = ? AND archivo = ?',
            (correo_id, archivo),
        ).fetchone()
        if fila:
            return fila['id']
        cur = self.cx.execute(
            'INSERT INTO facturas_crudas (correo_id, archivo, xml) VALUES (?, ?, ?)',
            (correo_id, archivo, xml),
        )
        self.cx.commit()
        return int(cur.lastrowid)

    def facturas_sin_parsear(self, limite: int = 500) -> list:
        return self.cx.execute(
            'SELECT * FROM facturas_crudas WHERE parseado_en IS NULL '
            'ORDER BY id LIMIT ?',
            (limite,),
        ).fetchall()

    def marcar_factura_parseada(self, cruda_id: int) -> None:
        self.cx.execute(
            "UPDATE facturas_crudas SET parseado_en = datetime('now') WHERE id = ?",
            (cruda_id,),
        )
        self.cx.commit()

    def guardar_factura(self, cruda_id: int | None, f, lineas) -> bool:
        """Cabecera + lineas. Devuelve False si el CUFE ya estaba.

        El dedupe es por CUFE y no por Message-ID a proposito: la DIAN
        reenvia la misma factura y el correo repetido trae otro Message-ID.
        """
        if not f.cufe:
            return False
        if self.cx.execute(
            'SELECT 1 FROM facturas WHERE cufe = ?', (f.cufe,)
        ).fetchone():
            return False
        self.cx.execute(
            """INSERT INTO facturas (cufe, cruda_id, nit, proveedor, numero, tipo,
                  signo, fecha, hora, sede, moneda, subtotal, descuento, total,
                  medios_pago, puntos_redimidos, ahorro, pagada_con_puntos)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f.cufe,
                cruda_id,
                f.nit,
                f.proveedor,
                f.numero,
                f.tipo,
                f.signo,
                f.fecha,
                f.hora,
                f.sede,
                f.moneda,
                f.subtotal,
                f.descuento,
                f.total,
                '|'.join(f.medios_pago),
                f.puntos_redimidos,
                f.ahorro,
                1 if f.pagada_con_puntos else 0,
            ),
        )
        self.cx.executemany(
            """INSERT INTO factura_lineas (cufe, n, nit, codigo, descripcion,
                  cantidad, unidad, precio_unitario, descuento, iva_pct, total,
                  fecha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    f.cufe,
                    ln.n,
                    f.nit,
                    ln.codigo,
                    ln.descripcion,
                    ln.cantidad,
                    ln.unidad,
                    ln.precio_unitario,
                    ln.descuento,
                    ln.iva_pct,
                    ln.total,
                    f.fecha,
                )
                for ln in lineas
            ],
        )
        self.cx.commit()
        return True

    def catalogo_ver(self, nit: str, codigo: str):
        return self.cx.execute(
            'SELECT * FROM catalogo WHERE nit = ? AND codigo = ?', (nit, codigo)
        ).fetchone()

    def catalogo_upsert(
        self, nit, codigo, descripcion, tipo, grupo, categoria, origen
    ) -> None:
        """Guarda la clasificacion de un producto.

        Nunca pisa un `origen = 'usuario'`: esa es una respuesta tuya por
        Telegram, y una regla automatica no tiene por que ganarle. Sin esto,
        la siguiente pasada del clasificador borraba lo que acababas de
        contestar.
        """
        self.cx.execute(
            """INSERT INTO catalogo (nit, codigo, descripcion, tipo, grupo,
                  categoria, origen, veces, actualizado_en)
               VALUES (?,?,?,?,?,?,?,1,datetime('now'))
               ON CONFLICT (nit, codigo) DO UPDATE SET
                  descripcion = CASE
                      WHEN length(excluded.descripcion) > length(catalogo.descripcion)
                      THEN excluded.descripcion ELSE catalogo.descripcion END,
                  tipo       = CASE WHEN catalogo.origen = 'usuario'
                                    THEN catalogo.tipo ELSE excluded.tipo END,
                  grupo      = CASE WHEN catalogo.origen = 'usuario'
                                    THEN catalogo.grupo ELSE excluded.grupo END,
                  categoria  = CASE WHEN catalogo.origen = 'usuario'
                                    THEN catalogo.categoria ELSE excluded.categoria END,
                  origen     = CASE WHEN catalogo.origen = 'usuario'
                                    THEN 'usuario' ELSE excluded.origen END,
                  veces      = catalogo.veces + 1,
                  actualizado_en = datetime('now')""",
            (nit, codigo, descripcion, tipo, grupo, categoria, origen),
        )
        self.cx.commit()

    def catalogo_por_preguntar(self, limite: int = 5) -> list:
        return self.cx.execute(
            'SELECT * FROM v_catalogo_por_preguntar LIMIT ?', (limite,)
        ).fetchall()

    def catalogo_marcar_preguntado(self, nit: str, codigo: str) -> None:
        self.cx.execute(
            "UPDATE catalogo SET preguntado_en = datetime('now') "
            'WHERE nit = ? AND codigo = ?',
            (nit, codigo),
        )
        self.cx.commit()

    def facturas_sin_sincronizar(self, limite: int = 500) -> list:
        return self.cx.execute(
            'SELECT * FROM facturas WHERE sincronizado_en IS NULL '
            'ORDER BY fecha LIMIT ?',
            (limite,),
        ).fetchall()

    def resumen_facturas(self) -> dict:
        f = self.cx.execute(
            'SELECT COUNT(*) n, COALESCE(SUM(total * signo), 0) total, '
            'MIN(fecha) desde, MAX(fecha) hasta FROM facturas'
        ).fetchone()
        c = self.cx.execute(
            "SELECT COUNT(*) n, SUM(grupo = 'Sin clasificar') sin FROM catalogo"
        ).fetchone()
        return {
            'facturas': f['n'],
            'total': f['total'],
            'desde': f['desde'],
            'hasta': f['hasta'],
            'productos': c['n'],
            'sin_clasificar': c['sin'] or 0,
        }

    def productos_de_lineas(self) -> list:
        """Cada (nit, codigo) que aparece en las lineas, con su mejor nombre.

        La descripcion es la MAS LARGA vista y no `MAX()`: los almacenes
        truncan el nombre y el corte varia entre facturas. `MAX()` da el
        maximo lexicografico — cualquiera —, y con un nombre mas corto se
        pierden justo las palabras que la regla necesita para clasificar.
        """
        return self.cx.execute(
            """SELECT l.nit, l.codigo,
                      (SELECT d.descripcion FROM factura_lineas d
                        WHERE d.nit = l.nit AND d.codigo = l.codigo
                        ORDER BY length(d.descripcion) DESC LIMIT 1) AS descripcion,
                      MAX(l.iva_pct) AS iva
               FROM factura_lineas l
               GROUP BY l.nit, l.codigo"""
        ).fetchall()

    def catalogo_responder(
        self, nit: str, codigo: str, tipo: str, grupo: str, categoria: str
    ) -> None:
        """La respuesta del usuario por Telegram. Queda con origen 'usuario',
        que ninguna regla automatica vuelve a pisar."""
        self.cx.execute(
            """INSERT INTO catalogo (nit, codigo, descripcion, tipo, grupo,
                  categoria, origen, actualizado_en)
               VALUES (?, ?,
                       (SELECT descripcion FROM catalogo
                         WHERE nit = ? AND codigo = ?),
                       ?, ?, ?, 'usuario', datetime('now'))
               ON CONFLICT (nit, codigo) DO UPDATE SET
                  tipo = excluded.tipo, grupo = excluded.grupo,
                  categoria = excluded.categoria, origen = 'usuario',
                  actualizado_en = datetime('now')""",
            (nit, codigo, nit, codigo, tipo, grupo, categoria),
        )
        self.cx.commit()

    def facturas_todas(self) -> list:
        return self.cx.execute('SELECT * FROM facturas ORDER BY fecha').fetchall()

    def lineas_todas(self) -> list:
        return self.cx.execute(
            """SELECT l.*, f.signo FROM factura_lineas l
               JOIN facturas f ON f.cufe = l.cufe ORDER BY l.cufe, l.n"""
        ).fetchall()

    def catalogo_todo(self) -> list:
        return self.cx.execute('SELECT * FROM catalogo ORDER BY nit, codigo').fetchall()

    def primer_usuario(self) -> int | None:
        fila = self.cx.execute('SELECT id FROM usuarios ORDER BY id LIMIT 1').fetchone()
        return fila['id'] if fila else None

    def catalogo_por_id(self, cat_id: int):
        """Un producto del catalogo por su rowid, que es lo que viaja en el
        callback de Telegram."""
        return self.cx.execute(
            'SELECT rowid AS id, * FROM catalogo WHERE rowid = ?', (cat_id,)
        ).fetchone()

    def catalogo_responder_id(
        self, cat_id: int, tipo: str, grupo: str, categoria: str
    ) -> None:
        self.cx.execute(
            """UPDATE catalogo
                  SET tipo = ?, grupo = ?, categoria = ?, origen = 'usuario',
                      actualizado_en = datetime('now')
                WHERE rowid = ?""",
            (tipo, grupo, categoria, cat_id),
        )
        self.cx.commit()

    def guardar_mensaje_producto(
        self, chat_id: str, mensaje_id: int, catalogo_id: int
    ) -> None:
        """Ata la pregunta de un producto a su mensaje de Telegram.

        Sin esto, contestar por escrito a «¿que es esto? fletes gravado» no
        tenia a donde llegar y el mensaje acababa en el camino de editar
        transacciones.
        """
        self.cx.execute(
            """INSERT OR REPLACE INTO preguntas_producto
                  (chat_id, mensaje_id, catalogo_id) VALUES (?, ?, ?)""",
            (str(chat_id), int(mensaje_id), int(catalogo_id)),
        )
        self.cx.commit()

    def producto_de_mensaje(self, chat_id: str, mensaje_id: int) -> int | None:
        r = self.cx.execute(
            """SELECT catalogo_id FROM preguntas_producto
               WHERE chat_id = ? AND mensaje_id = ?""",
            (str(chat_id), int(mensaje_id)),
        ).fetchone()
        return r['catalogo_id'] if r else None

    def productos_preguntados(self, chat_id: str, limite: int = 5) -> list:
        """Los productos que se le preguntaron a este chat y siguen abiertos.

        Es lo que permite que el modelo sepa que «fletes gravado» esta sobre la
        mesa y no confunda la respuesta con una orden sobre una transaccion.
        """
        return self.cx.execute(
            """SELECT c.rowid AS id, c.nit, c.codigo, c.descripcion,
                      c.grupo, c.categoria
                 FROM preguntas_producto q
                 JOIN catalogo c ON c.rowid = q.catalogo_id
                WHERE q.chat_id = ?
                  -- 'Sin clasificar' y no NULL: es la convencion del catalogo,
                  -- la misma que usa v_catalogo_por_preguntar.
                  AND (c.grupo = 'Sin clasificar' OR c.grupo IS NULL)
                ORDER BY q.mensaje_id DESC LIMIT ?""",
            (str(chat_id), limite),
        ).fetchall()

    def catalogo_marcar_preguntado_id(self, cat_id: int) -> None:
        self.cx.execute(
            "UPDATE catalogo SET preguntado_en = datetime('now') WHERE rowid = ?",
            (cat_id,),
        )
        self.cx.commit()

    def catalogo_sin_clasificar(self, limite: int = 20) -> list:
        """Todo lo que falta por clasificar, por plata. Ignora `preguntado_en`:
        es para el comando /productos, que se pide a proposito."""
        return self.cx.execute(
            """SELECT c.rowid AS id, c.*,
                      COALESCE(g.gasto, 0)   AS gasto,
                      COALESCE(g.compras, 0) AS compras
               FROM catalogo c
               LEFT JOIN (SELECT nit, codigo, SUM(total) AS gasto,
                                 COUNT(*) AS compras
                          FROM factura_lineas GROUP BY nit, codigo) g
                 ON g.nit = c.nit AND g.codigo = c.codigo
               WHERE c.grupo = 'Sin clasificar'
               ORDER BY COALESCE(g.gasto, 0) DESC
               LIMIT ?""",
            (limite,),
        ).fetchall()
