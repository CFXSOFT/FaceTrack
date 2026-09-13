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
import os
from datetime import datetime

try:
    from deepface import DeepFace
except ImportError:
    print("Error: deepface no esta instalado")
    print("   Instalalo con: pip install deepface")
    exit(1)

DB_PATH = "asistencia.db"
FOTOS_DIR = "fotos_personal"
MODEL_NAME = "Facenet"
DETECTOR_BACKEND = "opencv"

def conectar_db():
    return sqlite3.connect(DB_PATH)

def inicializar_bd():
    conn = conectar_db()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS personal (id INTEGER PRIMARY KEY AUTOINCREMENT, dni TEXT UNIQUE, nombre TEXT NOT NULL, tipo_personal TEXT CHECK(tipo_personal IN ('maestro', 'personal', 'jefe')), area TEXT, curso TEXT, cargo TEXT, embedding BLOB NOT NULL, foto_path TEXT, activo BOOLEAN DEFAULT 1, fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS registros_asistencia (id INTEGER PRIMARY KEY AUTOINCREMENT, personal_id INTEGER NOT NULL, tipo TEXT CHECK(tipo IN ('entrada', 'salida')), fecha_hora DATETIME DEFAULT CURRENT_TIMESTAMP, confianza REAL, FOREIGN KEY (personal_id) REFERENCES personal(id))")
    conn.commit()
    conn.close()

def capturar_foto(nombre_personal):
    print("Preparando captura para:", nombre_personal)
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
        cv2.putText(frame, "Registrando: " + nombre_personal, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.putText(frame, "ESPACIO = Capturar | Q = Salir", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.imshow("Registro de Personal - SENATI", frame)
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
        representation = DeepFace.represent(foto, model_name=MODEL_NAME, detector_backend=DETECTOR_BACKEND, enforce_detection=True)
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

def guardar_personal(dni, nombre, tipo, area, curso, cargo, embedding, foto):
    conn = conectar_db()
    cursor = conn.cursor()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    foto_filename = nombre.replace(" ", "_") + "_" + timestamp + ".jpg"
    foto_path = os.path.join(FOTOS_DIR, foto_filename)
    cv2.imwrite(foto_path, foto)
    embedding_blob = pickle.dumps(embedding)
    sql = "INSERT INTO personal (dni, nombre, tipo_personal, area, curso, cargo, embedding, foto_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    cursor.execute(sql, (dni, nombre, tipo, area, curso, cargo, embedding_blob, foto_path))
    personal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print("Personal registrado exitosamente")
    print("   ID:", personal_id)
    print("   DNI:", dni)
    print("   Nombre:", nombre)
    print("   Tipo:", tipo.upper())
    print("   Area:", area)
    print("   Curso:", curso)
    print("   Cargo:", cargo)
    print("   Foto:", foto_path)
    return personal_id

def seleccionar_tipo():
    print("\n--- TIPO DE PERSONAL ---")
    print("1. Maestro (docente/instructor)")
    print("2. Personal (administrativo/operativo)")
    print("3. Jefe (coordinador/gerente)")
    while True:
        opcion = input("Selecciona (1-3): ").strip()
        if opcion == "1":
            return "maestro"
        elif opcion == "2":
            return "personal"
        elif opcion == "3":
            return "jefe"
        else:
            print("Opcion no valida. Intenta de nuevo.")

def solicitar_datos(tipo):
    print("\n--- DATOS DEL PERSONAL ---")
    dni = input("DNI (opcional, Enter para omitir): ").strip() or None
    nombre = input("Nombre completo: ").strip()
    if not nombre:
        print("El nombre es obligatorio")
        return None
    if tipo == "maestro":
        area = input("Area/Especialidad (ej: Mecatronica, Electricidad): ").strip()
        curso = input("Curso que dicta (ej: PLC Basico): ").strip()
        cargo = input("Cargo (ej: Instructor, Docente): ").strip() or "Instructor"
    elif tipo == "personal":
        area = input("Area/Departamento (ej: Administracion, RRHH): ").strip()
        curso = None
        cargo = input("Cargo (ej: Asistente, Tecnico): ").strip()
    elif tipo == "jefe":
        area = input("Area a su cargo (ej: Academica, Administrativa): ").strip()
        curso = None
        cargo = input("Cargo (ej: Coordinador, Gerente): ").strip()
    else:
        area = input("Area: ").strip()
        curso = None
        cargo = input("Cargo: ").strip()
    return {"dni": dni, "nombre": nombre, "tipo": tipo, "area": area, "curso": curso, "cargo": cargo}

def registrar_personal():
    print("=" * 60)
    print("  REGISTRO DE PERSONAL - SENATI")
    print("=" * 60)
    tipo = seleccionar_tipo()
    datos = solicitar_datos(tipo)
    if datos is None:
        return
    print("\nRegistrando:", datos["nombre"])
    print("Se abrira la camara para capturar tu foto...")
    print("(Asegurate de estar en un lugar bien iluminado)\n")
    foto = capturar_foto(datos["nombre"])
    if foto is None:
        print("No se pudo capturar la foto")
        return
    print("\nExtrayendo caracteristicas faciales...")
    embedding = extraer_embedding(foto)
    if embedding is None:
        print("No se pudo extraer el embedding. Intenta de nuevo.")
        return
    print("\nGuardando en la base de datos...")
    guardar_personal(datos["dni"], datos["nombre"], datos["tipo"], datos["area"], datos["curso"], datos["cargo"], embedding, foto)
    print("\n" + "=" * 60)
    print("  Registro completado!")
    print("=" * 60)

def menu_registro():
    inicializar_bd()
    print("\n" + "=" * 60)
    print("  SISTEMA DE REGISTRO DE PERSONAL - SENATI")
    print("=" * 60)
    while True:
        print("\n1. Registrar nuevo personal")
        print("2. Salir")
        opcion = input("\nElige una opcion: ").strip()
        if opcion == "1":
            registrar_personal()
        elif opcion == "2":
            print("\nHasta luego!")
            break
        else:
            print("Opcion no valida.")

if __name__ == "__main__":
    os.makedirs(FOTOS_DIR, exist_ok=True)
    menu_registro()
