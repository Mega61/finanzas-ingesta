-- Esquema de la automatizacion de ingesta. SQLite (sirve igual en Postgres
-- cambiando INTEGER PRIMARY KEY por SERIAL y CURRENT_TIMESTAMP por now()).
--
-- Principio: los correos crudos NUNCA se borran. Cuando el parser mejore, se
-- reprocesa todo desde aqui sin volver a tocar el buzon.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------- usuarios

CREATE TABLE IF NOT EXISTS usuarios (
  id                INTEGER PRIMARY KEY,
  nombre            TEXT    NOT NULL,
  telegram_chat_id  TEXT    UNIQUE,          -- se llena en el primer /start
  firefly_url       TEXT    NOT NULL,
  firefly_token_enc TEXT    NOT NULL,        -- cifrado con la llave del .env
  zona_horaria      TEXT    NOT NULL DEFAULT 'America/Bogota',
  activo            INTEGER NOT NULL DEFAULT 1,
  creado_en         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- Un usuario puede tener varios buzones (hotmail personal + gmail, por ejemplo).
CREATE TABLE IF NOT EXISTS buzones (
  id            INTEGER PRIMARY KEY,
  usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  proveedor     TEXT    NOT NULL CHECK (proveedor IN ('graph', 'imap')),
  direccion     TEXT    NOT NULL,
  secreto_enc   TEXT,        -- graph: refresh token / imap: app password. Cifrado.
  imap_host     TEXT,        -- solo imap. gmail: imap.gmail.com:993
  delta_link    TEXT,        -- solo graph: el deltaLink para traer solo lo nuevo
  ultimo_sync   TEXT,
  ultimo_error  TEXT,        -- si el token se murio, aqui queda el motivo
  activo        INTEGER NOT NULL DEFAULT 1,
  UNIQUE (usuario_id, direccion)
);


-- ------------------------------------------------------------ correo crudo

CREATE TABLE IF NOT EXISTS correos_crudos (
  id           INTEGER PRIMARY KEY,
  buzon_id     INTEGER NOT NULL REFERENCES buzones(id) ON DELETE CASCADE,
  message_id   TEXT    NOT NULL,   -- el Message-ID de la cabecera: la clave de dedupe
  remitente    TEXT,
  asunto       TEXT,
  fecha_correo TEXT,
  cuerpo       TEXT    NOT NULL,   -- el text/plain ya extraido
  bajado_en    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  procesado_en TEXT,               -- NULL = el parser todavia no lo miro
  UNIQUE (buzon_id, message_id)
);

CREATE INDEX IF NOT EXISTS ix_correos_pendientes
  ON correos_crudos (procesado_en) WHERE procesado_en IS NULL;
CREATE INDEX IF NOT EXISTS ix_correos_fecha ON correos_crudos (fecha_correo);


-- ------------------------------------------------------------------ la cola
-- El corazon del sistema. Todo movimiento vive aqui con un estado y solo
-- pasa a Firefly cuando le toca.

CREATE TABLE IF NOT EXISTS pendientes (
  id            INTEGER PRIMARY KEY,
  correo_id     INTEGER NOT NULL REFERENCES correos_crudos(id) ON DELETE CASCADE,
  usuario_id    INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,

  -- lo que dijo el parser
  tipo          TEXT    NOT NULL,   -- compra_tarjeta | pago_qr | avance | ...
  fecha         TEXT,
  hora          TEXT,
  moneda        TEXT    NOT NULL DEFAULT 'COP',
  valor         REAL    NOT NULL,   -- negativo = sale plata
  instrumento       TEXT,           -- ultimos 4 digitos
  clase_instrumento TEXT CHECK (clase_instrumento IN ('tarjeta', 'cuenta')),
  traslado_a    TEXT,               -- si mueve plata entre productos propios
  contraparte   TEXT,
  descripcion   TEXT,
  plantilla     TEXT,               -- que familia del parser lo cazo

  -- lo que decidio el clasificador (o el usuario por Telegram)
  cuenta_firefly TEXT,              -- resuelta con dim_producto SEGUN LA FECHA
  cuenta_destino TEXT,
  categoria      TEXT,
  presupuesto    TEXT,
  etiquetas      TEXT,
  confianza      REAL,              -- 0..1; bajo el umbral se pregunta
  decidido_por   TEXT,              -- regla | historico | usuario

  -- estado. Politica: TODO entra a Firefly de una, y se confirma despues
  -- hablando con el bot. Nada espera al extracto.
  estado        TEXT    NOT NULL DEFAULT 'nuevo' CHECK (estado IN (
                  'nuevo',        -- el parser lo saco, todavia no esta en Firefly
                  'publicado',    -- ya esta en Firefly, con la etiqueta sin-confirmar
                  'confirmado',   -- confirmado (por el bot o por el extracto)
                  'corregido',    -- el extracto trajo otro monto y se ajusto
                  'fantasma',     -- nunca ocurrio: se borro de Firefly
                  'descartado',   -- el usuario dijo que no era un movimiento
                  'error')),      -- fallo al publicar, hay que reintentar

  -- Que le falta preguntarle al usuario. NULL = nada, esta cerrado.
  -- Un movimiento puede estar 'publicado' y con pregunta pendiente al mismo
  -- tiempo: por eso esto es una columna aparte y no un estado.
  pregunta      TEXT    CHECK (pregunta IN ('categoria', 'existencia', 'monto')),

  -- idempotencia: el hash del Message-ID va al external_id de Firefly, asi
  -- reintentar mil veces nunca duplica.
  external_id   TEXT    UNIQUE,
  firefly_id    TEXT,

  -- reconciliacion contra extracto
  visto_en          TEXT,   -- archivo del extracto que lo confirmo
  valor_confirmado  REAL,   -- el monto real si difiere del de la alerta

  preguntado_en TEXT,
  creado_en     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  actualizado_en TEXT
);

CREATE INDEX IF NOT EXISTS ix_pend_estado  ON pendientes (usuario_id, estado);
CREATE INDEX IF NOT EXISTS ix_pend_fecha   ON pendientes (fecha);
CREATE INDEX IF NOT EXISTS ix_pend_match   ON pendientes (instrumento, fecha, valor);


-- --------------------------------------------------------------- aprendizaje
-- comercio -> como se clasifica. Se siembra con las 1.348 filas de Firefly ya
-- clasificadas a mano y crece con cada respuesta de Telegram.

CREATE TABLE IF NOT EXISTS reglas (
  id             INTEGER PRIMARY KEY,
  usuario_id     INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
  patron         TEXT    NOT NULL,   -- se compara normalizado, sin tildes, en mayusculas
  es_regex       INTEGER NOT NULL DEFAULT 0,
  cuenta_firefly TEXT,
  categoria      TEXT,
  presupuesto    TEXT,
  etiquetas      TEXT,
  -- Gasto o ingreso. Sin esto, a una nomina que ENTRA se le ofrecian
  -- categorias de gasto como 'Mecato' o 'Salidas'.
  direccion      TEXT CHECK (direccion IN ('gasto', 'ingreso')),
  -- De donde salio el patron. Es la diferencia entre 'Grupo Super' (nombre de
  -- comercio, sirve para emparejar por palabras) y 'ALMUERZO SUPER' (nota
  -- escrita a mano, solo sirve para coincidencia exacta).
  origen         TEXT    NOT NULL DEFAULT 'manual'
                 CHECK (origen IN ('comercio', 'descripcion', 'usuario', 'manual')),
  aciertos       INTEGER NOT NULL DEFAULT 0,
  fallos         INTEGER NOT NULL DEFAULT 0,
  creada_en      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (usuario_id, patron)
);

CREATE INDEX IF NOT EXISTS ix_reglas_patron ON reglas (usuario_id, patron);


-- ----------------------------------------------------------------- bitacora
-- Todo lo que se le escribe a Firefly queda registrado, para poder deshacer.
-- Los scripts de reconciliacion/api/ ya usan este patron con sus log_*.json.

CREATE TABLE IF NOT EXISTS bitacora (
  id          INTEGER PRIMARY KEY,
  usuario_id  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
  pendiente_id INTEGER REFERENCES pendientes(id) ON DELETE SET NULL,
  accion      TEXT NOT NULL,   -- crear | actualizar | borrar
  firefly_id  TEXT,
  payload     TEXT,            -- el JSON que se envio
  respuesta   TEXT,
  ok          INTEGER NOT NULL DEFAULT 1,
  cuando      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- ------------------------------------------------ estado de la conversacion
-- Estas tres tablas las creaba bot.py a mano, en tiempo de ejecucion, con
-- CREATE TABLE IF NOT EXISTS dentro de una funcion. Eso es fragil: el esquema
-- dejaba de ser la fuente de verdad y no habia forma de saber que existian sin
-- leer el codigo del bot.

-- Las opciones que se ofrecieron en los botones de una pregunta. Hacen falta
-- porque el callback de Telegram solo aguanta 64 bytes: viaja el INDICE de la
-- opcion, no su texto, y hay que poder resolverlo despues.
CREATE TABLE IF NOT EXISTS sugerencias (
  pendiente_id INTEGER PRIMARY KEY REFERENCES pendientes(id) ON DELETE CASCADE,
  opciones     TEXT NOT NULL
);

-- Lo que se entendio de una respuesta en texto libre, esperando que el usuario
-- la confirme. No se aplica sola: una interpretacion equivocada aplicada en
-- silencio es peor que un mensaje mas.
CREATE TABLE IF NOT EXISTS propuestas (
  pendiente_id      INTEGER PRIMARY KEY REFERENCES pendientes(id) ON DELETE CASCADE,
  categoria         TEXT,
  presupuesto       TEXT,
  comercio          TEXT,
  pedir_presupuesto INTEGER NOT NULL DEFAULT 0
);

-- Que mensaje de Telegram corresponde a que movimiento. Sin esto, contestar
-- por texto resolvia la pregunta MAS RECIENTE en vez de la que se estaba
-- contestando: con seis abiertas, responder la tercera resolvia la sexta.
CREATE TABLE IF NOT EXISTS preguntas_enviadas (
  chat_id      TEXT    NOT NULL,
  mensaje_id   INTEGER NOT NULL,
  pendiente_id INTEGER NOT NULL REFERENCES pendientes(id) ON DELETE CASCADE,
  PRIMARY KEY (chat_id, mensaje_id)
);

CREATE INDEX IF NOT EXISTS ix_preguntas_pendiente
  ON preguntas_enviadas (pendiente_id);


-- El ultimo texto libre de cada chat que el bot resolvio por su cuenta.
--
-- Cuando escribes algo que no senala a ningun movimiento en particular, el bot
-- lo aplica al ultimo que te pregunto y te ofrece botones para moverlo a otro.
-- El texto tiene que sobrevivir hasta que toques el boton, y no cabe en el
-- callback: Telegram admite 64 bytes ahi.
--
-- Es uno por chat a proposito: solo importa el ultimo.
CREATE TABLE IF NOT EXISTS textos_en_espera (
  chat_id   TEXT PRIMARY KEY,
  texto     TEXT NOT NULL,
  creado_en TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ------------------------------------------------------------------- vistas

-- Lo que el bot tiene que preguntar ahora mismo. Incluye cosas ya publicadas
-- en Firefly: estan en el libro, pero sin confirmar.
-- Ojo con `preguntado_en`: sin ese filtro el servicio repetia la misma
-- pregunta en cada pasada, o sea cada 15 minutos hasta que contestaras.
-- Se vuelve a preguntar solo despues de 24h, como recordatorio.
CREATE VIEW IF NOT EXISTS v_por_preguntar AS
SELECT p.*, u.telegram_chat_id, u.nombre AS usuario
FROM pendientes p JOIN usuarios u ON u.id = p.usuario_id
WHERE p.pregunta IS NOT NULL
  AND p.estado IN ('nuevo', 'publicado', 'error')
  AND u.activo = 1
  AND (p.preguntado_en IS NULL
       OR julianday('now') - julianday(p.preguntado_en) > 1.0)
ORDER BY p.fecha DESC, p.id DESC;

-- El resumen diario: todo lo que sigue abierto.
CREATE VIEW IF NOT EXISTS v_sin_conciliar AS
SELECT usuario_id,
       estado,
       COALESCE(pregunta, 'nada') AS pregunta,
       COUNT(*)   AS n,
       SUM(valor) AS total,
       MIN(fecha) AS mas_viejo
FROM pendientes
WHERE estado IN ('nuevo', 'publicado', 'error') OR pregunta IS NOT NULL
GROUP BY usuario_id, estado, COALESCE(pregunta, 'nada');

-- Candidatos a fantasma: preautorizaciones de tarjeta que llevan mucho tiempo
-- publicadas sin que ningun extracto las confirme. El bot propone borrarlas.
CREATE VIEW IF NOT EXISTS v_sospechosos AS
SELECT p.*, u.telegram_chat_id
FROM pendientes p JOIN usuarios u ON u.id = p.usuario_id
WHERE p.estado = 'publicado'
  AND p.clase_instrumento = 'tarjeta'
  AND p.visto_en IS NULL
  AND julianday('now') - julianday(p.fecha) > 45
ORDER BY p.fecha;
