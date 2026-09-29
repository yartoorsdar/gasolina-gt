-- Esquema D1 del sistema de votos. Idempotente (se puede volver a ejecutar).
-- Mismas columnas que la tabla `votos` de collector/db.py (más usuario_id, vacío por ahora:
-- reservado para un login opcional futuro, sin migración).

CREATE TABLE IF NOT EXISTS votos (
    id_voto TEXT PRIMARY KEY,            -- dia|producto|modalidad|dispositivo: hace atomico "1 voto por dia"
    fecha TEXT NOT NULL,                 -- dia GT
    producto TEXT NOT NULL CHECK (producto IN ('superior', 'regular', 'diésel')),
    modalidad TEXT NOT NULL DEFAULT 'autoservicio',
    tipo TEXT NOT NULL CHECK (tipo IN ('coincide', 'otro')),
    precio REAL,                         -- precio escrito (NULL si coincide)
    precio_mostrado REAL,                -- oficial que la persona veia (NULL si voto ciego o sin dato)
    vio_oficial INTEGER NOT NULL DEFAULT 1 CHECK (vio_oficial IN (0, 1)),
    dispositivo TEXT NOT NULL,           -- hash anonimo con sal secreta
    zona TEXT,
    usuario_id TEXT,                     -- reservado para login opcional
    ts TEXT NOT NULL,                    -- hora GT con offset
    CHECK ((tipo = 'otro' AND precio IS NOT NULL) OR (tipo = 'coincide' AND precio IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_votos_dia ON votos (fecha, producto, modalidad);

-- Contador por hora y por IP hasheada, solo para frenar abuso (se limpia solo).
CREATE TABLE IF NOT EXISTS limites (
    clave TEXT NOT NULL,
    ventana TEXT NOT NULL,
    n INTEGER NOT NULL,
    PRIMARY KEY (clave, ventana)
);
