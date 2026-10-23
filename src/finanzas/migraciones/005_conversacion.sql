-- 005 · Contestar hablando: la IA lee primero, pregunta de vuelta y aprende.
--
-- Antes, contestar por escrito era emparejar palabras: «gata» -> Gato. Lo que
-- no estaba en la lista no se entendia, y el bot no podia preguntar nada de
-- vuelta: o adivinaba o mostraba botones. Ahora el modelo lee la respuesta y
-- decide entre proponer, preguntar o descartar, y nada se escribe en un libro
-- sin que la persona confirme con un toque.

-- El hilo de cada movimiento: lo que dijo la persona y lo que pregunto el bot.
-- Va en la base y no en memoria porque una conversacion sobre un movimiento
-- puede durar dias («¿quien es 3137570780?» ... «la arrendadora»), y el
-- proceso se reinicia en cada despliegue.
CREATE TABLE IF NOT EXISTS dialogos (
  id           INTEGER PRIMARY KEY,
  pendiente_id INTEGER NOT NULL REFERENCES pendientes(id) ON DELETE CASCADE,
  rol          TEXT NOT NULL CHECK (rol IN ('persona', 'bot')),
  texto        TEXT NOT NULL,
  creado_en    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_dialogos_pendiente ON dialogos (pendiente_id, id);

-- Lo que se entendio y se le mostro, esperando el toque. Se aplica ESTO y no
-- se le vuelve a preguntar al modelo: preguntando otra vez, lo que se aplica
-- podria no ser lo que se confirmo (pasó con los planes; ver textos_en_espera).
CREATE TABLE IF NOT EXISTS lecturas (
  pendiente_id INTEGER PRIMARY KEY REFERENCES pendientes(id) ON DELETE CASCADE,
  lectura      TEXT NOT NULL,
  creada_en    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Lo que la persona conto de una contraparte y vale para la proxima vez:
-- «3137570780 es la cuenta de la arrendadora». Una regla dice la categoria;
-- esto dice QUIEN es, que es lo que hace falta para entender la siguiente.
CREATE TABLE IF NOT EXISTS notas (
  usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  clave      TEXT NOT NULL,
  nota       TEXT NOT NULL,
  puesta_en  TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (usuario_id, clave)
);
