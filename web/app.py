"""
FOCUS - Sistema de Asistencia Facial SENATI
app.py — servidor LOCAL (Flask), conectado a Supabase (Postgres + Storage)
en vez de SQLite. Este archivo corre en la PC de SENATI (necesita camara y
DeepFace/TensorFlow), pero guarda y lee todo desde la nube, para que el
dashboard en Next.js/Vercel vea los mismos datos en tiempo real.
"""
import os
import io
import re
import csv
import json
import time
import uuid
import base64
import random
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, session, send_file, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import numpy as np
from PIL import Image
from supabase import create_client, Client

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import warnings
warnings.filterwarnings("ignore")

try:
    from deepface import DeepFace
    DEEPFACE_OK = True
except ImportError:
    DEEPFACE_OK = False

# ------------------------------------------------------------------
# Conexion a Supabase. SUPABASE_SERVICE_KEY es la clave "service_role":
# tiene acceso total (ignora RLS) y NUNCA debe compartirse ni subirse a
# git. Vive solo en el archivo .env local, que esta en .gitignore.
# ------------------------------------------------------------------
load_dotenv()
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL y/o SUPABASE_SERVICE_KEY. Crea un archivo .env junto a este "
        "archivo con esas dos variables (ver instrucciones)."
    )
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
BUCKET_FOTOS = "fotos-personal"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = "Facenet"
DETECTOR_BACKEND = "opencv"
DIAS_ANTES_DE_AVISAR = 30  # cada cuanto se avisa que conviene exportar/liberar espacio
SEGUNDOS_ENTRE_INTENTOS_SYNC = 30

# ==================================================================
# MODO OFFLINE: cola local de respaldo (SQLite, solo para esto). Si
# Supabase no responde (sin internet), los registros de asistencia se
# guardan aqui en vez de perderse, y un hilo en segundo plano los sube
# solos apenas vuelve la conexion.
# ==================================================================
QUEUE_DB_PATH = os.path.join(BASE_DIR, "offline_queue.db")
_lock_queue = threading.Lock()

def init_queue_db():
    with _lock_queue:
        conn = sqlite3.connect(QUEUE_DB_PATH)
        conn.execute("""CREATE TABLE IF NOT EXISTS pendientes_sync (
            id TEXT PRIMARY KEY, personal_id TEXT NOT NULL, tipo TEXT NOT NULL,
            fecha_hora TEXT NOT NULL, confianza REAL, creado_en TEXT NOT NULL
        )""")
        conn.commit()
        conn.close()

def guardar_registro_pendiente(fila):
    with _lock_queue:
        conn = sqlite3.connect(QUEUE_DB_PATH)
        conn.execute(
            "INSERT INTO pendientes_sync (id, personal_id, tipo, fecha_hora, confianza, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (fila["id"], fila["personal_id"], fila["tipo"], fila["fecha_hora"], fila.get("confianza"),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()

def obtener_pendientes():
    with _lock_queue:
        conn = sqlite3.connect(QUEUE_DB_PATH)
        conn.row_factory = sqlite3.Row
        filas = conn.execute("SELECT * FROM pendientes_sync ORDER BY creado_en").fetchall()
        conn.close()
        return [dict(f) for f in filas]

def eliminar_pendiente(id_registro):
    with _lock_queue:
        conn = sqlite3.connect(QUEUE_DB_PATH)
        conn.execute("DELETE FROM pendientes_sync WHERE id = ?", (id_registro,))
        conn.commit()
        conn.close()

def contar_pendientes():
    with _lock_queue:
        conn = sqlite3.connect(QUEUE_DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM pendientes_sync").fetchone()[0]
        conn.close()
        return total

def sincronizador_loop():
    """Corre para siempre en segundo plano. Cada SEGUNDOS_ENTRE_INTENTOS_SYNC revisa
    si hay registros de asistencia pendientes (guardados mientras no habia internet)
    y, si Supabase vuelve a responder, los sube y los borra de la cola local."""
    while True:
        time.sleep(SEGUNDOS_ENTRE_INTENTOS_SYNC)
        pendientes = obtener_pendientes()
        if not pendientes:
            continue
        print(f"[sincronizador] {len(pendientes)} registro(s) pendiente(s), intentando subir...")
        for fila in pendientes:
            try:
                supabase.table("registros_asistencia").insert({
                    "id": fila["id"], "personal_id": fila["personal_id"], "tipo": fila["tipo"],
                    "fecha_hora": fila["fecha_hora"], "confianza": fila["confianza"],
                }).execute()
                eliminar_pendiente(fila["id"])
            except Exception as e:
                # Si el primero falla, es casi seguro que sigue sin haber internet --
                # no tiene sentido seguir intentando con el resto en este ciclo.
                print(f"[sincronizador] todavia sin conexion, se reintentara en {SEGUNDOS_ENTRE_INTENTOS_SYNC}s: {e}")
                break
        else:
            print("[sincronizador] todos los pendientes se subieron correctamente.")

_ultima_deteccion = {}
_lock_deteccion = threading.Lock()
_lock_cache_personal = threading.Lock()
_cache_personal = {"lista": None, "matriz": None, "normas": None}
_lock_tipo_memoria = threading.Lock()
_ultimo_tipo_memoria = {}  # personal_id -> "entrada"/"salida", ultimo tipo conocido en esta sesion
_intentos_login = {}
_lock_login = threading.Lock()
MAX_INTENTOS_LOGIN = 5
BLOQUEO_LOGIN_SEGUNDOS = 300
COLORES_DISPONIBLES = [
    "#c6ff2e", "#6b7a3f", "#ff9d4d", "#4dc0ff", "#ff5d9e",
    "#a774ff", "#4dffb0", "#ffd24d", "#ff5d5d", "#4dfff0",
]

app = Flask(__name__)
_secret_key_env = os.environ.get("FOCUS_SECRET_KEY")
if _secret_key_env:
    app.secret_key = _secret_key_env
else:
    app.secret_key = secrets.token_hex(32)
    print("AVISO: no se definio FOCUS_SECRET_KEY; se genero una clave de sesion aleatoria temporal.")
    print("       Define esa variable en tu .env para que las sesiones de admin no se cierren en cada reinicio.")
DEBUG_MODE = os.environ.get("FOCUS_DEBUG", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ==================================================================
# Utilidades de Storage (fotos) — reemplazan a guardar en disco local
# ==================================================================
def _slug(nombre):
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", nombre.strip().replace(" ", "_"))
    return slug[:60] or "persona"

def subir_foto_storage(nombre_persona, frame_bgr, sufijo=""):
    """Sube una imagen (array BGR de OpenCV/PIL) al bucket de Supabase Storage y
    devuelve la URL publica. Antes esto guardaba un archivo en
    web/static/img/fotos_personal/; ahora vive en la nube para que tanto el
    reconocimiento local como el dashboard en Vercel puedan mostrarla."""
    filename = f"{_slug(nombre_persona)}{sufijo}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    buffer = io.BytesIO()
    Image.fromarray(frame_bgr[:, :, ::-1]).save(buffer, format="JPEG", quality=90)
    buffer.seek(0)
    supabase.storage.from_(BUCKET_FOTOS).upload(
        filename, buffer.read(), {"content-type": "image/jpeg"}
    )
    return supabase.storage.from_(BUCKET_FOTOS).get_public_url(filename)

def eliminar_foto_storage(url_publica):
    """Borra una foto del bucket a partir de su URL publica guardada en la BD.
    Si la URL no pertenece a este bucket (o esta vacia), no hace nada."""
    if not url_publica or f"/{BUCKET_FOTOS}/" not in url_publica:
        return
    path = url_publica.split(f"/{BUCKET_FOTOS}/", 1)[1]
    try:
        supabase.storage.from_(BUCKET_FOTOS).remove([path])
    except Exception as e:
        print(f"[eliminar_foto_storage] no se pudo borrar {path}: {e}")


def subir_pdf_storage(nombre_persona, data_url_o_bytes, nombre_original=None):
    """Sube un PDF (data URL base64 o bytes) al mismo bucket y devuelve URL pública."""
    if isinstance(data_url_o_bytes, str):
        raw = data_url_o_bytes
        if raw.startswith("data:"):
            raw = raw.split(",", 1)[-1]
        contenido = base64.b64decode(raw)
    else:
        contenido = data_url_o_bytes
    safe_name = _slug(nombre_original or "horario")[:40]
    filename = f"horario_{_slug(nombre_persona)}_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    supabase.storage.from_(BUCKET_FOTOS).upload(
        filename, contenido, {"content-type": "application/pdf"}
    )
    return supabase.storage.from_(BUCKET_FOTOS).get_public_url(filename)


# ==================================================================
# Configuracion / cache (igual que antes, pero leyendo de Supabase)
# ==================================================================
_lock_config = threading.Lock()
_cache_config = None

def invalidar_config():
    global _cache_config
    with _lock_config:
        _cache_config = None

def cargar_config():
    global _cache_config
    with _lock_config:
        if _cache_config is not None:
            return dict(_cache_config)
    try:
        res = supabase.table("admin_config").select(
            "umbral_confianza,intervalo_escaneo,tiempo_reescaneo,hora_entrada,hora_salida,tolerancia_min"
        ).eq("id", 1).execute()
        row = res.data[0] if res.data else {}
    except Exception as e:
        # Sin internet y sin cache previa (ej. primer arranque offline): sigue con
        # los valores por defecto en vez de tumbar el servidor.
        print(f"[cargar_config] sin conexion a Supabase, usando valores por defecto: {e}")
        row = {}
    config = dict(row)
    config.setdefault("umbral_confianza", 0.40)
    config.setdefault("intervalo_escaneo", 3.0)
    config.setdefault("tiempo_reescaneo", 4)
    config.setdefault("hora_entrada", "08:00")
    config.setdefault("hora_salida", "17:00")
    config.setdefault("tolerancia_min", 0)
    with _lock_config:
        _cache_config = dict(config)
    return config

def color_para_nuevo_personal():
    res = supabase.table("personal").select("color").execute()
    usados = {r["color"] for r in (res.data or []) if r.get("color")}
    libres = [c for c in COLORES_DISPONIBLES if c not in usados]
    return libres[0] if libres else "#%06x" % random.randint(0, 0xFFFFFF)

def verificar_admin_inicial():
    """Si la tabla admin_config esta vacia (instalacion nueva), crea la fila con
    una contrasena aleatoria impresa una sola vez en consola. Si ya existe (como
    en tu caso, que ya la cambiaste desde Supabase), no la toca."""
    res = supabase.table("admin_config").select("id").eq("id", 1).execute()
    if res.data:
        return
    clave_inicial = secrets.token_urlsafe(9)
    supabase.table("admin_config").insert({
        "id": 1, "password_hash": generate_password_hash(clave_inicial),
    }).execute()
    print("=" * 64)
    print("CONTRASEÑA DE ADMINISTRADOR GENERADA (instalación nueva):")
    print("   " + clave_inicial)
    print("Guárdala ahora: no se volverá a mostrar. Podrás cambiarla luego desde el panel admin.")
    print("=" * 64)


# ==================================================================
# Reconocimiento facial (igual logica que antes, cache en memoria)
# ==================================================================
def origen_valido():
    origen = request.headers.get("Origin") or request.headers.get("Referer")
    if not origen:
        return True
    return request.host in origen

def requiere_admin(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"ok": False, "mensaje": "No autorizado."}), 401
        if request.method in ("POST", "PUT", "DELETE") and not origen_valido():
            return jsonify({"ok": False, "mensaje": "Solicitud rechazada (origen no válido)."}), 403
        return func(*args, **kwargs)
    return wrapper

def dataurl_a_bgr(data_url):
    header, encoded = data_url.split(",", 1)
    img_bytes = base64.b64decode(encoded)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    arr = np.array(img)
    return arr[:, :, ::-1]

def extraer_embedding(frame_bgr):
    representation = DeepFace.represent(frame_bgr, model_name=MODEL_NAME, detector_backend=DETECTOR_BACKEND, enforce_detection=True)
    return np.array(representation[0]["embedding"]) if representation else None

def invalidar_cache_personal():
    with _lock_cache_personal:
        _cache_personal["lista"] = None
        _cache_personal["matriz"] = None
        _cache_personal["normas"] = None

def cargar_personal_activo():
    with _lock_cache_personal:
        if _cache_personal["lista"] is not None:
            return _cache_personal["lista"], _cache_personal["matriz"], _cache_personal["normas"]

    try:
        res = supabase.table("personal").select(
            "id,nombre,tipo_personal,area,cargo,foto_path,icono_path,embedding"
        ).eq("activo", True).execute()
        filas = res.data or []
    except Exception as e:
        # Sin internet: si ya habia una lista cargada de antes, se sigue usando esa
        # (el reconocimiento no debe detenerse). Si nunca se cargo nada (arranque
        # offline desde cero), no queda otra que devolver una lista vacia.
        print(f"[cargar_personal_activo] sin conexion a Supabase, usando ultima lista conocida: {e}")
        with _lock_cache_personal:
            if _cache_personal["lista"] is not None:
                return _cache_personal["lista"], _cache_personal["matriz"], _cache_personal["normas"]
        filas = []
    # El embedding ya viene como arreglo nativo de Postgres (lista de floats),
    # no como BLOB con pickle -- no hace falta deserializar nada.
    lista = [{**f, "embedding": np.array(f["embedding"], dtype=float)} for f in filas]
    if lista:
        matriz = np.stack([p["embedding"] for p in lista])
        normas = np.linalg.norm(matriz, axis=1)
    else:
        matriz = np.zeros((0, 0))
        normas = np.zeros((0,))

    with _lock_cache_personal:
        _cache_personal["lista"] = lista
        _cache_personal["matriz"] = matriz
        _cache_personal["normas"] = normas
    return lista, matriz, normas

def identificar_rostro(embedding_captura, lista, matriz, normas):
    if not lista: return None, float("inf")
    norma_captura = np.linalg.norm(embedding_captura)
    if norma_captura == 0: return None, float("inf")
    similitudes = (matriz @ embedding_captura) / (normas * norma_captura + 1e-10)
    distancias = 1 - similitudes
    idx = int(np.argmin(distancias))
    return lista[idx], float(distancias[idx])


# ==================================================================
# Rutas — paginas
# ==================================================================
@app.route("/")
def inicio(): return render_template("index.html")

@app.route("/<path:page>")
def spa_router(page):
    if page in ['registros', 'calendario', 'admin', 'admin/enrolar']:
        if page == 'admin/enrolar' and not session.get("is_admin"):
            return redirect(url_for("spa_router", page='admin'))
        return render_template("index.html")
    return render_template("index.html")


# ==================================================================
# Reconocimiento / identificacion
# ==================================================================
def _minutos(hora_str):
    """'07:30' o '7:30:00' → minutos desde medianoche."""
    if not hora_str:
        return None
    partes = str(hora_str).strip().split(":")
    try:
        return int(partes[0]) * 60 + int(partes[1])
    except (ValueError, IndexError):
        return None


def obtener_bloques_hoy(personal_id, ahora=None):
    """Bloques del día de la semana actual ordenados por hora_inicio."""
    ahora = ahora or datetime.now()
    # Python: lunes=0 … domingo=6 (igual que DIAS_SEMANA del editor)
    dia = ahora.weekday()
    try:
        res = supabase.table("horarios_bloques").select(
            "id,dia_semana,hora_inicio,hora_fin,materia,tolerancia_min"
        ).eq("personal_id", personal_id).eq("dia_semana", dia).order("hora_inicio").execute()
        return res.data or []
    except Exception as e:
        print(f"[obtener_bloques_hoy] {e}")
        return []


def decidir_tipo_por_horario(personal_id, ahora=None):
    """Usa bloques de clase para decidir entrada/salida y si es tardanza.

    Reglas:
    - Cada bloque tiene inicio, fin y tolerancia (default 5 min).
    - Si la hora actual está cerca del inicio del bloque (± ventana) → entrada.
      Tarde si marca después de inicio + tolerancia.
    - Si está cerca del fin → salida.
    - Si hay dos clases seguidas (fin A y inicio B muy cerca) y marca entre ambas,
      puede generar lógica de salida de A / entrada a B según el último registro.
    - Sin bloques: alterna entrada/salida como antes.
    """
    ahora = ahora or datetime.now()
    min_ahora = ahora.hour * 60 + ahora.minute
    bloques = obtener_bloques_hoy(personal_id, ahora)

    # Fallback sin horario: alternar
    def fallback_alternar():
        with _lock_tipo_memoria:
            ultimo_tipo_memoria = _ultimo_tipo_memoria.get(personal_id)
        if ultimo_tipo_memoria is not None:
            return {"tipo": "salida" if ultimo_tipo_memoria == "entrada" else "entrada",
                    "es_tardanza": False, "bloque_id": None, "materia": None,
                    "mensaje": "sin horario cargado — modo alternado"}
        try:
            res_ultimo = supabase.table("registros_asistencia").select("tipo") \
                .eq("personal_id", personal_id).order("fecha_hora", desc=True).limit(1).execute()
            ultimo = res_ultimo.data[0] if res_ultimo.data else None
            tipo = "entrada" if (ultimo is None or ultimo["tipo"] == "salida") else "salida"
        except Exception:
            tipo = "entrada"
        return {"tipo": tipo, "es_tardanza": False, "bloque_id": None, "materia": None,
                "mensaje": "sin horario cargado — modo alternado"}

    if not bloques:
        return fallback_alternar()

    # Enriquecer bloques con minutos
    for b in bloques:
        b["_ini"] = _minutos(b.get("hora_inicio"))
        b["_fin"] = _minutos(b.get("hora_fin"))
        b["_tol"] = int(b.get("tolerancia_min") if b.get("tolerancia_min") is not None else 5)

    # Último registro de hoy (para no duplicar entrada del mismo bloque)
    hoy_str = ahora.strftime("%Y-%m-%d")
    try:
        regs_hoy = supabase.table("registros_asistencia").select(
            "tipo,fecha_hora,bloque_id,es_tardanza"
        ).eq("personal_id", personal_id).gte("fecha_hora", hoy_str).lte(
            "fecha_hora", hoy_str + " 23:59:59"
        ).order("fecha_hora", desc=True).execute().data or []
    except Exception:
        regs_hoy = []

    ultimo_reg = regs_hoy[0] if regs_hoy else None

    # Elegir el bloque más relevante a la hora actual
    mejor = None
    mejor_dist = 10**9
    for b in bloques:
        if b["_ini"] is None or b["_fin"] is None:
            continue
        # Dentro del bloque (con margen de tolerancia antes del inicio)
        if b["_ini"] - b["_tol"] <= min_ahora <= b["_fin"] + b["_tol"]:
            dist = 0
        else:
            dist = min(abs(min_ahora - b["_ini"]), abs(min_ahora - b["_fin"]))
        if dist < mejor_dist:
            mejor_dist = dist
            mejor = b

    if mejor is None:
        return fallback_alternar()

    ini, fin, tol = mejor["_ini"], mejor["_fin"], mejor["_tol"]
    mid = (ini + fin) / 2

    # ¿Ya hay entrada para este bloque hoy?
    ya_entro_bloque = any(
        r.get("tipo") == "entrada" and str(r.get("bloque_id") or "") == str(mejor.get("id") or "")
        for r in regs_hoy
    )
    # Heurística adicional: si el último fue entrada y estamos en la 2ª mitad → salida
    if ultimo_reg and ultimo_reg.get("tipo") == "entrada" and min_ahora >= mid:
        tipo = "salida"
        es_tardanza = False  # salida tardía se evalúa distinto
        # Salida después del fin + tolerancia cuenta como irregular (no "tarde" de entrada)
        mensaje = None
        if min_ahora > fin + tol:
            mensaje = f"salida fuera de horario del bloque {mejor.get('hora_inicio')}-{mejor.get('hora_fin')}"
    elif ya_entro_bloque or (ultimo_reg and ultimo_reg.get("tipo") == "entrada" and min_ahora < mid):
        tipo = "salida"
        es_tardanza = False
        mensaje = None
    else:
        tipo = "entrada"
        # Tarde si marca después de inicio + tolerancia
        es_tardanza = min_ahora > (ini + tol)
        mensaje = (
            f"entrada tarde al bloque {mejor.get('hora_inicio')} (tol. {tol} min)"
            if es_tardanza else
            f"entrada a tiempo — {mejor.get('materia') or mejor.get('hora_inicio')}"
        )

    # Clase siguiente muy cerca: si salimos tarde de A y ya empezó B, la entrada a B también será tarde
    # (eso se evalúa en el próximo marcaje como entrada al bloque B).
    return {
        "tipo": tipo,
        "es_tardanza": bool(es_tardanza),
        "bloque_id": mejor.get("id"),
        "materia": mejor.get("materia"),
        "mensaje": mensaje,
    }


_ultima_alerta_tardanzas = 0


def revisar_alerta_tardanzas():
    """Si hoy hay ≥ N tardanzas, notifica al director (email/webhook WhatsApp)."""
    global _ultima_alerta_tardanzas
    ahora = time.time()
    if ahora - _ultima_alerta_tardanzas < 3600:
        return  # como máximo 1 alerta por hora
    cfg = cargar_config()
    umbral = int(cfg.get("alerta_tardanzas_umbral") or 5)
    email = (cfg.get("alerta_email") or "").strip()
    webhook = (cfg.get("alerta_webhook") or "").strip()
    if not email and not webhook:
        return
    hoy = datetime.now().strftime("%Y-%m-%d")
    try:
        res = supabase.table("registros_asistencia").select("id", count="exact") \
            .eq("es_tardanza", True).gte("fecha_hora", hoy).lte("fecha_hora", hoy + " 23:59:59").execute()
        total = res.count if res.count is not None else len(res.data or [])
    except Exception:
        return
    if total < umbral:
        return
    _ultima_alerta_tardanzas = ahora
    texto = f"[FaceTrack] Alerta: {total} tardanzas registradas hoy ({hoy}). Umbral: {umbral}."
    if webhook:
        try:
            import urllib.request
            req = urllib.request.Request(
                webhook, data=json.dumps({"text": texto, "mensaje": texto}).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST"
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception as e:
            print(f"[alerta webhook] {e}")
    if email:
        try:
            # Solo registra la intención; el envío real depende de SMTP en el servidor
            print(f"[alerta email] → {email}: {texto}")
            supabase.table("admin_config").update({
                "ultima_alerta_tardanzas": datetime.now().isoformat()
            }).eq("id", 1).execute()
        except Exception as e:
            print(f"[alerta email] {e}")


@app.route("/api/reconocer", methods=["POST"])
def api_reconocer():
    if not DEEPFACE_OK: return jsonify({"ok": False, "mensaje": "DeepFace no está instalado."}), 500
    data = request.get_json()
    try:
        frame = dataurl_a_bgr(data["imagen"])
        embedding = extraer_embedding(frame)
    except ValueError:
        return jsonify({"ok": False, "mensaje": "No se detectó ningún rostro. Intenta de nuevo."})
    except Exception as e:
        print(f"[api_reconocer] error inesperado procesando el frame: {type(e).__name__}: {e}")
        return jsonify({"ok": False, "mensaje": "No se detectó ningún rostro. Intenta de nuevo."})
    if embedding is None: return jsonify({"ok": False, "mensaje": "No se detectó ningún rostro."})

    lista, matriz, normas = cargar_personal_activo()
    if not lista: return jsonify({"ok": False, "mensaje": "No hay personal registrado."})

    cfg = cargar_config()
    p, distancia = identificar_rostro(embedding, lista, matriz, normas)
    if p is None or distancia > cfg["umbral_confianza"]: return jsonify({"ok": False, "mensaje": "Rostro no reconocido."})

    tiempo_reescaneo = cfg["tiempo_reescaneo"]
    ahora = time.time()
    with _lock_deteccion:
        ultima = _ultima_deteccion.get(p["id"], 0)
        if ahora - ultima < tiempo_reescaneo:
            restante = round(tiempo_reescaneo - (ahora - ultima), 1)
            return jsonify({"ok": False, "mensaje": f"Ya se registro a {p['nombre']}. Espera {restante}s.",
                             "reescaneo": True})
        _ultima_deteccion[p["id"]] = ahora

    # Decide entrada/salida y tardanza según bloques de horario del día.
    # Si no hay bloques, cae al modo alternado (entrada → salida → entrada...).
    ahora_dt = datetime.now()
    decision = decidir_tipo_por_horario(p["id"], ahora_dt)
    tipo = decision["tipo"]
    es_tardanza = decision["es_tardanza"]
    bloque_id = decision.get("bloque_id")
    materia = decision.get("materia")

    registro_id = str(uuid.uuid4())
    fecha_hora_local = ahora_dt.strftime("%Y-%m-%d %H:%M:%S")
    fila = {
        "id": registro_id,
        "personal_id": p["id"],
        "tipo": tipo,
        "confianza": round(1 - distancia, 3),
        "fecha_hora": fecha_hora_local,
        "es_tardanza": es_tardanza,
        "bloque_id": bloque_id,
        "materia": materia,
    }
    try:
        supabase.table("registros_asistencia").insert(fila).execute()
    except Exception as e:
        # Si fallan columnas nuevas (migración pendiente), reintentar sin ellas
        msg = str(e).lower()
        if "es_tardanza" in msg or "bloque_id" in msg or "materia" in msg or "column" in msg:
            fila_min = {
                "id": registro_id, "personal_id": p["id"], "tipo": tipo,
                "confianza": round(1 - distancia, 3), "fecha_hora": fecha_hora_local,
            }
            try:
                supabase.table("registros_asistencia").insert(fila_min).execute()
            except Exception as e2:
                print(f"[api_reconocer] sin conexion a Supabase, guardando localmente: {e2}")
                guardar_registro_pendiente(fila_min)
        else:
            print(f"[api_reconocer] sin conexion a Supabase, guardando localmente: {e}")
            guardar_registro_pendiente(fila)
    with _lock_tipo_memoria:
        _ultimo_tipo_memoria[p["id"]] = tipo

    # Aviso al director si se acumulan muchas tardanzas hoy
    try:
        if es_tardanza:
            revisar_alerta_tardanzas()
    except Exception as e:
        print(f"[api_reconocer] alerta tardanzas: {e}")

    return jsonify({
        "ok": True,
        "nombre": p["nombre"],
        "tipo": tipo,
        "confianza": round((1 - distancia) * 100, 1),
        "foto": p.get("icono_path") or p.get("foto_path"),
        "es_tardanza": es_tardanza,
        "materia": materia,
        "mensaje_extra": decision.get("mensaje"),
    })

@app.route("/api/identificar", methods=["POST"])
def api_identificar():
    if not DEEPFACE_OK: return jsonify({"ok": False, "mensaje": "DeepFace no está instalado."}), 500
    data = request.get_json()
    try:
        frame = dataurl_a_bgr(data["imagen"])
        embedding = extraer_embedding(frame)
    except ValueError:
        return jsonify({"ok": False, "mensaje": "No se detectó ningún rostro. Intenta de nuevo."})
    except Exception as e:
        print(f"[api_identificar] error inesperado procesando el frame: {type(e).__name__}: {e}")
        return jsonify({"ok": False, "mensaje": "No se detectó ningún rostro. Intenta de nuevo."})
    if embedding is None: return jsonify({"ok": False, "mensaje": "No se detectó ningún rostro."})

    lista, matriz, normas = cargar_personal_activo()
    if not lista: return jsonify({"ok": False, "mensaje": "No hay personal registrado."})

    cfg = cargar_config()
    p, distancia = identificar_rostro(embedding, lista, matriz, normas)
    if p is None or distancia > cfg["umbral_confianza"]: return jsonify({"ok": False, "mensaje": "Rostro no reconocido."})

    res = supabase.table("registros_asistencia").select("tipo,fecha_hora,confianza") \
        .eq("personal_id", p["id"]).order("fecha_hora", desc=True).limit(30).execute()
    registros = res.data or []
    return jsonify({"ok": True, "id": p["id"], "nombre": p["nombre"], "confianza": round((1 - distancia) * 100, 1),
                     "foto": p.get("icono_path") or p.get("foto_path"),
                     "registros": [{"tipo": r["tipo"], "fecha_hora": r["fecha_hora"], "confianza": r["confianza"]} for r in registros]})


# ==================================================================
# Registros / reportes
# ==================================================================
@app.route("/api/registros/resumen")
def api_registros_resumen():
    """Igual que /api/registros pero agrupado: una fila por persona por dia (con su
    entrada y salida ya combinadas en el mismo objeto), en vez de una fila por cada
    evento suelto. Esto es lo que consumen Inicio (Recientes) y Registros -- el
    Calendario sigue usando /api/registros tal cual, porque ahi conviene tener los
    eventos sueltos (los agrupa el propio JS del calendario)."""
    rango = request.args.get("rango", "mes")
    q = request.args.get("q", "").strip()
    hoy = datetime.now()
    if rango == "semana": desde = hoy - timedelta(days=hoy.weekday())
    elif rango == "anio": desde = hoy.replace(month=1, day=1)
    elif rango == "custom":
        try:
            desde = datetime.strptime(request.args.get("inicio"), "%Y-%m-%d")
            hasta = datetime.strptime(request.args.get("fin"), "%Y-%m-%d")
        except Exception:
            return jsonify({"ok": False, "mensaje": "Fechas inválidas"}), 400
    else: desde = hoy.replace(day=1)
    if rango != "custom": hasta = hoy

    query = supabase.table("registros_asistencia").select(
        "id,tipo,fecha_hora,confianza,personal_id,personal(nombre,tipo_personal,area,curso,color,foto_path),"
        "justificaciones(id,estado)"
    ).gte("fecha_hora", desde.strftime("%Y-%m-%d")).lte("fecha_hora", hasta.strftime("%Y-%m-%d") + " 23:59:59")
    if q:
        query = query.or_(f"nombre.ilike.%{q}%,area.ilike.%{q}%,curso.ilike.%{q}%", reference_table="personal")
    filas = query.order("fecha_hora", desc=True).limit(2000).execute().data or []

    # Agrupa por (persona, dia). Las filas vienen ordenadas de mas reciente a mas
    # antigua, asi que la primera entrada/salida que encontramos para cada grupo ya
    # es la mas reciente de ese tipo -- no hace falta comparar fechas a mano.
    grupos = {}
    for f in filas:
        p = f.get("personal") or {}
        dia = (f["fecha_hora"] or "").split(" ")[0]
        clave = (f["personal_id"], dia)
        if clave not in grupos:
            grupos[clave] = {
                "personal_id": f["personal_id"], "nombre": p.get("nombre"),
                "tipo_personal": p.get("tipo_personal"), "area": p.get("area"),
                "curso": p.get("curso"), "color": p.get("color") or "#c6ff2e",
                "foto": p.get("foto_path"), "fecha": dia, "entrada": None, "salida": None,
                "justificado": False,
            }
        g = grupos[clave]
        justs = f.get("justificaciones") or []
        if justs and max(justs, key=lambda j: j["id"])["estado"] == "aprobado":
            g["justificado"] = True
        campo = "entrada" if f["tipo"] == "entrada" else "salida"
        if g[campo] is None:
            g[campo] = {"fecha_hora": f["fecha_hora"], "confianza": f.get("confianza") or 0}

    resultado = sorted(grupos.values(), key=lambda g: g["fecha"], reverse=True)
    return jsonify(resultado)


@app.route("/api/registros")
def api_registros():
    rango = request.args.get("rango", "mes")
    q = request.args.get("q", "").strip()
    tipo = (request.args.get("tipo") or "").strip().lower()
    hoy = datetime.now()
    if rango == "semana": desde = hoy - timedelta(days=hoy.weekday())
    elif rango == "anio": desde = hoy.replace(month=1, day=1)
    elif rango == "custom":
        try:
            desde = datetime.strptime(request.args.get("inicio"), "%Y-%m-%d")
            hasta = datetime.strptime(request.args.get("fin"), "%Y-%m-%d")
        except Exception: return jsonify({"ok": False, "mensaje": "Fechas inválidas"}), 400
    else: desde = hoy.replace(day=1)
    if rango != "custom": hasta = hoy

    query = supabase.table("registros_asistencia").select(
        "id,tipo,fecha_hora,confianza,es_tardanza,bloque_id,materia,"
        "personal(nombre,tipo_personal,area,curso,color,foto_path),"
        "justificaciones(id,estado)"
    ).gte("fecha_hora", desde.strftime("%Y-%m-%d")).lte("fecha_hora", hasta.strftime("%Y-%m-%d") + " 23:59:59")
    if tipo in ("entrada", "salida"):
        query = query.eq("tipo", tipo)
    if q:
        # Filtro sobre columnas de la tabla relacionada 'personal' vía sintaxis de
        # PostgREST para recursos embebidos.
        query = query.or_(f"nombre.ilike.%{q}%,area.ilike.%{q}%,curso.ilike.%{q}%", reference_table="personal")
    query = query.order("fecha_hora", desc=True).limit(1000)
    try:
        filas = query.execute().data or []
    except Exception:
        # Migración pendiente de es_tardanza: reintentar sin esas columnas
        query = supabase.table("registros_asistencia").select(
            "id,tipo,fecha_hora,confianza,personal(nombre,tipo_personal,area,curso,color,foto_path),"
            "justificaciones(id,estado)"
        ).gte("fecha_hora", desde.strftime("%Y-%m-%d")).lte("fecha_hora", hasta.strftime("%Y-%m-%d") + " 23:59:59")
        if tipo in ("entrada", "salida"):
            query = query.eq("tipo", tipo)
        filas = query.order("fecha_hora", desc=True).limit(1000).execute().data or []

    salida = []
    for f in filas:
        p = f.get("personal") or {}
        justs = f.get("justificaciones") or []
        ultimo_just = max(justs, key=lambda j: j["id"])["estado"] if justs else None
        salida.append({
            "registro_id": f["id"], "nombre": p.get("nombre"), "tipo_personal": p.get("tipo_personal"),
            "area": p.get("area"), "curso": p.get("curso"), "color": p.get("color") or "#c6ff2e",
            "foto": p.get("foto_path"), "tipo": f["tipo"], "fecha_hora": f["fecha_hora"],
            "confianza": f.get("confianza") or 0, "justificado": ultimo_just == "aprobado",
            "es_tardanza": bool(f.get("es_tardanza")), "materia": f.get("materia"),
        })
    return jsonify(salida)


# ==================================================================
# Personal (CRUD + fotos)
# ==================================================================
@app.route("/api/personal/publico", methods=["GET"])
def api_personal_publico():
    """Lista pública reducida para la sección de números telefónicos (sin DNI ni embeddings)."""
    res = supabase.table("personal").select(
        "id,nombre,tipo_personal,area,cargo,activo,color,foto_path,icono_path,telefono,correo"
    ).eq("activo", True).order("nombre").execute()
    return jsonify(res.data or [])

@app.route("/api/personal", methods=["GET"])
@requiere_admin
def api_personal():
    res = supabase.table("personal").select(
        "id,dni,codigo,nombre,tipo_personal,area,curso,semestre,cargo,activo,color,foto_path,icono_path,horario_path,correo,telefono,fecha_registro"
    ).order("nombre").execute()
    return jsonify(res.data or [])

@app.route("/api/personal", methods=["POST"])
@requiere_admin
def api_personal_create():
    if not DEEPFACE_OK: return jsonify({"ok": False, "mensaje": "DeepFace no instalado."}), 500
    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre: return jsonify({"ok": False, "mensaje": "El nombre es obligatorio."}), 400
    dni = (data.get("dni") or "").strip()
    if not dni: return jsonify({"ok": False, "mensaje": "El DNI es obligatorio."}), 400
    tipo_personal = data.get("tipo_personal", "instructor")
    codigo = (data.get("codigo") or "").strip()
    if tipo_personal == "instructor" and not codigo:
        return jsonify({"ok": False, "mensaje": "El ID / código de personal es obligatorio para instructores."}), 400

    imagen_raw = data.get("imagen") or data.get("foto")
    if not imagen_raw: return jsonify({"ok": False, "mensaje": "La foto es obligatoria."}), 400
    if not imagen_raw.startswith("data:"): imagen_raw = "data:image/jpeg;base64," + imagen_raw

    try:
        frame = dataurl_a_bgr(imagen_raw)
        embedding = extraer_embedding(frame)
    except Exception: embedding = None
    if embedding is None: return jsonify({"ok": False, "mensaje": "No se detectó rostro en la foto."}), 400

    foto_url = subir_foto_storage(nombre, frame)

    icono_url = None
    icono_raw = data.get("icono")
    if icono_raw:
        try:
            if not icono_raw.startswith("data:"): icono_raw = "data:image/jpeg;base64," + icono_raw
            icono_frame = dataurl_a_bgr(icono_raw)
            icono_url = subir_foto_storage(nombre, icono_frame, sufijo="_icono")
        except Exception:
            icono_url = None

    horario_url = None
    horario_raw = data.get("horario")
    if horario_raw:
        try:
            horario_url = subir_pdf_storage(nombre, horario_raw, data.get("horario_nombre"))
        except Exception as e:
            print(f"[api_personal_create] error subiendo horario PDF: {e}")

    try:
        fila_insert = {
            "dni": dni, "codigo": codigo, "nombre": nombre, "tipo_personal": tipo_personal,
            "area": data.get("area"), "curso": data.get("curso"), "semestre": data.get("semestre"),
            "cargo": data.get("cargo"), "embedding": embedding.tolist(), "foto_path": foto_url,
            "icono_path": icono_url, "color": data.get("color") or color_para_nuevo_personal(),
            "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "correo": (data.get("correo") or "").strip() or None,
            "telefono": (data.get("telefono") or "").strip() or None,
        }
        if horario_url:
            fila_insert["horario_path"] = horario_url
        supabase.table("personal").insert(fila_insert).execute()
    except Exception as e:
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            return jsonify({"ok": False, "mensaje": "El DNI ya está registrado."}), 400
        print(f"[api_personal_create] error: {e}")
        return jsonify({"ok": False, "mensaje": "No se pudo guardar el personal."}), 500
    invalidar_cache_personal()
    return jsonify({"ok": True})

@app.route("/api/personal/<id>", methods=["PUT"])
@requiere_admin
def api_personal_update(id):
    res = supabase.table("personal").select("*").eq("id", id).execute()
    if not res.data:
        return jsonify({"ok": False, "mensaje": "No se encontró el personal."}), 404
    row = res.data[0]

    data = request.get_json() or {}
    nombre = (data.get("nombre") or row["nombre"] or "").strip()
    dni = (data.get("dni") or row["dni"] or "").strip()
    codigo = (data.get("codigo") or row["codigo"] or "").strip()
    tipo_personal = data.get("tipo_personal") or row["tipo_personal"]
    if not nombre: return jsonify({"ok": False, "mensaje": "El nombre es obligatorio."}), 400
    if not dni: return jsonify({"ok": False, "mensaje": "El DNI es obligatorio."}), 400
    if tipo_personal == "instructor" and not codigo:
        return jsonify({"ok": False, "mensaje": "El ID / código es obligatorio para instructores."}), 400

    cambios = {
        "dni": dni, "codigo": codigo, "nombre": nombre, "tipo_personal": tipo_personal,
        "area": data.get("area", row["area"]), "curso": data.get("curso", row["curso"]),
        "semestre": data.get("semestre", row["semestre"]), "cargo": data.get("cargo", row["cargo"]),
    }

    foto_anterior = row["foto_path"]
    imagen_raw = data.get("foto")
    if imagen_raw:
        if not DEEPFACE_OK: return jsonify({"ok": False, "mensaje": "DeepFace no instalado."}), 500
        if not imagen_raw.startswith("data:"): imagen_raw = "data:image/jpeg;base64," + imagen_raw
        try:
            frame = dataurl_a_bgr(imagen_raw)
            embedding = extraer_embedding(frame)
        except Exception:
            embedding = None
        if embedding is None:
            return jsonify({"ok": False, "mensaje": "No se detectó rostro en la nueva foto."}), 400
        cambios["foto_path"] = subir_foto_storage(nombre, frame)
        cambios["embedding"] = embedding.tolist()

    icono_anterior = row["icono_path"]
    icono_raw = data.get("icono")
    if icono_raw:
        try:
            if not icono_raw.startswith("data:"): icono_raw = "data:image/jpeg;base64," + icono_raw
            icono_frame = dataurl_a_bgr(icono_raw)
            cambios["icono_path"] = subir_foto_storage(nombre, icono_frame, sufijo="_icono")
        except Exception:
            pass

    try:
        supabase.table("personal").update(cambios).eq("id", id).execute()
    except Exception as e:
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            return jsonify({"ok": False, "mensaje": "El DNI ya está registrado para otra persona."}), 400
        print(f"[api_personal_update] error: {e}")
        return jsonify({"ok": False, "mensaje": "No se pudo actualizar."}), 500

    if "foto_path" in cambios and foto_anterior and foto_anterior != cambios["foto_path"]:
        eliminar_foto_storage(foto_anterior)
    if "icono_path" in cambios and icono_anterior and icono_anterior != cambios["icono_path"]:
        eliminar_foto_storage(icono_anterior)
    invalidar_cache_personal()
    return jsonify({"ok": True})

@app.route("/api/personal/<id>", methods=["DELETE"])
@requiere_admin
def api_personal_delete(id):
    res = supabase.table("personal").select("foto_path,icono_path").eq("id", id).execute()
    if not res.data: return jsonify({"ok": False}), 404
    row = res.data[0]
    supabase.table("justificaciones").delete().eq("personal_id", id).execute()
    supabase.table("registros_asistencia").delete().eq("personal_id", id).execute()
    supabase.table("horarios_bloques").delete().eq("personal_id", id).execute()
    supabase.table("personal").delete().eq("id", id).execute()
    eliminar_foto_storage(row.get("foto_path"))
    eliminar_foto_storage(row.get("icono_path"))
    with _lock_deteccion:
        _ultima_deteccion.pop(id, None)
    invalidar_cache_personal()
    return jsonify({"ok": True})

@app.route("/api/personal/<id>/toggle", methods=["POST"])
@requiere_admin
def api_personal_toggle(id):
    res = supabase.table("personal").select("activo").eq("id", id).execute()
    if not res.data: return jsonify({"ok": False}), 404
    activo = res.data[0]["activo"]
    supabase.table("personal").update({"activo": not activo}).eq("id", id).execute()
    invalidar_cache_personal()
    return jsonify({"ok": True})


@app.route("/api/personal/<id>/estadisticas", methods=["GET"])
@requiere_admin
def api_personal_estadisticas(id):
    """Resumen de asistencia de una persona: entradas, salidas, tardanzas,
    permisos/justificaciones y % de asistencia (días con entrada+salida)."""
    persona = supabase.table("personal").select("id,nombre").eq("id", id).execute()
    if not persona.data:
        return jsonify({"ok": False, "mensaje": "No se encontró el personal."}), 404

    # Config global de tolerancia / hora entrada (mientras exista)
    cfg = {}
    try:
        cfg_res = supabase.table("configuracion").select("*").limit(1).execute()
        if cfg_res.data:
            cfg = cfg_res.data[0]
    except Exception:
        pass
    hora_limite = (cfg.get("hora_entrada") or "08:00")
    tolerancia = int(cfg.get("tolerancia_min") or 0)
    try:
        h_lim, m_lim = map(int, hora_limite.split(":"))
        minutos_limite = h_lim * 60 + m_lim + tolerancia
    except Exception:
        minutos_limite = 8 * 60 + tolerancia

    registros = supabase.table("registros_asistencia").select(
        "id,tipo,fecha_hora,confianza"
    ).eq("personal_id", id).order("fecha_hora").execute().data or []

    justs = supabase.table("justificaciones").select(
        "id,estado,motivo,fecha_solicitud"
    ).eq("personal_id", id).execute().data or []

    por_dia = {}
    total_entradas = 0
    total_salidas = 0
    tardanzas = 0
    confianzas = []

    for r in registros:
        fh = r.get("fecha_hora") or ""
        dia = fh[:10]
        if dia not in por_dia:
            por_dia[dia] = {"entrada": None, "salida": None}
        if r["tipo"] == "entrada":
            total_entradas += 1
            if por_dia[dia]["entrada"] is None:
                por_dia[dia]["entrada"] = fh
            # Tardanza: solo la primera entrada del día
            try:
                hora = fh[11:16] if "T" in fh or len(fh) > 10 else (fh.split(" ")[1][:5] if " " in fh else "")
                if hora:
                    hh, mm = map(int, hora.split(":"))
                    if (hh * 60 + mm) > minutos_limite:
                        tardanzas += 1
            except Exception:
                pass
        elif r["tipo"] == "salida":
            total_salidas += 1
            if por_dia[dia]["salida"] is None:
                por_dia[dia]["salida"] = fh
        if r.get("confianza") is not None:
            confianzas.append(float(r["confianza"]))

    dias_completos = sum(1 for d in por_dia.values() if d["entrada"] and d["salida"])
    dias_con_registro = len(por_dia)
    # Faltas: días con solo salida o sin par completo no se cuentan como falta
    # real sin calendario laboral; usamos días con entrada incompleta como proxy débil.
    dias_incompletos = sum(1 for d in por_dia.values() if not (d["entrada"] and d["salida"]))

    permisos_total = len(justs)
    permisos_aprobados = sum(1 for j in justs if j.get("estado") == "aprobado")
    permisos_pendientes = sum(1 for j in justs if j.get("estado") == "pendiente")
    permisos_rechazados = sum(1 for j in justs if j.get("estado") == "rechazado")

    pct_asistencia = round((dias_completos / dias_con_registro) * 100, 1) if dias_con_registro else 0.0
    conf_promedio = round((sum(confianzas) / len(confianzas)) * 100, 1) if confianzas else 0.0

    return jsonify({
        "ok": True,
        "total_registros": len(registros),
        "total_entradas": total_entradas,
        "total_salidas": total_salidas,
        "tardanzas": tardanzas,
        "dias_con_registro": dias_con_registro,
        "dias_completos": dias_completos,
        "dias_incompletos": dias_incompletos,
        "porcentaje_asistencia": pct_asistencia,
        "confianza_promedio": conf_promedio,
        "permisos_total": permisos_total,
        "permisos_aprobados": permisos_aprobados,
        "permisos_pendientes": permisos_pendientes,
        "permisos_rechazados": permisos_rechazados,
    })


# ==================================================================
# Horarios
# ==================================================================
DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

@app.route("/api/personal/<id>/horarios", methods=["GET"])
@requiere_admin
def api_horarios_listar(id):
    try:
        res = supabase.table("horarios_bloques").select(
            "id,personal_id,dia_semana,hora_inicio,hora_fin,materia,aula,carrera,semestre,modalidad,tolerancia_min"
        ).eq("personal_id", id).order("dia_semana").order("hora_inicio").execute()
    except Exception:
        res = supabase.table("horarios_bloques").select(
            "id,personal_id,dia_semana,hora_inicio,hora_fin,materia,aula,modalidad,tolerancia_min"
        ).eq("personal_id", id).order("dia_semana").order("hora_inicio").execute()
    return jsonify(res.data or [])

@app.route("/api/personal/<id>/horarios", methods=["POST"])
@requiere_admin
def api_horarios_crear(id):
    data = request.get_json() or {}
    try:
        dia = int(data.get("dia_semana"))
        assert 0 <= dia <= 6
    except (TypeError, ValueError, AssertionError):
        return jsonify({"ok": False, "mensaje": "Día de la semana inválido."}), 400
    hora_inicio = (data.get("hora_inicio") or "").strip()
    hora_fin = (data.get("hora_fin") or "").strip()
    if not hora_inicio or not hora_fin:
        return jsonify({"ok": False, "mensaje": "Hora de inicio y fin son obligatorias."}), 400
    if hora_fin <= hora_inicio:
        return jsonify({"ok": False, "mensaje": "La hora de fin debe ser posterior a la de inicio."}), 400

    persona = supabase.table("personal").select("id").eq("id", id).execute()
    if not persona.data:
        return jsonify({"ok": False, "mensaje": "No se encontró el personal."}), 404

    existentes = supabase.table("horarios_bloques").select("id,hora_inicio,hora_fin") \
        .eq("personal_id", id).eq("dia_semana", dia).execute().data or []
    solapa = any(not (h["hora_fin"] <= hora_inicio or h["hora_inicio"] >= hora_fin) for h in existentes)
    if solapa:
        return jsonify({"ok": False, "mensaje": f"Ya existe un bloque que se cruza ese {DIAS_SEMANA[dia]}."}), 400

    fila = {
        "personal_id": id, "dia_semana": dia, "hora_inicio": hora_inicio, "hora_fin": hora_fin,
        "materia": data.get("materia"), "aula": data.get("aula"),
        "modalidad": data.get("modalidad", "presencial"), "tolerancia_min": int(data.get("tolerancia_min") or 5),
    }
    if data.get("carrera"):
        fila["carrera"] = data.get("carrera")
    if data.get("semestre"):
        try:
            fila["semestre"] = int(data.get("semestre"))
        except (TypeError, ValueError):
            fila["semestre"] = data.get("semestre")
    try:
        supabase.table("horarios_bloques").insert(fila).execute()
    except Exception as e:
        # Si faltan columnas carrera/semestre, reintentar sin ellas
        msg = str(e).lower()
        if "carrera" in msg or "semestre" in msg or "column" in msg:
            fila.pop("carrera", None)
            fila.pop("semestre", None)
            supabase.table("horarios_bloques").insert(fila).execute()
        else:
            raise
    return jsonify({"ok": True})

@app.route("/api/horarios/<bloque_id>", methods=["DELETE"])
@requiere_admin
def api_horarios_eliminar(bloque_id):
    supabase.table("horarios_bloques").delete().eq("id", bloque_id).execute()
    return jsonify({"ok": True})


# ------------------------------------------------------------------
# Importar horario con IA (Google Gemini — API gratuita / GEMINI_API_KEY)
# ------------------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL",
    "https://generativelanguage.googleapis.com/v1beta/openai",
)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

PROMPT_HORARIO = """Eres un extractor de horarios académicos. Analiza la imagen o el texto del horario
y devuelve SOLO un JSON válido (sin markdown) con esta forma exacta:
{"bloques":[{"dia_semana":0,"hora_inicio":"07:30","hora_fin":"12:45","materia":"Nombre del curso","aula":"A-101","carrera":"Diseño Gráfico Digital","semestre":1,"tolerancia_min":5}]}

Reglas:
- dia_semana: 0=lunes, 1=martes, 2=miércoles, 3=jueves, 4=viernes, 5=sábado, 6=domingo
- horas en formato HH:MM de 24 horas
- semestre solo 1, 2 o 3 si se puede inferir; si no, null
- tolerancia_min siempre 5 si no se indica
- no inventes bloques que no aparezcan
- si no hay datos claros: {"bloques":[]}
"""


def _llamar_gemini_horario(contenido_usuario, imagen_b64=None, mime="image/jpeg"):
    """Llama a Gemini (compatible OpenAI) y devuelve texto de respuesta."""
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "Falta GEMINI_API_KEY en el archivo .env. "
            "Consíguela gratis en Google AI Studio (aistudio.google.com) o itsfree.ai"
        )
    import urllib.request

    messages = [{"role": "system", "content": PROMPT_HORARIO}]
    if imagen_b64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": contenido_usuario or "Extrae todos los bloques de este horario."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{imagen_b64}"}},
            ],
        })
    else:
        messages.append({"role": "user", "content": contenido_usuario})

    body = json.dumps({
        "model": GEMINI_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_BASE_URL.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GEMINI_API_KEY}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _parsear_bloques_ia(texto):
    """Extrae JSON de la respuesta del modelo."""
    if not texto:
        return []
    texto = texto.strip()
    # Quitar fences ```json ... ```
    if "```" in texto:
        partes = texto.split("```")
        for p in partes:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                texto = p
                break
    # Buscar primer { ... último }
    i = texto.find("{")
    j = texto.rfind("}")
    if i >= 0 and j > i:
        texto = texto[i:j + 1]
    data = json.loads(texto)
    bloques = data.get("bloques") if isinstance(data, dict) else data
    if not isinstance(bloques, list):
        return []
    limpios = []
    for b in bloques:
        try:
            dia = int(b.get("dia_semana"))
            if not (0 <= dia <= 6):
                continue
            hi = str(b.get("hora_inicio") or "").strip()[:5]
            hf = str(b.get("hora_fin") or "").strip()[:5]
            if not hi or not hf or hf <= hi:
                continue
            limpios.append({
                "dia_semana": dia,
                "hora_inicio": hi,
                "hora_fin": hf,
                "materia": (b.get("materia") or None),
                "aula": (b.get("aula") or None),
                "carrera": (b.get("carrera") or None),
                "semestre": b.get("semestre"),
                "tolerancia_min": int(b.get("tolerancia_min") or 5),
                "modalidad": "presencial",
            })
        except Exception:
            continue
    return limpios


@app.route("/api/ia/estado", methods=["GET"])
def api_ia_estado():
    """Indica si la IA está configurada (sin exponer la key)."""
    return jsonify({
        "ok": True,
        "gemini_configurado": bool(GEMINI_API_KEY),
        "modelo": GEMINI_MODEL if GEMINI_API_KEY else None,
    })


@app.route("/api/personal/<id>/horarios/importar-ia", methods=["POST"])
@requiere_admin
def api_horarios_importar_ia(id):
    """Recibe imagen/PDF (base64) o texto y crea bloques con Gemini."""
    persona = supabase.table("personal").select("id,nombre").eq("id", id).execute()
    if not persona.data:
        return jsonify({"ok": False, "mensaje": "No se encontró el personal."}), 404

    data = request.get_json() or {}
    imagen = data.get("imagen") or data.get("archivo")  # dataURL o base64 puro
    texto_extra = (data.get("texto") or "").strip()
    reemplazar = bool(data.get("reemplazar"))

    mime = "image/jpeg"
    b64 = None
    if imagen:
        if isinstance(imagen, str) and imagen.startswith("data:"):
            # data:image/png;base64,....
            try:
                header, b64 = imagen.split(",", 1)
                if "image/" in header:
                    mime = header.split(";")[0].split(":")[1]
                elif "pdf" in header:
                    mime = "application/pdf"
            except ValueError:
                return jsonify({"ok": False, "mensaje": "Formato de archivo inválido."}), 400
        else:
            b64 = imagen

    if not b64 and not texto_extra:
        return jsonify({"ok": False, "mensaje": "Sube una imagen/PDF del horario o pega el texto."}), 400

    # PDF: Gemini OpenAI-compat a veces no acepta PDF; intentamos como imagen si el cliente mandó foto.
    if mime == "application/pdf" and b64:
        # Intentamos igualmente; si falla, el usuario puede subir captura de pantalla.
        pass

    try:
        raw = _llamar_gemini_horario(
            texto_extra or "Extrae todos los bloques de clase de este horario académico.",
            imagen_b64=b64,
            mime=mime if mime.startswith("image/") else "image/jpeg",
        )
        bloques = _parsear_bloques_ia(raw)
    except RuntimeError as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 503
    except Exception as e:
        print(f"[importar-ia] error: {type(e).__name__}: {e}")
        return jsonify({
            "ok": False,
            "mensaje": "La IA no pudo procesar el horario. Prueba con una captura (PNG/JPG) nítida. "
                       f"Detalle: {type(e).__name__}",
        }), 502

    if not bloques:
        return jsonify({"ok": False, "mensaje": "No se detectaron bloques. Revisa la imagen o agrega manualmente."}), 422

    if reemplazar:
        try:
            supabase.table("horarios_bloques").delete().eq("personal_id", id).execute()
        except Exception as e:
            print(f"[importar-ia] no se pudo limpiar bloques: {e}")

    insertados = 0
    errores = []
    for b in bloques:
        fila = {
            "personal_id": id,
            "dia_semana": b["dia_semana"],
            "hora_inicio": b["hora_inicio"],
            "hora_fin": b["hora_fin"],
            "materia": b.get("materia"),
            "aula": b.get("aula"),
            "modalidad": "presencial",
            "tolerancia_min": b.get("tolerancia_min") or 5,
        }
        if b.get("carrera"):
            fila["carrera"] = b["carrera"]
        if b.get("semestre") is not None and b.get("semestre") != "":
            try:
                fila["semestre"] = int(b["semestre"])
            except (TypeError, ValueError):
                pass
        try:
            supabase.table("horarios_bloques").insert(fila).execute()
            insertados += 1
        except Exception as e:
            # reintento sin columnas opcionales
            fila.pop("carrera", None)
            fila.pop("semestre", None)
            try:
                supabase.table("horarios_bloques").insert(fila).execute()
                insertados += 1
            except Exception as e2:
                errores.append(str(e2))

    return jsonify({
        "ok": True,
        "insertados": insertados,
        "total_detectados": len(bloques),
        "errores": errores[:5],
        "bloques": bloques,
    })


@app.route("/api/personal/<id>/horarios/pdf", methods=["GET"])
@requiere_admin
def api_horarios_pdf(id):
    """Exporta el horario semanal de una persona a un PDF simple."""
    persona = supabase.table("personal").select("nombre,tipo_personal,area").eq("id", id).execute()
    if not persona.data:
        return jsonify({"ok": False, "mensaje": "No encontrado"}), 404
    p = persona.data[0]
    bloques = supabase.table("horarios_bloques").select(
        "dia_semana,hora_inicio,hora_fin,materia,tolerancia_min"
    ).eq("personal_id", id).order("dia_semana").order("hora_inicio").execute().data or []

    # PDF mínimo sin dependencias externas (PDF 1.4 texto)
    lineas = [
        f"Horario — {p.get('nombre') or ''}",
        f"{p.get('tipo_personal') or ''} · {p.get('area') or ''}",
        "",
    ]
    for b in bloques:
        dia = DIAS_SEMANA[int(b["dia_semana"])] if b.get("dia_semana") is not None else "?"
        lineas.append(
            f"{dia}: {b.get('hora_inicio')} - {b.get('hora_fin')}  "
            f"{b.get('materia') or ''}  (tol. {b.get('tolerancia_min', 5)} min)"
        )
    if not bloques:
        lineas.append("(sin bloques registrados)")

    def pdf_escape(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    y = 750
    content_ops = ["BT", "/F1 12 Tf", "50 780 Td", f"({pdf_escape(lineas[0])}) Tj"]
    for ln in lineas[1:]:
        content_ops.append("0 -16 Td")
        content_ops.append(f"({pdf_escape(ln[:110])}) Tj")
    content_ops.append("ET")
    stream = "\n".join(content_ops).encode("latin-1", errors="replace")

    objects = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return send_file(
        io.BytesIO(bytes(out)),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"horario_{_slug(p.get('nombre') or 'personal')}.pdf",
    )


@app.route("/api/dashboard/stats", methods=["GET"])
@requiere_admin
def api_dashboard_stats():
    """Estadísticas para gráficos: asistencia por carrera y por día de la semana."""
    hoy = datetime.now()
    desde = (hoy - timedelta(days=30)).strftime("%Y-%m-%d")
    hasta = hoy.strftime("%Y-%m-%d") + " 23:59:59"
    try:
        filas = supabase.table("registros_asistencia").select(
            "tipo,fecha_hora,es_tardanza,personal(area,nombre)"
        ).gte("fecha_hora", desde).lte("fecha_hora", hasta).limit(5000).execute().data or []
    except Exception as e:
        return jsonify({"ok": False, "mensaje": str(e)}), 500

    por_carrera = {}
    por_dia = {i: {"entradas": 0, "salidas": 0, "tardanzas": 0} for i in range(7)}
    for f in filas:
        p = f.get("personal") or {}
        area = p.get("area") or "Sin carrera"
        if area not in por_carrera:
            por_carrera[area] = {"entradas": 0, "salidas": 0, "tardanzas": 0}
        if f.get("tipo") == "entrada":
            por_carrera[area]["entradas"] += 1
        else:
            por_carrera[area]["salidas"] += 1
        if f.get("es_tardanza"):
            por_carrera[area]["tardanzas"] += 1
        try:
            fh = f.get("fecha_hora") or ""
            dt = datetime.fromisoformat(fh.replace("Z", "+00:00").replace(" ", "T")[:19])
            d = dt.weekday()
            if f.get("tipo") == "entrada":
                por_dia[d]["entradas"] += 1
            else:
                por_dia[d]["salidas"] += 1
            if f.get("es_tardanza"):
                por_dia[d]["tardanzas"] += 1
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "por_carrera": por_carrera,
        "por_dia": [
            {"dia": DIAS_SEMANA[i], **por_dia[i]} for i in range(7)
        ],
        "rango": {"desde": desde[:10], "hasta": hasta[:10]},
    })


# ==================================================================
# Admin: login / configuracion / password
# ==================================================================
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    ip = request.remote_addr or "desconocida"
    ahora = time.time()
    with _lock_login:
        fallos, ultimo = _intentos_login.get(ip, (0, 0.0))
        if fallos >= MAX_INTENTOS_LOGIN and ahora - ultimo < BLOQUEO_LOGIN_SEGUNDOS:
            restante = int(BLOQUEO_LOGIN_SEGUNDOS - (ahora - ultimo))
            return jsonify({"ok": False, "mensaje": f"Demasiados intentos fallidos. Espera {restante // 60 + 1} minuto(s)."}), 429

    data = request.get_json()
    res = supabase.table("admin_config").select("password_hash").eq("id", 1).execute()
    row = res.data[0] if res.data else None
    if row and check_password_hash(row["password_hash"], data.get("password", "")):
        with _lock_login:
            _intentos_login.pop(ip, None)
        session.permanent = True
        session["is_admin"] = True
        return jsonify({"ok": True})

    with _lock_login:
        fallos, _ = _intentos_login.get(ip, (0, 0.0))
        _intentos_login[ip] = (fallos + 1, ahora)
    return jsonify({"ok": False}), 401

@app.route("/api/admin/logout", methods=["POST"])
def api_admin_logout():
    session.pop("is_admin", None)
    return jsonify({"ok": True})

@app.route("/api/admin/diagnostico")
@requiere_admin
def api_admin_diagnostico():
    try:
        supabase.table("admin_config").select("id").eq("id", 1).limit(1).execute()
        bd_ok = True
    except Exception:
        bd_ok = False
    try:
        import cv2  # noqa: F401
        camara_ok = True
    except Exception:
        camara_ok = False
    res = supabase.table("admin_config").select("ultimo_backup").eq("id", 1).execute()
    ultimo_backup = res.data[0]["ultimo_backup"] if res.data else None
    return jsonify({"camara": camara_ok, "base_datos": bd_ok, "modelo": DEEPFACE_OK, "ultimo_backup": ultimo_backup})

@app.route("/api/admin/reset-db", methods=["POST"])
@requiere_admin
def api_admin_reset_db():
    data = request.get_json()
    password = data.get("password", "")
    res = supabase.table("admin_config").select("password_hash").eq("id", 1).execute()
    row = res.data[0] if res.data else None
    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"ok": False, "mensaje": "Contraseña incorrecta."}), 403
    try:
        # Trae las fotos antes de borrar los registros, para poder limpiarlas del Storage.
        personal_actual = supabase.table("personal").select("foto_path,icono_path").execute().data or []
        supabase.table("justificaciones").delete().neq("id", 0).execute()
        supabase.table("registros_asistencia").delete().neq("id", 0).execute()
        supabase.table("personal").delete().neq("id", 0).execute()
    except Exception as e:
        print(f"[api_admin_reset_db] error al formatear la base de datos: {type(e).__name__}: {e}")
        return jsonify({"ok": False, "mensaje": "Error al formatear."}), 500
    for p in personal_actual:
        eliminar_foto_storage(p.get("foto_path"))
        eliminar_foto_storage(p.get("icono_path"))
    invalidar_cache_personal()
    return jsonify({"ok": True, "mensaje": "Base de datos formateada correctamente."})

@app.route("/api/configuracion")
def api_configuracion():
    cfg = cargar_config()
    return jsonify({"intervalo_escaneo": cfg["intervalo_escaneo"], "hora_entrada": cfg["hora_entrada"],
                     "hora_salida": cfg["hora_salida"], "tolerancia_min": cfg["tolerancia_min"]})

@app.route("/api/admin/configuracion")
@requiere_admin
def api_admin_configuracion_get():
    return jsonify(cargar_config())

@app.route("/api/admin/configuracion", methods=["POST"])
@requiere_admin
def api_admin_configuracion_set():
    data = request.get_json() or {}
    actual = cargar_config()
    try:
        umbral = float(data.get("umbral_confianza", actual["umbral_confianza"]))
        intervalo = float(data.get("intervalo_escaneo", actual["intervalo_escaneo"]))
        reescaneo = int(data.get("tiempo_reescaneo", actual["tiempo_reescaneo"]))
        tolerancia = int(data.get("tolerancia_min", actual["tolerancia_min"]))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "mensaje": "Valores numéricos inválidos."}), 400
    if not (0 < umbral <= 1): return jsonify({"ok": False, "mensaje": "El umbral debe estar entre 0 y 1."}), 400
    if intervalo < 1: return jsonify({"ok": False, "mensaje": "El intervalo mínimo es 1 segundo."}), 400
    if reescaneo < 1: return jsonify({"ok": False, "mensaje": "El tiempo de reescaneo mínimo es 1 segundo."}), 400
    hora_entrada = data.get("hora_entrada", actual["hora_entrada"])
    hora_salida = data.get("hora_salida", actual["hora_salida"])

    supabase.table("admin_config").update({
        "umbral_confianza": umbral, "intervalo_escaneo": intervalo, "tiempo_reescaneo": reescaneo,
        "hora_entrada": hora_entrada, "hora_salida": hora_salida, "tolerancia_min": tolerancia,
    }).eq("id", 1).execute()
    invalidar_config()
    return jsonify({"ok": True})

@app.route("/api/admin/change-password", methods=["POST"])
@requiere_admin
def api_admin_change_password():
    data = request.get_json() or {}
    actual = data.get("password_actual", "")
    nueva = data.get("password_nueva", "")
    if len(nueva) < 6:
        return jsonify({"ok": False, "mensaje": "La nueva contraseña debe tener al menos 6 caracteres."}), 400
    res = supabase.table("admin_config").select("password_hash").eq("id", 1).execute()
    row = res.data[0] if res.data else None
    if not row or not check_password_hash(row["password_hash"], actual):
        return jsonify({"ok": False, "mensaje": "La contraseña actual es incorrecta."}), 403
    supabase.table("admin_config").update({"password_hash": generate_password_hash(nueva)}).eq("id", 1).execute()
    return jsonify({"ok": True, "mensaje": "Contraseña actualizada correctamente."})


@app.route("/api/admin/estado-sync")
@requiere_admin
def api_admin_estado_sync():
    """El frontend puede llamar a esto para mostrar un avisito tipo 'sin conexion,
    3 registros pendientes de subir' en el panel admin."""
    return jsonify({"pendientes": contar_pendientes()})


# ==================================================================
# NUEVO: aviso de "liberar espacio" cada 30 dias
# ==================================================================
@app.route("/api/admin/verificar-espacio")
@requiere_admin
def api_admin_verificar_espacio():
    """El panel admin debe llamar a esto al cargar (por ejemplo cada vez que se abre
    /admin) para saber si mostrar el aviso de 'han pasado 30 días, exporta y libera
    espacio'. Solo avisa si además hay registros de asistencia acumulados -- si no
    hay nada que exportar, no tiene sentido molestar con la notificación."""
    res = supabase.table("admin_config").select("ultimo_backup").eq("id", 1).execute()
    ultimo_backup = res.data[0]["ultimo_backup"] if res.data else None
    dias = None
    if ultimo_backup:
        try:
            fecha = datetime.fromisoformat(ultimo_backup.replace("Z", "+00:00"))
            dias = (datetime.now(timezone.utc) - fecha).days
        except ValueError:
            dias = None

    total = supabase.table("registros_asistencia").select("id", count="exact").execute().count or 0
    debe_avisar = total > 0 and (dias is None or dias >= DIAS_ANTES_DE_AVISAR)
    return jsonify({"debe_avisar": debe_avisar, "dias_desde_ultimo_backup": dias, "total_registros": total})

@app.route("/api/admin/exportar-y-liberar", methods=["POST"])
@requiere_admin
def api_admin_exportar_y_liberar():
    """Exporta TODOS los registros de asistencia a un CSV descargable. Si el
    frontend manda confirmar_borrado=true (el admin ya confirmo, tras descargar el
    CSV, que quiere liberar espacio), borra de Supabase los registros con mas de
    30 dias de antiguedad -- nunca borra sin haber generado antes el CSV con esos
    mismos datos."""
    data = request.get_json(silent=True) or {}
    confirmar_borrado = bool(data.get("confirmar_borrado"))

    filas = supabase.table("registros_asistencia").select(
        "id,tipo,fecha_hora,confianza,personal(nombre,dni)"
    ).order("fecha_hora").execute().data or []

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["nombre", "dni", "tipo", "fecha_hora", "confianza"])
    for f in filas:
        p = f.get("personal") or {}
        writer.writerow([p.get("nombre", ""), p.get("dni", ""), f["tipo"], f["fecha_hora"], f.get("confianza", "")])
    contenido = buffer.getvalue().encode("utf-8-sig")

    ahora_iso = datetime.now(timezone.utc).isoformat()
    supabase.table("admin_config").update({"ultimo_backup": ahora_iso}).eq("id", 1).execute()

    if confirmar_borrado:
        limite = (datetime.now(timezone.utc) - timedelta(days=DIAS_ANTES_DE_AVISAR)).strftime("%Y-%m-%d %H:%M:%S")
        antiguos = [f["id"] for f in filas if f["fecha_hora"] < limite]
        if antiguos:
            supabase.table("registros_asistencia").delete().in_("id", antiguos).execute()

    return send_file(
        io.BytesIO(contenido), mimetype="text/csv", as_attachment=True,
        download_name=f"asistencias_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
    )


# ==================================================================
init_queue_db()
threading.Thread(target=sincronizador_loop, daemon=True).start()
verificar_admin_inicial()

if DEEPFACE_OK:
    try:
        print("Precargando modelo de reconocimiento facial (esto puede tardar unos segundos)...")
        _frame_calentamiento = np.zeros((160, 160, 3), dtype=np.uint8)
        DeepFace.represent(_frame_calentamiento, model_name=MODEL_NAME, detector_backend="skip", enforce_detection=False)
        cargar_personal_activo()
        print("Modelo cargado. El primer registro/reconocimiento ya no debería demorar.")
    except Exception as e:
        print("No se pudo precargar el modelo (se cargará en la primera solicitud):", e)

if __name__ == "__main__":
    if DEBUG_MODE:
        print("AVISO: modo debug activo (FOCUS_DEBUG=1). No usar esta configuración en producción.")
    # host="0.0.0.0": permite que otros dispositivos en la misma red local (celulares,
    # otras laptops) se conecten usando la IP de esta PC, ej. http://192.168.1.x:5000
    app.run(host="0.0.0.0", debug=DEBUG_MODE, port=5000, threaded=True)