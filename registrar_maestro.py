import cv2
import numpy as np
import sqlite3
import pickle
import os
from datetime import datetime

# Intentar importar deepface
try:
    from deepface import DeepFace
except ImportError:
    print("Error: deepface no esta instalado")
    print("   Instalalo con: pip install deepface")
    exit(1)

# Configuracion
DB_PATH = "asistencia.db"
FOTOS_DIR = "fotos_maestros"
MODEL_NAME = "Facenet"
DETECTOR_BACKEND = "opencv"

def conectar_db():
    return sqlite3.connect(DB_PATH)

def capturar_foto(nombre_maestro):
    print("Preparando captura para:", nombre_maestro)
    print("Presiona ESPACIO para capturar, Q para salir")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: No se pudo abrir la camara")
        return None

    foto = None
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        cv2.putText(frame, "Registrando: " + nombre_maestro, (10, 30), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, "ESPACIO = Capturar | Q = Salir", (10, 60), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.imshow("Registro de Maestro", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == 32:
            foto = frame.copy()
            print("Foto capturada")
            break
        elif key == ord("q"):
            print("Captura cancelada")
            break

    cap.release()
    cv2.destroyAllWindows()
    return foto

def extraer_embedding(foto):
    try:
        representation = DeepFace.represent(
            foto, 
            model_name=MODEL_NAME, 
            detector_backend=DETECTOR_BACKEND,
            enforce_detection=True
        )

        if len(representation) > 0:
            embedding = np.array(representation[0]["embedding"])
            print("Embedding extraido:", len(embedding), "dimensiones")
            return embedding
        else:
            print("No se detecto ningun rostro en la imagen")
            return None

    except Exception as e:
        print("Error extrayendo embedding:", e)
        return None

def guardar_maestro(nombre, departamento, embedding, foto):
    conn = conectar_db()
    cursor = conn.cursor()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    foto_filename = nombre.replace(" ", "_") + "_" + timestamp + ".jpg"
    foto_path = os.path.join(FOTOS_DIR, foto_filename)
    cv2.imwrite(foto_path, foto)

    embedding_blob = pickle.dumps(embedding)

    sql = "INSERT INTO maestros (nombre, departamento, embedding, foto_path) VALUES (?, ?, ?, ?)"
    cursor.execute(sql, (nombre, departamento, embedding_blob, foto_path))

    maestro_id = cursor.lastrowid
    conn.commit()
    conn.close()

    print("Maestro registrado exitosamente")
    print("   ID:", maestro_id)
    print("   Nombre:", nombre)
    print("   Departamento:", departamento)
    print("   Foto guardada en:", foto_path)

    return maestro_id

def registrar_maestro():
    print("=" * 50)
    print("REGISTRO DE NUEVO MAESTRO")
    print("=" * 50)

    nombre = input("\nNombre del maestro: ").strip()
    if not nombre:
        print("El nombre es obligatorio")
        return

    departamento = input("Departamento: ").strip()

    print("\nRegistrando:", nombre)
    print("Se abrira la camara para capturar tu foto...")
    print("(Asegurate de estar en un lugar bien iluminado)\n")

    foto = capturar_foto(nombre)
    if foto is None:
        print("No se pudo capturar la foto")
        return

    print("\nExtrayendo caracteristicas faciales...")
    embedding = extraer_embedding(foto)
    if embedding is None:
        print("No se pudo extraer el embedding. Intenta de nuevo.")
        return

    print("\nGuardando en la base de datos...")
    maestro_id = guardar_maestro(nombre, departamento, embedding, foto)

    print("\n" + "=" * 50)
    print("Registro completado!")
    print("=" * 50)

if __name__ == "__main__":
    os.makedirs(FOTOS_DIR, exist_ok=True)

    print("Sistema de Registro de Maestros - DeepFace")
    print("=" * 50)

    while True:
        registrar_maestro()

        continuar = input("\nRegistrar otro maestro? (s/n): ").strip().lower()
        if continuar != "s":
            print("\nHasta luego!")
            break
