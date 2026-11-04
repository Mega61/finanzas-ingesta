-- 007 · Los sospechosos de un cargo que no se entiende.
--
-- «Compraste 78.000 en RAPYD*MC»: la pasarela no dice quien vendio, pero el
-- buzon si (la confirmacion de Comic Con llego tres segundos despues). El bot
-- ofrece esos comercios como botones, y como el callback de Telegram solo
-- aguanta 64 bytes viaja el INDICE: aqui queda la lista para resolverlo.
-- Es aparte de `sugerencias` porque aquella es de categorias y se reescribe
-- en cada pregunta.
CREATE TABLE IF NOT EXISTS sospechosos (
  pendiente_id INTEGER PRIMARY KEY REFERENCES pendientes(id) ON DELETE CASCADE,
  opciones     TEXT NOT NULL          -- JSON: [{"comercio", "motivo"}, ...]
);
