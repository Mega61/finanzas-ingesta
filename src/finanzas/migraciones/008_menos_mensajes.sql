-- 008 · El bot deja de insistir.
--
-- Una pregunta sin contestar se repetia cada 24 horas, para siempre, seis
-- por pasada cada 15 minutos. Con una semana sin contestar eran 90 mensajes
-- de un tiron, y entre tantos los importantes (una venta, un fantasma) se
-- perdian. Ahora cada pregunta sale a lo sumo tres veces: al llegar, al dia
-- siguiente y tres dias despues. Despues se queda callada en /pendientes.
ALTER TABLE pendientes ADD COLUMN veces_preguntado INTEGER NOT NULL DEFAULT 0;
UPDATE pendientes SET veces_preguntado = 1 WHERE preguntado_en IS NOT NULL;

-- La vista vive aqui y no en esquema.sql: depende de la columna de arriba,
-- que la version 0 no tiene.
DROP VIEW IF EXISTS v_por_preguntar;
CREATE VIEW v_por_preguntar AS
SELECT p.*, u.telegram_chat_id, u.nombre AS usuario
FROM pendientes p JOIN usuarios u ON u.id = p.usuario_id
WHERE p.pregunta IS NOT NULL
  AND p.estado IN ('nuevo', 'publicado', 'error')
  AND u.activo = 1
  AND p.veces_preguntado < 3
  AND (p.preguntado_en IS NULL
       OR julianday('now') - julianday(p.preguntado_en)
          > CASE WHEN p.veces_preguntado <= 1 THEN 1.0 ELSE 3.0 END)
ORDER BY p.fecha DESC, p.id DESC;

-- La de productos se redefine en esquema.sql (sin bolsas ni centavos); se
-- borra para que la recree la segunda pasada del esquema base.
DROP VIEW IF EXISTS v_catalogo_por_preguntar;

-- Las bolsas que ya estaban en la cola: la regla nueva solo mira lo que
-- entra, y estas se seguirian preguntando.
UPDATE catalogo
   SET tipo = 'Consumible', grupo = 'Servicios',
       categoria = 'Bolsas y empaques', origen = 'palabra',
       actualizado_en = datetime('now')
 WHERE grupo = 'Sin clasificar'
   AND (lower(descripcion) LIKE 'bolsa%' OR lower(descripcion) LIKE '% bolsa%')
   AND lower(descripcion) NOT LIKE '%basur%';
