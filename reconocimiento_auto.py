import os
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import warnings
warnings.filterwarnings("ignore")

import logging
logging.getLogger("tensorflow").setLevel(logging.FATAL)
logging.getLogger("absl").setLevel(logging.FATAL)

import cv2
import numpy as np
import sqlite3
import pickle
import platform
from datetime import datetime
from scipy.spatial.distance import cosine
import time

try:
    from deepface import DeepFace
except ImportError:
    print("Error: deepface no esta instalado")
    exit(1)

DB_PATH = "asistencia.db"
MODEL_NAME = "Facenet"
DETECTOR_BACKEND = "opencv"
UMBRAL_CONFIANZA = 0.40
INTERVALO_DETECCION = 2
TIEMPO_ESPERA = 4  # candado de reescaneo: una misma persona no se vuelve a reconocer antes de 4s

def conectar_db():
    return sqlite3.connect(DB_PATH)

def inicializar_bd():
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS personal (id INTEGER PRIMARY KEY AUTOINCREMENT, dni TEXT UNIQUE, nombre TEXT NOT NULL, tipo_personal TEXT CHECK(tipo_personal IN ('maestro', 'personal', 'jefe')), area TEXT, curso TEXT, cargo TEXT, embedding BLOB NOT NULL, foto_path TEXT, activo BOOLEAN DEFAULT 1, fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS registros_asistencia (id INTEGER PRIMARY KEY AUTOINCREMENT, personal_id INTEGER NOT NULL, tipo TEXT CHECK(tipo IN ('entrada', 'salida')), fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP, confianza REAL, FOREIGN KEY (personal_id) REFERENCES personal(id))")
    conn.commit()
    conn.close()

def cargar_personal():
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, nombre, tipo_personal, area, cargo, embedding FROM personal WHERE activo = 1")
    personal = []
    for row in cursor.fetchall():
        personal.append({
            "id": row[0],
            "nombre": row[1],
            "tipo": row[2],
            "area": row[3],
            "cargo": row[4],
            "embedding": pickle.loads(row[5])
        })
    conn.close()
    return personal

def extraer_embedding(foto):
    try:
        representation = DeepFace.represent(
            foto, 
            model_name=MODEL_NAME, 
            detector_backend=DETECTOR_BACKEND,
            enforce_detection=True
        )
        if len(representation) > 0:
            return np.array(representation[0]["embedding"])
        return None
    except Exception:
        return None

def identificar_rostro(embedding_captura, personal):
    if embedding_captura is None or len(personal) == 0:
        return None, 1.0
    mejor_match = None
    mejor_distancia = float("inf")
    for p in personal:
        distancia = cosine(embedding_captura, p["embedding"])
        if distancia < mejor_distancia:
            mejor_distancia = distancia
            mejor_match = p
    return mejor_match, mejor_distancia

def registrar_asistencia(personal_id, tipo, confianza):
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO registros_asistencia (personal_id, tipo, confianza) VALUES (?, ?, ?)",
        (personal_id, tipo, confianza)
    )
    conn.commit()
    conn.close()

def obtener_ultimo_registro(personal_id):
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT tipo FROM registros_asistencia WHERE personal_id = ? ORDER BY fecha_hora DESC LIMIT 1",
        (personal_id,)
    )
    resultado = cursor.fetchone()
    conn.close()
    return resultado[0] if resultado else None

def sonido_exito():
    if platform.system() == "Windows":
        try:
            import winsound
            winsound.Beep(1000, 300)
            winsound.Beep(1500, 200)
        except:
            pass

def main():
    inicializar_bd()

    print("=" * 60)
    print("  SISTEMA DE ASISTENCIA - SENATI")
    print("  Cámara SIEMPRE ACTIVA - Reconocimiento Automático")
    print("=" * 60)
    print("\n  - Acercate a la camara para registrar entrada/salida")
    print("  - El sistema detecta automaticamente tu rostro")
    print("  - Presiona Q para salir\n")

    personal = cargar_personal()
    print(f"Personal activo registrado: {len(personal)}")

    if len(personal) == 0:
        print("\nNo hay personal registrado.")
        print("Ejecuta: python registrar_personal.py")
        return

    print("\nIniciando camara...")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: No se pudo abrir la camara")
        return

    ultimo_reconocimiento = {}
    estado = "Esperando rostro..."
    color_estado = (255, 255, 0)
    tiempo_mostrar_estado = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        ahora = time.time()

        cv2.putText(frame, "SISTEMA DE ASISTENCIA - SENATI", (10, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, estado, (10, 60), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_estado, 2)
        cv2.putText(frame, "Acercate a la camara | Q = Salir", (10, 90), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        h, w = frame.shape[:2]
        cx, cy = w // 2, h // 2
        cv2.rectangle(frame, (cx - 150, cy - 200), (cx + 150, cy + 200), (0, 255, 0), 2)
        cv2.putText(frame, "Zona de deteccion", (cx - 150, cy - 210), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        if ahora - tiempo_mostrar_estado > INTERVALO_DETECCION:
            embedding = extraer_embedding(frame)

            if embedding is not None:
                p, distancia = identificar_rostro(embedding, personal)

                if p is not None and distancia <= UMBRAL_CONFIANZA:
                    personal_id = p["id"]
                    ultimo_tiempo = ultimo_reconocimiento.get(personal_id, 0)

                    if ahora - ultimo_tiempo > TIEMPO_ESPERA:
                        confianza = round(1 - distancia, 3)
                        ultimo = obtener_ultimo_registro(personal_id)

                        if ultimo is None or ultimo == "salida":
                            tipo = "entrada"
                        else:
                            tipo = "salida"

                        registrar_asistencia(personal_id, tipo, confianza)
                        ultimo_reconocimiento[personal_id] = ahora

                        estado = f"OK {tipo.upper()}: {p['nombre']}"
                        color_estado = (0, 255, 0)
                        tiempo_mostrar_estado = ahora
                        sonido_exito()

                        print(f"[{datetime.now().strftime('%H:%M:%S')}] {tipo.upper()}: {p['nombre']}")
                    else:
                        segundos = int(TIEMPO_ESPERA - (ahora - ultimo_tiempo))
                        estado = f"Espera {segundos}s..."
                        color_estado = (255, 165, 0)
                        tiempo_mostrar_estado = ahora
                else:
                    estado = "Rostro no reconocido"
                    color_estado = (0, 0, 255)
                    tiempo_mostrar_estado = ahora
            else:
                estado = "Esperando rostro..."
                color_estado = (255, 255, 0)

        cv2.imshow("Asistencia SENATI - Automatico", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            print("\nSaliendo...")
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()