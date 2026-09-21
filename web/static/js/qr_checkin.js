/* ===========================================================
   qr_checkin.js — Lectura de QR del carnet para check-in directo.
   -----------------------------------------------------------
   Corre en paralelo al reconocimiento facial, mientras la cámara
   está encendida. Usa jsQR sobre un canvas propio (no comparte el
   que usa captureAndSend en app.js) para no pisar el frame que se
   está por enviar al reconocimiento.

   Es mucho más barato que el reconocimiento facial (no hay red
   neuronal de por medio), así que corre a un ritmo fijo corto
   (~300ms) sin afectar el rendimiento del resto del sistema.

   El check-in con QR NO pasa por la prueba de vida ni por
   reconocimiento facial -- el carnet es personal e intransferible
   del maestro, así que su sola lectura ya alcanza para marcar
   entrada/salida (decisión explícita, no un descuido).
   =========================================================== */
const QRCheckin = (() => {
    const MS_LOOP = 300;
    let canvasQR = null;
    let ctxQR = null;
    let timer = null;
    let enCooldown = false;

    function asegurarCanvas() {
        if (canvasQR) return;
        canvasQR = document.createElement('canvas');
        ctxQR = canvasQR.getContext('2d', { willReadFrequently: true });
    }

    function fijarEstado(estado, texto, duracionMs) {
        const marco = document.getElementById('qrViewfinder');
        const status = document.getElementById('qrStatus');
        if (!marco) return;
        marco.classList.remove('qr-detectado', 'qr-ok', 'qr-error');
        if (estado) marco.classList.add(estado);
        if (status) status.textContent = texto || '';
        if (duracionMs) {
            clearTimeout(fijarEstado._t);
            fijarEstado._t = setTimeout(() => {
                marco.classList.remove('qr-detectado', 'qr-ok', 'qr-error');
                if (status) status.textContent = '';
            }, duracionMs);
        }
    }

    async function enviarToken(token) {
        enCooldown = true;
        fijarEstado('qr-detectado', 'Verificando…');
        try {
            const res = await fetch('/api/qr/checkin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token })
            });
            const data = await res.json();
            if (data.requiere_confirmacion) {
                fijarEstado(null, '');
                mostrarModalSiguienteClase(data);
            } else if (data.ok) {
                fijarEstado('qr-ok', `${data.nombre.split(' ')[0]} · ${data.tipo.toUpperCase()}`, 2000);
                log(`${data.nombre} — ${data.tipo.toUpperCase()} (QR)${data.es_tardanza ? ' · TARDE' : ''}${data.materia ? ' [' + data.materia + ']' : ''}`);
                mostrarCarnetToast(data.nombre, data.tipo, data.foto, data.es_tardanza, false);
                if (typeof cargarRecientes === 'function') cargarRecientes();
            } else {
                fijarEstado('qr-error', data.mensaje || 'QR no reconocido', 2000);
                if (data.mensaje) log(data.mensaje);
            }
        } catch (err) {
            fijarEstado('qr-error', 'Error de conexión', 2000);
            log('Error de conexión al leer el QR.');
        }
        // cooldown corto para no reintentar el mismo QR frame tras frame
        // mientras el carnet sigue frente a la cámara
        setTimeout(() => { enCooldown = false; }, 2500);
    }

    function analizarFrame(video) {
        if (enCooldown || typeof jsQR === 'undefined') return;
        if (!video || video.readyState < 2 || !video.videoWidth) return;

        asegurarCanvas();
        // Un tamaño moderado alcanza para leer el QR y es rápido de procesar.
        const ancho = 400;
        const escala = ancho / video.videoWidth;
        const alto = Math.round(video.videoHeight * escala);
        canvasQR.width = ancho;
        canvasQR.height = alto;
        ctxQR.drawImage(video, 0, 0, ancho, alto);

        let datosImagen;
        try {
            datosImagen = ctxQR.getImageData(0, 0, ancho, alto);
        } catch (e) {
            return;
        }

        const resultado = jsQR(datosImagen.data, ancho, alto);
        if (resultado && resultado.data) {
            enviarToken(resultado.data.trim());
        }
    }

    function iniciar(video) {
        if (timer) clearInterval(timer);
        timer = setInterval(() => analizarFrame(video), MS_LOOP);
    }

    function detener() {
        if (timer) clearInterval(timer);
        timer = null;
        enCooldown = false;
        fijarEstado(null, '');
    }

    return { iniciar, detener };
})();