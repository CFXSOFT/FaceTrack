"""
Herramienta para juntar ejemplos de entrenamiento del liveness.

Abre la cámara en una ventana aparte (no depende de la web ni de Flask).
Con cada foto que se guarda, ya se recorta el rostro con el mismo detector
que usa el sistema, así el ejemplo queda igual de "limpio" que lo que
recibe app.py en producción.

Controles:
  R -> guardar el frame actual como REAL   (tu cara, tal cual)
  F -> guardar el frame actual como FALSO  (la foto/celular que uses para probar)
  Q -> salir

Recomendado: al menos 40-60 de cada tipo. Para "real" varía luz, ángulo,
distancia y con/sin lentes si corresponde. Para "falso" probá con distintos
celulares, fotos impresas, y pantallas, a distintas distancias.

Uso:
    python capturar_ejemplos.py
"""
import os

import cv2
from deepface import DeepFace

CARPETA_REAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_liveness", "real")
CARPETA_FALSO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset_liveness", "falso")
DETECTOR_BACKEND = "yunet"  # mismo detector que usa app.py


def siguiente_nombre(carpeta):
    os.makedirs(carpeta, exist_ok=True)
    existentes = len(os.listdir(carpeta))
    return os.path.join(carpeta, f"{existentes:04d}.jpg")


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("No se pudo abrir la cámara (¿está en uso por otra ventana de FaceTrack?).")
        return

    print("R = guardar REAL | F = guardar FALSO | Q = salir")
    contador_real, contador_falso = 0, 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        texto = f"real: {contador_real}   falso: {contador_falso}"
        cv2.putText(frame, texto, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("Captura de ejemplos - liveness", frame)

        tecla = cv2.waitKey(1) & 0xFF
        if tecla in (ord('r'), ord('f')):
            try:
                caras = DeepFace.extract_faces(frame, detector_backend=DETECTOR_BACKEND, enforce_detection=True)
            except ValueError:
                print("No se detectó ningún rostro en este frame, no se guardó nada.")
                continue

            recorte_rgb = (caras[0]["face"] * 255).astype("uint8")
            recorte_bgr = cv2.cvtColor(recorte_rgb, cv2.COLOR_RGB2BGR)

            if tecla == ord('r'):
                destino = siguiente_nombre(CARPETA_REAL)
                contador_real += 1
            else:
                destino = siguiente_nombre(CARPETA_FALSO)
                contador_falso += 1

            cv2.imwrite(destino, recorte_bgr)
            print(f"Guardado: {destino}")
        elif tecla == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"\nTotal -> real: {contador_real}  falso: {contador_falso}")
    print("Cuando tengas suficientes de cada uno, corré: python entrenar_liveness.py")


if __name__ == "__main__":
    main()