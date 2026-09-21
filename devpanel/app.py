"""
FaceTrack — Panel de Desarrollador
===================================
App Flask SEPARADA del sistema de reconocimiento (no usa DeepFace, no
necesita cámara). Pensada para correr 100% en Vercel, sin modo offline --
si no hay internet en el momento, simplemente se revisa más tarde; no es un
punto único de falla como sí lo es el reconocimiento.

Dos partes:

1. PANEL DE DATOS (genérico): en vez de tener escritas a mano las columnas
   de cada tabla (lo que nos ha hecho perder tiempo cada vez que el esquema
   cambia y yo no lo sabía), esta app se conecta directo a Postgres y
   DESCUBRE solas qué tablas y columnas existen ahora mismo, vía
   information_schema. Funciona con cualquier tabla nueva que agregues después,
   sin tocar este código.

2. PENDIENTES / NOTAS / DASHBOARD: sí son tablas nuevas, definidas en
   migracion_devpanel.sql -- ver ese archivo, hay que correrlo una vez.

Seguridad: usa la cadena de conexión DIRECTA a Postgres (no el cliente
supabase-py/PostgREST) porque necesita poder leer information_schema y
armar SQL dinámico validado -- ver DATABASE_URL más abajo. Esa conexión
tiene el mismo nivel de acceso que la service_role key: NUNCA debe
exponerse, solo vive en variables de entorno.
"""
import os
import re
from datetime import timedelta, datetime
from functools import wraps

import psycopg2
import psycopg2.extras
from psycopg2 import sql
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session, render_template
from werkzeug.security import check_password_hash

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "Falta DATABASE_URL. Es la cadena de conexión directa a Postgres de Supabase "
        "(Settings → Database → Connection string → Transaction pooler, puerto 6543 "
        "-- ese modo es el pensado para funciones serverless como las de Vercel)."
    )

DEV_PASSWORD_HASH = os.environ.get("DEV_PASSWORD_HASH")
if not DEV_PASSWORD_HASH:
    raise RuntimeError(
        "Falta DEV_PASSWORD_HASH. Generalo una vez con:\n"
        "  python -c \"from werkzeug.security import generate_password_hash; "
        "print(generate_password_hash('tu_clave_aqui'))\"\n"
        "y ponlo como variable de entorno (nunca la clave en texto plano)."
    )

# Columnas que NUNCA se muestran editables (aunque existan en la tabla) --
# datos sensibles o binarios sin sentido para editar a mano.
COLUMNAS_PROTEGIDAS = {"password_hash", "embedding", "qr_token"}
FILAS_POR_PAGINA = 50

app = Flask(__name__)
app.secret_key = os.environ.get("FOCUS_SECRET_KEY") or os.urandom(32)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

_IDENTIFICADOR_VALIDO = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def conectar():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _tablas_reales(cur):
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return {r["table_name"] for r in cur.fetchall()}


def _columnas_reales(cur, tabla):
    cur.execute("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (tabla,))
    return cur.fetchall()


def _clave_primaria(cur, tabla):
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public' AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
        LIMIT 1
    """, (tabla,))
    fila = cur.fetchone()
    return fila["column_name"] if fila else "id"


def _validar_nombre(nombre, nombres_permitidos):
    """Nunca se interpola un nombre de tabla/columna en SQL sin antes
    confirmar que aparece tal cual en information_schema -- esto es lo que
    evita inyección SQL a través de nombres de tabla/columna dinámicos."""
    if nombre not in nombres_permitidos or not _IDENTIFICADOR_VALIDO.match(nombre):
        raise ValueError(f"Nombre no válido o inexistente: {nombre}")
    return nombre


def requiere_login(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("dev_ok"):
            return jsonify({"ok": False, "mensaje": "No autorizado."}), 401
        if request.method in ("POST", "PUT", "DELETE"):
            origen = request.headers.get("Origin") or request.headers.get("Referer")
            if origen and request.host not in origen:
                return jsonify({"ok": False, "mensaje": "Origen no válido."}), 403
        return func(*args, **kwargs)
    return wrapper


# ==================================================================
# Login
# ==================================================================
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    if check_password_hash(DEV_PASSWORD_HASH, data.get("password", "")):
        session.permanent = True
        session["dev_ok"] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("dev_ok", None)
    return jsonify({"ok": True})


# ==================================================================
# Panel 1 — Datos: genérico, descubre tablas/columnas reales
# ==================================================================
@app.route("/api/tablas")
@requiere_login
def api_tablas():
    """Lista todas las tablas reales con su cantidad de filas."""
    with conectar() as conn, conn.cursor() as cur:
        tablas = sorted(_tablas_reales(cur))
        salida = []
        for t in tablas:
            cur.execute(sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier(t)))
            salida.append({"tabla": t, "filas": cur.fetchone()["n"]})
        return jsonify(salida)


@app.route("/api/tablas/<tabla>/columnas")
@requiere_login
def api_columnas(tabla):
    with conectar() as conn, conn.cursor() as cur:
        _validar_nombre(tabla, _tablas_reales(cur))
        cols = _columnas_reales(cur, tabla)
        pk = _clave_primaria(cur, tabla)
        for c in cols:
            c["protegida"] = c["column_name"] in COLUMNAS_PROTEGIDAS
            c["es_clave"] = c["column_name"] == pk
        return jsonify({"columnas": cols, "clave_primaria": pk})


@app.route("/api/tablas/<tabla>")
@requiere_login
def api_filas(tabla):
    pagina = max(int(request.args.get("pagina", 1)), 1)
    q = request.args.get("q", "").strip()
    with conectar() as conn, conn.cursor() as cur:
        _validar_nombre(tabla, _tablas_reales(cur))
        cols = _columnas_reales(cur, tabla)
        nombres_col = [c["column_name"] for c in cols]
        pk = _clave_primaria(cur, tabla)

        where_sql = sql.SQL("")
        params = []
        if q:
            # Busca el texto en todas las columnas de tipo texto de la tabla.
            columnas_texto = [c["column_name"] for c in cols if c["data_type"] in ("text", "character varying")]
            if columnas_texto:
                condiciones = sql.SQL(" OR ").join(
                    sql.SQL("{}::text ILIKE %s").format(sql.Identifier(col)) for col in columnas_texto
                )
                where_sql = sql.SQL("WHERE ") + condiciones
                params = [f"%{q}%"] * len(columnas_texto)

        offset = (pagina - 1) * FILAS_POR_PAGINA
        query = sql.SQL("SELECT * FROM {} {} ORDER BY {} DESC LIMIT %s OFFSET %s").format(
            sql.Identifier(tabla), where_sql, sql.Identifier(pk)
        )
        cur.execute(query, params + [FILAS_POR_PAGINA, offset])
        filas = cur.fetchall()

        query_total = sql.SQL("SELECT COUNT(*) AS n FROM {} {}").format(sql.Identifier(tabla), where_sql)
        cur.execute(query_total, params)
        total = cur.fetchone()["n"]

        # Las columnas protegidas se redactan, no se devuelven en crudo.
        for f in filas:
            for prot in COLUMNAS_PROTEGIDAS:
                if prot in f and f[prot] is not None:
                    f[prot] = "••• (protegido)"

        return jsonify({"filas": filas, "columnas": nombres_col, "clave_primaria": pk,
                         "total": total, "pagina": pagina, "por_pagina": FILAS_POR_PAGINA})


@app.route("/api/tablas/<tabla>/<valor_pk>", methods=["PUT"])
@requiere_login
def api_actualizar_fila(tabla, valor_pk):
    data = request.get_json() or {}
    with conectar() as conn, conn.cursor() as cur:
        _validar_nombre(tabla, _tablas_reales(cur))
        cols = _columnas_reales(cur, tabla)
        nombres_col = {c["column_name"] for c in cols}
        pk = _clave_primaria(cur, tabla)

        cambios = {k: v for k, v in data.items() if k in nombres_col and k not in COLUMNAS_PROTEGIDAS and k != pk}
        if not cambios:
            return jsonify({"ok": False, "mensaje": "Nada para actualizar."}), 400

        set_sql = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(k)) for k in cambios
        )
        query = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
            sql.Identifier(tabla), set_sql, sql.Identifier(pk)
        )
        try:
            cur.execute(query, list(cambios.values()) + [valor_pk])
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({"ok": False, "mensaje": f"No se pudo actualizar: {e}"}), 400
        return jsonify({"ok": True, "filas_afectadas": cur.rowcount})


@app.route("/api/tablas/<tabla>/<valor_pk>", methods=["DELETE"])
@requiere_login
def api_eliminar_fila(tabla, valor_pk):
    with conectar() as conn, conn.cursor() as cur:
        _validar_nombre(tabla, _tablas_reales(cur))
        pk = _clave_primaria(cur, tabla)
        query = sql.SQL("DELETE FROM {} WHERE {} = %s").format(sql.Identifier(tabla), sql.Identifier(pk))
        try:
            cur.execute(query, [valor_pk])
            conn.commit()
        except Exception as e:
            conn.rollback()
            return jsonify({"ok": False, "mensaje": f"No se pudo eliminar (¿tiene datos relacionados?): {e}"}), 400
        return jsonify({"ok": True, "filas_afectadas": cur.rowcount})


@app.route("/api/tablas/<tabla>", methods=["POST"])
@requiere_login
def api_crear_fila(tabla):
    data = request.get_json() or {}
    with conectar() as conn, conn.cursor() as cur:
        _validar_nombre(tabla, _tablas_reales(cur))
        cols = _columnas_reales(cur, tabla)
        nombres_col = {c["column_name"] for c in cols}
        valores = {k: v for k, v in data.items() if k in nombres_col and k not in COLUMNAS_PROTEGIDAS and v not in (None, "")}
        if not valores:
            query = sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING *").format(sql.Identifier(tabla))
            params = []
        else:
            cols_sql = sql.SQL(", ").join(sql.Identifier(k) for k in valores)
            marcadores = sql.SQL(", ").join(sql.Placeholder() for _ in valores)
            query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                sql.Identifier(tabla), cols_sql, marcadores
            )
            params = list(valores.values())
        try:
            cur.execute(query, params)
            conn.commit()
            return jsonify({"ok": True, "fila": cur.fetchone()})
        except Exception as e:
            conn.rollback()
            return jsonify({"ok": False, "mensaje": f"No se pudo crear: {e}"}), 400


# ==================================================================
# Panel 2 — Pendientes (tabla nueva: pendientes_dev)
# ==================================================================
@app.route("/api/pendientes", methods=["GET"])
@requiere_login
def api_pendientes_listar():
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM pendientes_dev ORDER BY hecho ASC, creado_en DESC")
        return jsonify(cur.fetchall())


@app.route("/api/pendientes", methods=["POST"])
@requiere_login
def api_pendientes_crear():
    data = request.get_json() or {}
    texto = (data.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False, "mensaje": "El texto es obligatorio."}), 400
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pendientes_dev (texto, personal_id) VALUES (%s, %s) RETURNING *",
            (texto, data.get("personal_id") or None),
        )
        conn.commit()
        return jsonify({"ok": True, "fila": cur.fetchone()})


@app.route("/api/pendientes/<pid>", methods=["PUT"])
@requiere_login
def api_pendientes_actualizar(pid):
    data = request.get_json() or {}
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE pendientes_dev SET hecho = COALESCE(%s, hecho), texto = COALESCE(%s, texto) WHERE id = %s",
            (data.get("hecho"), data.get("texto"), pid),
        )
        conn.commit()
        return jsonify({"ok": True})


@app.route("/api/pendientes/<pid>", methods=["DELETE"])
@requiere_login
def api_pendientes_eliminar(pid):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM pendientes_dev WHERE id = %s", (pid,))
        conn.commit()
        return jsonify({"ok": True})


# ==================================================================
# Panel 2 — Notas por persona (tabla nueva: notas_personal)
# ==================================================================
@app.route("/api/notas/<personal_id>", methods=["GET"])
@requiere_login
def api_notas_listar(personal_id):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM notas_personal WHERE personal_id = %s ORDER BY creado_en DESC", (personal_id,)
        )
        return jsonify(cur.fetchall())


@app.route("/api/notas/<personal_id>", methods=["POST"])
@requiere_login
def api_notas_crear(personal_id):
    data = request.get_json() or {}
    texto = (data.get("texto") or "").strip()
    if not texto:
        return jsonify({"ok": False, "mensaje": "El texto es obligatorio."}), 400
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notas_personal (personal_id, texto) VALUES (%s, %s) RETURNING *",
            (personal_id, texto),
        )
        conn.commit()
        return jsonify({"ok": True, "fila": cur.fetchone()})


@app.route("/api/notas/nota/<nota_id>", methods=["DELETE"])
@requiere_login
def api_notas_eliminar(nota_id):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM notas_personal WHERE id = %s", (nota_id,))
        conn.commit()
        return jsonify({"ok": True})


# ==================================================================
# Panel 2 — Dashboard de tendencias
# ==================================================================
@app.route("/api/dashboard")
@requiere_login
def api_dashboard():
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT p.curso AS carrera,
                   COUNT(*) FILTER (WHERE r.tipo = 'entrada') AS entradas,
                   COUNT(*) FILTER (WHERE r.tipo = 'entrada' AND r.es_tardanza) AS tardanzas
            FROM registros_asistencia r
            JOIN personal p ON p.id = r.personal_id
            WHERE r.fecha_hora >= (CURRENT_DATE - INTERVAL '90 days')
            GROUP BY p.curso
            ORDER BY entradas DESC
        """)
        por_carrera = cur.fetchall()

        cur.execute("""
            SELECT to_char(date_trunc('month', r.fecha_hora), 'YYYY-MM') AS mes,
                   COUNT(*) FILTER (WHERE r.tipo = 'entrada') AS entradas,
                   COUNT(*) FILTER (WHERE r.tipo = 'entrada' AND r.es_tardanza) AS tardanzas
            FROM registros_asistencia r
            WHERE r.fecha_hora >= (CURRENT_DATE - INTERVAL '12 months')
            GROUP BY 1 ORDER BY 1
        """)
        por_mes = cur.fetchall()

        cur.execute("SELECT COUNT(*) AS n FROM personal WHERE activo = true")
        activos = cur.fetchone()["n"]

        cur.execute("""
            SELECT COUNT(*) AS n FROM registros_asistencia
            WHERE fecha_hora >= CURRENT_DATE AND tipo = 'entrada'
        """)
        entradas_hoy = cur.fetchone()["n"]

        return jsonify({
            "por_carrera": por_carrera, "por_mes": por_mes,
            "personal_activo": activos, "entradas_hoy": entradas_hoy,
        })


@app.route("/")
def inicio():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=os.environ.get("FOCUS_DEBUG", "0") == "1", port=5001)
