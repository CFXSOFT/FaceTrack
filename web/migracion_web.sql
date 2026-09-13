-- ============================================================
-- Migración adicional para adaptar la BD existente a la web
-- Ejecutar UNA VEZ después de actualizar_bd.sql
-- ============================================================

-- Color identificativo único por persona (hex, ej: "#c6ff2e")
ALTER TABLE personal ADD COLUMN color TEXT;

-- PDF del horario personal (URL pública en Supabase Storage)
ALTER TABLE personal ADD COLUMN horario_path TEXT;

-- Contacto opcional
ALTER TABLE personal ADD COLUMN correo TEXT;
ALTER TABLE personal ADD COLUMN telefono TEXT;

-- Solicitudes de justificación (el maestro solicita, el director aprueba/rechaza)
CREATE TABLE IF NOT EXISTS justificaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    registro_id INTEGER NOT NULL,
    personal_id INTEGER NOT NULL,
    motivo TEXT,
    estado TEXT NOT NULL DEFAULT 'pendiente' CHECK(estado IN ('pendiente', 'aprobado', 'rechazado')),
    fecha_solicitud DATETIME DEFAULT CURRENT_TIMESTAMP,
    fecha_resolucion DATETIME,
    FOREIGN KEY (registro_id) REFERENCES registros_asistencia(id),
    FOREIGN KEY (personal_id) REFERENCES personal(id)
);

-- Configuración del panel admin (contraseña del director, hash)
CREATE TABLE IF NOT EXISTS admin_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    ultimo_backup DATETIME
);

-- Nota: la fila con id=1 y la contraseña inicial ("senati2026") se crean
-- automáticamente la primera vez que arranca app.py (ver init_admin_config()).
-- Cámbiala desde Modo Admin -> Configuración apenas entres.

-- Marcaje ligado a bloques de horario (ejecutar en Supabase)
-- ALTER TABLE registros_asistencia ADD COLUMN IF NOT EXISTS es_tardanza BOOLEAN DEFAULT FALSE;
-- ALTER TABLE registros_asistencia ADD COLUMN IF NOT EXISTS bloque_id TEXT;
-- ALTER TABLE registros_asistencia ADD COLUMN IF NOT EXISTS materia TEXT;
