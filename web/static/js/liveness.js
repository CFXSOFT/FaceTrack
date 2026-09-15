/* ===========================================================
   liveness.js — Prueba de vida (anti-spoof) 100% INVISIBLE.
   -----------------------------------------------------------
   No muestra ningún mensaje, ícono ni notificación en pantalla.
   Internamente exige detectar un parpadeo o un pequeño
   movimiento de cabeza (vía landmarks faciales) antes de dejar
   pasar un frame al backend de reconocimiento. Una foto impresa
   o una pantalla estática nunca produce ese movimiento, así que
   nunca llega a "verificado" y jamás se envía a /api/reconocer.

   Flujo interno (nunca expuesto en la UI):
     inactivo -> esperando -> analizando -> movimiento_detectado -> completado

   Uso desde app.js:
     await Liveness.iniciar(video);   // al encender la cámara
     Liveness.estaVerificado();       // gate antes de captureAndSend()
     Liveness.detener();              // al apagar la cámara

   Requisito: cargar el script de face-api.js ANTES que este
   archivo (ver templates/index.html).

   La verificación server-side (DeepFace anti_spoofing en
   app.py) se mantiene intacta como segunda capa de defensa.
   =========================================================== */
const Liveness = (() => {
    // Pesos oficiales del modelo (justadudewhohacks/face-api.js), servidos por CDN.
    const MODEL_URL = 'https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights';

    const UMBRAL_EAR = 0.21;           // eye-aspect-ratio por debajo de esto = "ojo cerrado"
    const UMBRAL_EAR_RECUPERO = 0.24;  // debe volver a superar esto para contar el parpadeo
    const UMBRAL_YAW = 0.07;           // variación del ratio nariz/mandíbula -> giro real de cabeza
    const UMBRAL_PITCH = 0.07;         // variación del ratio nariz/mentón -> asentir real
    const MUESTRAS_MIN_MOV = 5;        // frames mínimos antes de confiar en el rango medido (filtra ruido)
    const VENTANA_MOV_MS = 2000;       // ventana de tiempo para medir el movimiento de cabeza
    const MS_SIN_ROSTRO_RESET = 800;   // sin rostro detectado por este tiempo -> se reinicia la sesión
    const MS_LOOP = 180;               // cada cuánto se analiza un frame (~5-6 fps, suficiente y liviano)

    let modelosListos = false;
    let promesaModelos = null;
    let videoEl = null;
    let loopTimer = null;
    let detenido = true;

    // ---- Estado interno de la sesión de liveness actual ----
    let fase = 'inactivo';   // inactivo | esperando | analizando | movimiento_detectado | completado
    let verificado = false;
    let ultimaVezConRostro = 0;
    let earEnCaida = false;
    let poseHistorial = []; // { yaw, pitch, t } -- ratios de perspectiva, no posición absoluta

    function log(...args) {
        // Solo consola de desarrollador (nunca visible para el usuario final).
        if (window.__LIVENESS_DEBUG__) console.debug('[liveness]', ...args);
    }

    async function cargarModelos() {
        if (modelosListos) return;
        if (promesaModelos) return promesaModelos;
        promesaModelos = (async () => {
            await faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL);
            await faceapi.nets.faceLandmark68Net.loadFromUri(MODEL_URL);
            modelosListos = true;
        })();
        try {
            await promesaModelos;
        } catch (err) {
            promesaModelos = null;
            throw err;
        }
    }

    function distancia(a, b) {
        return Math.hypot(a.x - b.x, a.y - b.y);
    }

    // Eye Aspect Ratio clásico (6 puntos por ojo, esquema de 68 landmarks).
    function earDeOjo(pts) {
        const A = distancia(pts[1], pts[5]);
        const B = distancia(pts[2], pts[4]);
        const C = distancia(pts[0], pts[3]);
        return C === 0 ? 0 : (A + B) / (2 * C);
    }

    function reiniciarSesion() {
        fase = 'esperando';
        verificado = false;
        earEnCaida = false;
        poseHistorial = [];
    }

    function marcarVerificado() {
        if (!verificado) {
            fase = 'completado';
            verificado = true;
            log('verificación de vida completada');
        }
    }

    async function analizarFrame() {
        if (!videoEl || videoEl.readyState < 2) return;

        let resultado;
        try {
            resultado = await faceapi
                .detectSingleFace(videoEl, new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.5 }))
                .withFaceLandmarks();
        } catch (err) {
            // Un fallo puntual de inferencia no debe romper el resto de la app.
            return;
        }

        const ahora = Date.now();

        if (!resultado) {
            if (fase !== 'inactivo' && ahora - ultimaVezConRostro > MS_SIN_ROSTRO_RESET) {
                fase = 'inactivo';
                verificado = false;
                earEnCaida = false;
                poseHistorial = [];
            }
            return;
        }

        if (fase === 'inactivo') reiniciarSesion();
        ultimaVezConRostro = ahora;

        if (verificado) return; // ya se superó la prueba en esta sesión, no hace falta seguir analizando

        fase = 'analizando';

        const pts = resultado.landmarks.positions;
        const ojoIzq = pts.slice(36, 42);
        const ojoDer = pts.slice(42, 48);
        const ear = (earDeOjo(ojoIzq) + earDeOjo(ojoDer)) / 2;

        // --- Detección de parpadeo ---
        if (ear < UMBRAL_EAR) {
            earEnCaida = true;
        } else if (earEnCaida && ear > UMBRAL_EAR_RECUPERO) {
            earEnCaida = false;
            fase = 'movimiento_detectado';
            marcarVerificado();
            return;
        }

        // --- Detección de movimiento natural de cabeza (giro / inclinación) ---
        // OJO: NO se mide la posición/traslación del rostro en la imagen -- una
        // foto sostenida a pulso tiembla y eso desplaza el rostro tanto o más que
        // un giro real, lo cual falseaba esta prueba. En su lugar se miden RATIOS
        // de perspectiva entre landmarks (dónde cae la nariz respecto al ancho de
        // la mandíbula, y respecto a la distancia ojos-mentón). Esos ratios se
        // mantienen prácticamente constantes ante cualquier traslación o rotación
        // en el plano de un objeto plano (la foto), y solo cambian de verdad
        // cuando hay una rotación 3D real de una cabeza frente a la cámara.
        const jawIzq = pts[0], jawDer = pts[16], nariz = pts[30], menton = pts[8];
        const anchoMandibula = Math.abs(jawDer.x - jawIzq.x);
        const altoRostro = Math.abs(menton.y - ((pts[36].y + pts[45].y) / 2));

        if (anchoMandibula > 10 && altoRostro > 10) {
            const yaw = (nariz.x - Math.min(jawIzq.x, jawDer.x)) / anchoMandibula;
            const pitch = (nariz.y - ((pts[36].y + pts[45].y) / 2)) / altoRostro;

            poseHistorial.push({ yaw, pitch, t: ahora });
            poseHistorial = poseHistorial.filter(p => ahora - p.t < VENTANA_MOV_MS);

            if (poseHistorial.length >= MUESTRAS_MIN_MOV) {
                const yaws = poseHistorial.map(p => p.yaw);
                const pitches = poseHistorial.map(p => p.pitch);
                const rangoYaw = Math.max(...yaws) - Math.min(...yaws);
                const rangoPitch = Math.max(...pitches) - Math.min(...pitches);
                if (rangoYaw > UMBRAL_YAW || rangoPitch > UMBRAL_PITCH) {
                    fase = 'movimiento_detectado';
                    marcarVerificado();
                }
            }
        }
    }

    function loop() {
        if (detenido) return;
        analizarFrame().finally(() => {
            if (!detenido) loopTimer = setTimeout(loop, MS_LOOP);
        });
    }

    async function iniciar(video) {
        videoEl = video;
        detenido = false;
        fase = 'inactivo';
        verificado = false;
        ultimaVezConRostro = 0;
        earEnCaida = false;
        poseHistorial = [];

        try {
            await cargarModelos();
        } catch (err) {
            // Si no se pudieron cargar los modelos (ej. sin internet la primera vez),
            // no se bloquea el sistema completo: se deja pasar al reconocimiento y
            // queda como única defensa el anti-spoofing del servidor (DeepFace).
            console.warn('[liveness] no se pudieron cargar los modelos, se omite la prueba de vida en cliente:', err);
            verificado = true;
            return;
        }

        if (loopTimer) clearTimeout(loopTimer);
        loop();
    }

    function detener() {
        detenido = true;
        videoEl = null;
        if (loopTimer) clearTimeout(loopTimer);
        loopTimer = null;
        fase = 'inactivo';
        verificado = false;
        earEnCaida = false;
        poseHistorial = [];
    }

    function estaVerificado() {
        return verificado === true;
    }

    return { iniciar, detener, estaVerificado };
})();