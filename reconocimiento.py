import cv2
import numpy as np
import sqlite3
import pickle
import os
from datetime import datetime
from scipy.spatial.distance import cosine

try:
    from deepface import DeepFace
except ImportError:
    print("Error: deepface no esta instalado")
    print("   Instalalo con: pip install deepface")
    exit(1)

DB_PATH = "asistencia.db"
MODEL_NAME = "Facenet"
DETECTOR_BACKEND = "opencv"
UMBRAL_CONFIANZA = 0.40

def conectar_db():
    return sqlite3.connect(DB_PATH)

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
        representation = DeepFace.represent(foto, model_name=MODEL_NAME, detector_backend=DETECTOR_BACKEND, enforce_detection=True)
        if len(representation) > 0:
            return np.array(representation[0]["embedding"])
        return None
    except Exception as e:
        print("Error extrayendo embedding:", e)
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
    sql = "INSERT INTO registros_asistencia (personal_id, tipo, confianza) VALUES (?, ?, ?)"
    cursor.execute(sql, (personal_id, tipo, confianza))
    conn.commit()
    conn.close()
    print("Asistencia registrada:", tipo)

def obtener_ultimo_registro(personal_id):
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("SELECT tipo FROM registros_asistencia WHERE personal_id = ? ORDER BY fecha_hora DESC LIMIT 1", (personal_id,))
    resultado = cursor.fetchone()
    conn.close()
    return resultado[0] if resultado else None

def modo_reconocimiento():
    print("=" * 60)
    print("  MODO RECONOCIMIENTO - SENATI")
    print("  Registro de Entrada/Salida")
    print("=" * 60)

    personal = cargar_personal()
    print("Personal activo registrado:", len(personal))

    if len(personal) == 0:
        print("No hay personal registrado. Ejecuta registrar_personal.py primero.")
        return

    print("\nPresiona ESPACIO para registrar entrada/salida")
    print("Presiona Q para salir")
    print("Esperando rostro...\n")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: No se pudo abrir la camara")
        return

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        cv2.putText(frame, "Sistema de Asistencia - SENATI", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, "ESPACIO = Registrar | Q = Salir", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.imshow("Reconocimiento Facial - SENATI", frame)

        key = cv2.waitKey(1) & 0xFF

        if key == 32:
            print("\nCapturando rostro...")
            embedding = extraer_embedding(frame)

            if embedding is None:
                print("No se detecto rostro. Intenta de nuevo.")
                continue

            p, distancia = identificar_rostro(embedding, personal)

            if p is None or distancia > UMBRAL_CONFIANZA:
                print("Rostro no reconocido. Intenta de nuevo.")
                print("Distancia:", round(distancia, 3), "(umbral:", UMBRAL_CONFIANZA, ")")
                continue

            confianza = round(1 - distancia, 3)
            print("Rostro identificado:", p["nombre"])
            print("Tipo:", p["tipo"].upper(), "| Area:", p["area"], "| Cargo:", p["cargo"])
            print("Confianza:", confianza * 100, "%")

            ultimo = obtener_ultimo_registro(p["id"])
            if ultimo is None or ultimo == "salida":
                tipo = "entrada"
            else:
                tipo = "salida"

            registrar_asistencia(p["id"], tipo, confianza)
            print("Registro:", tipo.upper(), "-", p["nombre"])
            print("-" * 40)

        elif key == ord("q"):
            print("\nSaliendo del modo reconocimiento...")
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    modo_reconocimiento()
