-- ============================================================
-- Panel de Desarrollador — tablas nuevas (correr una vez en el
-- SQL Editor de Supabase)
-- ============================================================

CREATE TABLE IF NOT EXISTS pendientes_dev (
    id          bigint generated always as identity primary key,
    texto       text not null,
    hecho       boolean not null default false,
    personal_id uuid references personal(id) on delete set null,
    creado_en   timestamptz not null default now()
);

CREATE TABLE IF NOT EXISTS notas_personal (
    id          bigint generated always as identity primary key,
    personal_id uuid not null references personal(id) on delete cascade,
    texto       text not null,
    creado_en   timestamptz not null default now()
);

-- Igual que el resto de tus tablas: RLS activado, sin políticas públicas --
-- solo se accede vía la conexión directa a Postgres (DATABASE_URL), que
-- tiene el mismo nivel de acceso que la service_role key y no pasa por RLS.
ALTER TABLE pendientes_dev ENABLE ROW LEVEL SECURITY;
ALTER TABLE notas_personal ENABLE ROW LEVEL SECURITY;
