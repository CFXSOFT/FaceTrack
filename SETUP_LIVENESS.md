# Liveness (anti-spoofing) — cómo funciona y cómo re-entrenarlo

FaceTrack incluye una capa de liveness que rechaza fotos/pantallas mostradas
a la cámara en vez de un rostro real, antes de dejar pasar el reconocimiento.

## Si vienes de clonar el repositorio

Si `web/modelo_liveness.pkl` ya está incluido en el repo, no tienes que hacer
nada extra: `app.py` lo carga solo al arrancar. Vas a ver en la consola:

```
[liveness-ml] modelo cargado desde .../web/modelo_liveness.pkl
```

Si ese archivo **no** existe todavía en tu copia, el sistema sigue
funcionando igual (reconocimiento normal), simplemente sin esta verificación
extra, hasta que lo entrenes vos.

## Cómo (re)entrenarlo desde cero

1. Instala dependencias (ya están en `requirements.txt`):
   ```
   pip install -r requirements.txt
   ```
2. Desde la carpeta `web/`, corre:
   ```
   python capturar_ejemplos.py
   ```
   Con la cámara abierta: `R` guarda un ejemplo REAL (tu cara), `F` guarda un
   ejemplo FALSO (una foto/celular/pantalla mostrando un rostro), `Q` para
   salir. Recomendado: 40-60 de cada tipo como mínimo, con 3-4 personas
   distintas para "real" (no hace falta que sea todo el personal), variando
   luz, ángulo y distancia. Para "falso", varios celulares/fotos distintos.
   Todo se guarda en `web/dataset_liveness/real/` y `web/dataset_liveness/falso/`.
3. Entrena y guarda el modelo:
   ```
   python entrenar_liveness.py
   ```
   Esto imprime qué tan bien distingue real de falso y guarda
   `web/modelo_liveness.pkl`.
4. Reinicia `app.py` (Ctrl+C y volver a correrlo) para que tome el modelo nuevo.

## Si el sistema empieza a rechazar a alguien real por error

No hace falta rehacer todo el dataset: corre `capturar_ejemplos.py` de nuevo,
agrega unos 15-20 ejemplos `R` de esa persona (se suman a los que ya había),
y vuelve a correr `entrenar_liveness.py`.

## Archivos relacionados

- `web/liveness_ml.py` — extracción de características y carga del modelo.
- `web/capturar_ejemplos.py` — herramienta de captura (no depende de Flask).
- `web/entrenar_liveness.py` — entrenamiento, guarda `modelo_liveness.pkl`.
- `web/dataset_liveness/` — imágenes de ejemplo (real/ y falso/).
- `web/modelo_liveness.pkl` — modelo entrenado, generado por el paso anterior.