-- 006 · El plan de caja vive en la base, y se cambia hablando con el bot.
--
-- Antes era un archivo o una variable PLAN_JSON en Portainer: para cambiar
-- la fecha de pago de una tarjeta habia que editar un JSON de una linea,
-- pegarlo en la UI del servidor y redesplegar. Ahora cada version del plan
-- queda aqui; la vigente es la ultima confirmada. Las versiones viejas no se
-- borran: dicen que cambio y cuando.
CREATE TABLE IF NOT EXISTS planes (
  id          INTEGER PRIMARY KEY,
  usuario_id  INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
  datos       TEXT NOT NULL,          -- el plan, en JSON
  motivo      TEXT,                   -- lo que dijo la persona, o «importado»
  confirmado  INTEGER NOT NULL DEFAULT 0,
  creado_en   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_planes_usuario ON planes (usuario_id, confirmado, id);
