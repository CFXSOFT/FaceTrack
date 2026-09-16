"""
Clasificador de liveness (rostro real vs. foto/pantalla) entrenado con
ejemplos propios -- reemplaza la dependencia del anti_spoofing interno de
DeepFace, que en este entorno (detector "yunet") fallaba en silencio.

No usa red neuronal: extrae unas pocas características simples y rápidas
del recorte de rostro (el mismo que ya entrega DeepFace.extract_faces, sin
volver a recortar nada) y las clasifica con un modelo liviano entrenado por
vos. Flujo completo:

  1. python capturar_ejemplos.py   -> juntás fotos de tu cara real y de la
                                       foto/celular que uses para probar
  2. python entrenar_liveness.py   -> entrena y guarda modelo_liveness.pkl
  3. app.py lo carga solo si existe modelo_liveness.pkl (si no existe, no
     bloquea nada -- se comporta como si esta capa no estuviera, igual que
     antes).
"""
import os

import cv2
import numpy as np

NOMBRES_CARACTERISTICAS = ["moire", "nitidez", "brillos", "bordes", "sat_media", "sat_std"]


def extraer_caracteristicas(cara_recorte):
    """cara_recorte: array RGB (float 0-1 u uint8), como el que devuelve
    DeepFace.extract_faces en caras[0]["face"], o una imagen leída con
    cv2.imread ya convertida a RGB."""
    img = cara_recorte
    if img.dtype != np.uint8:
        img = (np.clip(img, 0, 1) * 255).astype(np.uint8)

    if img.ndim == 3:
        gris = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        hsv = cv2.resize(hsv, (128, 128))
    else:
        gris = img
        hsv = None
    gris = cv2.resize(gris, (128, 128))

    # 1. Energía de alta frecuencia -- patrón de moiré típico de pantallas
    #    (la grilla de píxeles del celular/monitor interfiere con el sensor
    #    de la cámara y deja una firma de alta frecuencia que la piel no tiene).
    espectro = np.fft.fftshift(np.fft.fft2(gris.astype(np.float32)))
    magnitud = np.abs(espectro)
    h, w = magnitud.shape
    cy, cx = h // 2, w // 2
    Y, X = np.ogrid[:h, :w]
    dist = np.sqrt((Y - cy) ** 2 + (X - cx) ** 2)
    banda_alta = (dist > 10) & (dist < min(cy, cx) - 4)
    moire = float(magnitud[banda_alta].sum() / (magnitud.sum() + 1e-6))

    # 2. Nitidez (varianza del Laplaciano) -- impresiones/pantallas refotografiadas
    #    suelen perder algo de definición frente a piel real de cerca.
    nitidez = float(cv2.Laplacian(gris, cv2.CV_64F).var())

    # 3. Proporción de píxeles muy brillantes -- reflejos de vidrio/pantalla.
    brillos = float(np.mean(gris > 240))

    # 4. Densidad de bordes.
    bordes = float(np.mean(cv2.Canny(gris, 80, 160) > 0))

    # 5. Saturación media y su desviación -- el color que reproduce una
    #    pantalla suele diferir sutilmente del de la piel real bajo la misma luz.
    if hsv is not None:
        sat_media = float(hsv[:, :, 1].mean())
        sat_std = float(hsv[:, :, 1].std())
    else:
        sat_media = sat_std = 0.0

    return np.array([moire, nitidez, brillos, bordes, sat_media, sat_std], dtype=float)


_modelo = None
_modelo_cargado = False
_ruta_modelo_default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modelo_liveness.pkl")


def cargar_modelo(ruta=None):
    global _modelo, _modelo_cargado
    if _modelo_cargado:
        return _modelo
    _modelo_cargado = True
    ruta = ruta or _ruta_modelo_default
    try:
        import joblib
        if os.path.exists(ruta):
            _modelo = joblib.load(ruta)
            print(f"[liveness-ml] modelo cargado desde {ruta}")
        else:
            print(f"[liveness-ml] no se encontró {ruta} -- corré capturar_ejemplos.py "
                  f"y luego entrenar_liveness.py para activar esta capa.")
    except Exception as e:
        print(f"[liveness-ml] no se pudo cargar el modelo: {e}")
    return _modelo


def clasificar(cara_recorte, umbral_confianza=0.6):
    """Devuelve (es_real: bool, confianza_real: float), o None si el modelo
    todavía no está entrenado/disponible (en ese caso no se bloquea nada)."""
    modelo = cargar_modelo()
    if modelo is None:
        return None
    caracteristicas = extraer_caracteristicas(cara_recorte).reshape(1, -1)
    proba = modelo.predict_proba(caracteristicas)[0]
    clases = list(modelo.classes_)
    idx_real = clases.index("real") if "real" in clases else 0
    confianza_real = float(proba[idx_real])
    return confianza_real >= umbral_confianza, confianza_real