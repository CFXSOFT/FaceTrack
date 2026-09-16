"""
Entrena el clasificador de liveness a partir de dataset_liveness/real y
dataset_liveness/falso (generados con capturar_ejemplos.py) y guarda
modelo_liveness.pkl, que app.py carga automáticamente en el próximo arranque
si el archivo existe.

Requiere: pip install scikit-learn joblib

Uso:
    python entrenar_liveness.py
"""
import os

import cv2
import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

from liveness_ml import NOMBRES_CARACTERISTICAS, extraer_caracteristicas

CARPETA_REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_liveness", "real")
CARPETA_FALSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_liveness", "falso")
MINIMO_POR_CLASE = 15  # con menos que esto el modelo no va a generalizar nada


def cargar_carpeta(carpeta, etiqueta):
    X, y = [], []
    if not os.path.isdir(carpeta):
        return X, y
    for nombre in sorted(os.listdir(carpeta)):
        ruta = os.path.join(carpeta, nombre)
        img_bgr = cv2.imread(ruta)
        if img_bgr is None:
            continue
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        X.append(extraer_caracteristicas(img_rgb))
        y.append(etiqueta)
    return X, y


def main():
    X_real, y_real = cargar_carpeta(CARPETA_REAL, "real")
    X_falso, y_falso = cargar_carpeta(CARPETA_FALSO, "falso")
    print(f"Ejemplos reales: {len(X_real)}  |  Ejemplos falsos: {len(X_falso)}")

    if len(X_real) < MINIMO_POR_CLASE or len(X_falso) < MINIMO_POR_CLASE:
        print(f"Necesitás al menos {MINIMO_POR_CLASE} ejemplos de cada tipo (idealmente 40+) "
              f"para que el modelo sirva de algo. Corré capturar_ejemplos.py un poco más.")
        return

    X = np.array(X_real + X_falso)
    y = np.array(y_real + y_falso)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, stratify=y, random_state=42
    )

    modelo = RandomForestClassifier(n_estimators=200, class_weight="balanced", random_state=42)
    modelo.fit(X_train, y_train)

    print("\n--- Resultado sobre datos de prueba (que el modelo no vio al entrenar) ---")
    print(classification_report(y_test, modelo.predict(X_test)))

    print("--- Qué tanto pesa cada característica para distinguir real de falso ---")
    for nombre, peso in sorted(zip(NOMBRES_CARACTERISTICAS, modelo.feature_importances_), key=lambda x: -x[1]):
        print(f"  {nombre}: {peso:.3f}")

    destino = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelo_liveness.pkl")
    joblib.dump(modelo, destino)
    print(f"\nGuardado en {destino}")
    print("Reiniciá app.py (Ctrl+C y volver a correr python app.py) para que empiece a usarlo.")


if __name__ == "__main__":
    main()