/* =========================================================
   SENATI — App SPA unificada (Flask-compatible)
   Router + Inicio + Registros + Calendario + Admin
   Optimizado: delegación de eventos, RAF, caches
   ========================================================= */

/* ═══════════════════════════════════════════════════════
   ESTADO GLOBAL
   ═══════════════════════════════════════════════════════ */
const STATE = {
    paginaActual: 'inicio',
    stream: null,
    scanInterval: null,
    isScanning: false,
    registrosCache: { hoy: null, ayer: null, antier: null },
    abortController: null,
    // Calendario
    calOffset: 0,
    calData: [],
    calFiltroAreas: [],
    calFiltroAreasPendiente: [],
    calBusqueda: '',
    calFaceIdStream: null,
    // Admin
    adminAutenticado: false,
    adminTab: 'personal',
    personalCache: [],
    personalFiltrado: [],
    adminCamStream: null,
    adminFotoBase64: null,
    // Configuracion real del sistema (antes esto se guardaba en localStorage y nunca se volvia
    // a leer). Los valores de aca son el respaldo mientras se completa la primera carga desde
    // /api/configuracion — coinciden con el comportamiento que tenia el sistema antes de que
    // esta configuracion existiera, asi que no hay ningun cambio de comportamiento al arrancar.
    config: { intervalo_escaneo: 3, hora_entrada: '08:00', hora_salida: '17:00', tolerancia_min: 0 },
    adminIconBase64: null,
    adminHorarioBase64: null,
    adminHorarioNombre: null,
    adminEliminarId: null,
    repArmado: false,
    pwArmado: false,
    reportesCache: [],
    areasSENATI: [
        'Administración de Empresas', 'Administradores industrial', 'Diseño Gráfico Digital',
        'Electricidad Industrial', 'Ing Ciberseguridad', 'Ing Software con IA',
        'Marketing y Gestión Comercial', 'Mecánico Automotriz', 'Mecánico de Mantenimiento',
        'Mecánico de Maquinaria Pesada', 'Mecatrónica Automotriz', 'Mecatronica Industrial', 'MPTD'
    ]
};

/* ═══════════════════════════════════════════════════════
   UTILIDADES
   ═══════════════════════════════════════════════════════ */
const $ = (sel, ctx = document) => ctx.querySelector(sel);
const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

// Antes de insertar cualquier texto que venga de datos (nombre, área, etc.) dentro de
// innerHTML, hay que escaparlo. Sin esto, un nombre con caracteres como <, >, " o ' puede
// romper el HTML generado (ej. un apóstrofe en el nombre rompía el botón "Eliminar", ver
// abajo) o, en el peor caso, ejecutar HTML/JS no deseado si alguien lo hace a propósito.
function escapeHtml(texto) {
    return String(texto ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

async function cargarConfigSistema() {
    try {
        const res = await fetch('/api/configuracion');
        if (res.ok) STATE.config = await res.json();
    } catch (e) {
        // Si falla, se sigue usando el valor por defecto de STATE.config (igual al
        // comportamiento que tenia el sistema antes de que esta configuracion existiera).
    }
}

function pad(n) { return n < 10 ? '0' + n : n; }
function formatearFecha(date) { return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`; }
function formatearFechaHumana(date) {
    const meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
    return `${date.getDate()} ${meses[date.getMonth()]}`;
}

function obtenerFechaTab(tab) {
    const hoy = new Date();
    const d = new Date(hoy);
    if (tab === 'ayer' || tab === 'historial') d.setDate(d.getDate() - 1);
    if (tab === 'antier') d.setDate(d.getDate() - 2);
    return formatearFecha(d);
}

function horaDeFecha(fechaHora) {
    if (!fechaHora) return '--:--';
    const partes = fechaHora.split(' ');
    if (partes.length > 1) return partes[1].substring(0, 5);
    return fechaHora.substring(11, 16) || '--:--';
}

function horaAmPm(fechaHora) {
    const h24 = horaDeFecha(fechaHora);
    if (!h24 || h24 === '--:--') return '-- : --';
    const [hh, mm] = h24.split(':').map(Number);
    if (Number.isNaN(hh) || Number.isNaN(mm)) return '-- : --';
    const suf = hh >= 12 ? 'pm' : 'am';
    const h12 = hh % 12 || 12;
    return `${h12}:${String(mm).padStart(2, '0')}${suf}`;
}

function horaDecimal(fechaHora) {
    const h = horaDeFecha(fechaHora);
    if (h === '--:--') return null;
    const [hh, mm] = h.split(':').map(Number);
    return hh + mm / 60;
}

function esTardanza(horaEntrada, horaInicioBloque, toleranciaMin) {
    if (!horaEntrada || horaEntrada === '--:--') return false;
    const [h, m] = horaEntrada.split(':').map(Number);
    // Si hay bloque de clase, usamos su hora de inicio + tolerancia (default 5 min)
    if (horaInicioBloque) {
        const [hb, mb] = horaInicioBloque.split(':').map(Number);
        const tol = toleranciaMin != null ? toleranciaMin : 5;
        return (h * 60 + m) > (hb * 60 + mb + tol);
    }
    const [hLimite, mLimiteBase] = (STATE.config.hora_entrada || '08:00').split(':').map(Number);
    const minutosLimite = hLimite * 60 + mLimiteBase + (STATE.config.tolerancia_min != null ? STATE.config.tolerancia_min : 5);
    return (h * 60 + m) > minutosLimite;
}

function inicioDeSemana(date) {
    const d = new Date(date);
    const day = d.getDay();
    const diff = d.getDate() - day + (day === 0 ? -6 : 1);
    d.setDate(diff);
    d.setHours(0, 0, 0, 0);
    return d;
}

function finDeSemana(date) {
    const d = inicioDeSemana(date);
    d.setDate(d.getDate() + 6);
    d.setHours(23, 59, 59, 999);
    return d;
}

function diasDeSemana(date) {
    const lunes = inicioDeSemana(date);
    const dias = [];
    for (let i = 0; i < 7; i++) {
        const d = new Date(lunes);
        d.setDate(lunes.getDate() + i);
        dias.push(d);
    }
    return dias;
}

function debounce(fn, ms) {
    let t;
    return (...args) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
    };
}

/* ═══════════════════════════════════════════════════════
   ROUTER SPA
   ═══════════════════════════════════════════════════════ */
function pathnameToPage(path) {
    path = path.replace(/^\//, '').replace(/\/$/, '');
    if (!path) return 'inicio';
    const map = { registros: 'registros', calendario: 'calendario', admin: 'admin', telefonos: 'telefonos' };
    return map[path] || 'inicio';
}

function pageToPathname(page) {
    if (page === 'inicio') return '/';
    return '/' + page;
}

let _sidebarCollapseTimer = null;

function expandSidebarBriefly() {
    const shell = document.querySelector('.app-shell');
    if (!shell) return;
    shell.classList.add('sidebar-expanded');
    clearTimeout(_sidebarCollapseTimer);
    _sidebarCollapseTimer = setTimeout(() => {
        shell.classList.remove('sidebar-expanded');
    }, 2200);
}

function navigateTo(page, push = true) {
    if (STATE.paginaActual === page) {
        expandSidebarBriefly();
        return;
    }
    if (STATE.paginaActual === 'inicio') cleanupCamara();
    if (STATE.paginaActual === 'calendario') cleanupFaceId();
    if (STATE.paginaActual === 'admin') cleanupAdminCam();

    STATE.paginaActual = page;
    if (push && window.history.pushState) {
        window.history.pushState({ page }, '', pageToPathname(page));
    }
    $$('.page').forEach(p => p.classList.add('hidden'));
    const destino = $(`#page-${page}`);
    if (destino) {
        destino.classList.remove('hidden');
        void destino.offsetWidth;
        destino.style.animation = 'none';
        destino.offsetHeight;
        destino.style.animation = '';
    }
    $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === page));
    expandSidebarBriefly();
    if (page === 'registros') initRegistros();
    if (page === 'inicio') initInicio();
    if (page === 'calendario') initCalendario();
    if (page === 'admin') initAdmin();
    if (page === 'telefonos') initTelefonos();
}

function initRouter() {
    document.addEventListener('click', e => {
        const link = e.target.closest('.nav-item');
        if (!link) return;
        const page = link.dataset.page;
        if (!page) return;
        e.preventDefault();
        if (link.dataset.adminTab) {
            STATE.adminTab = link.dataset.adminTab;
            if (page === 'admin' && STATE.paginaActual === 'admin') {
                renderAdminTab(STATE.adminTab);
                return;
            }
        }
        navigateTo(page);
    });
    window.addEventListener('popstate', e => {
        const page = (e.state && e.state.page) || pathnameToPage(location.pathname);
        navigateTo(page, false);
    });
    navigateTo(pathnameToPage(location.pathname), false);
}

/* ═══════════════════════════════════════════════════════
   PÁGINA: INICIO — CÁMARA
   ═══════════════════════════════════════════════════════ */
const video = $('#video');
const canvas = $('#canvas');
const placeholder = $('#cameraPlaceholder');
const scanOverlay = $('#scanOverlay');
const btnEncender = $('#btnEncender');
const btnApagar = $('#btnApagar');
const consoleBox = $('#consoleBox');
const scanTime = $('#scanTime');

setInterval(() => {
    if (scanTime) scanTime.textContent = new Date().toLocaleTimeString('es-PE', { hour12: false });
}, 1000);

async function encenderCamara() {
    if (STATE.stream) return;
    try {
        STATE.stream = await navigator.mediaDevices.getUserMedia({
            video: { width: 1280, height: 720, facingMode: 'user' }
        });
        video.srcObject = STATE.stream;
        video.style.display = 'block';
        placeholder.style.display = 'none';
        scanOverlay.style.display = 'flex';
        btnEncender.disabled = true;
        btnApagar.disabled = false;
        startScanning();
    } catch (err) {
        log('Error: no se pudo acceder a la cámara.');
        console.error(err);
    }
}

function apagarCamara() { cleanupCamara(); }

function cleanupCamara() {
    if (STATE.stream) {
        STATE.stream.getTracks().forEach(t => t.stop());
        STATE.stream = null;
    }
    if (video) video.style.display = 'none';
    if (placeholder) placeholder.style.display = 'flex';
    if (scanOverlay) scanOverlay.style.display = 'none';
    if (btnEncender) btnEncender.disabled = false;
    if (btnApagar) btnApagar.disabled = true;
    stopScanning();
}

function startScanning() {
    STATE.isScanning = true;
    log('Cámara activada. Esperando rostro...');
    const ms = Math.max(1000, Math.round((STATE.config.intervalo_escaneo || 3) * 1000));
    STATE.scanInterval = setInterval(captureAndSend, ms);
}

function stopScanning() {
    STATE.isScanning = false;
    if (STATE.scanInterval) clearInterval(STATE.scanInterval);
    STATE.scanInterval = null;
    log('Cámara detenida.');
}

function captureAndSend() {
    if (!STATE.stream || !STATE.isScanning) return;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/jpeg', 0.85);

    fetch('/api/reconocer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ imagen: dataUrl })
    })
        .then(r => r.json())
        .then(data => {
            if (data.ok) {
                log(`${data.nombre} — ${data.tipo.toUpperCase()}${data.es_tardanza ? ' · TARDE' : ''}${data.materia ? ' [' + data.materia + ']' : ''} (${data.confianza}%)`);
                mostrarCarnetToast(data.nombre, data.tipo, data.foto, data.es_tardanza);
                cargarRecientes();
            } else if (data.mensaje && !data.mensaje.includes('reconocido') && !data.mensaje.toLowerCase().includes('rostro')
                && !data.mensaje.toLowerCase().includes('no hay personal')) {
                log(data.mensaje);
            }
        })
        .catch(() => log('Error de conexión con el servidor.'));
}

let carnetToastTimeout = null;
function mostrarCarnetToast(nombre, tipo, foto, esTarde) {
    const toast = $('#carnetToast');
    const img = $('#carnetToastFoto');
    const fallback = $('#carnetToastFallback');
    const photoWrap = toast.querySelector('.carnet-toast-photo');
    const msgEl = $('#carnetToastMsg');

    $('#carnetToastNombre').textContent = nombre;
    let msg = tipo === 'salida' ? 'salida registrada' : 'entrada registrada';
    if (esTarde) msg += ' · TARDE';
    msgEl.textContent = msg;
    msgEl.classList.toggle('salida', tipo === 'salida');
    msgEl.classList.toggle('tarde', !!esTarde);
    photoWrap.classList.toggle('salida-border', tipo === 'salida');

    if (foto) {
        img.src = foto;
        img.classList.remove('hidden');
        fallback.textContent = '';
    } else {
        img.classList.add('hidden');
        fallback.textContent = nombre.trim().charAt(0).toUpperCase();
    }

    clearTimeout(carnetToastTimeout);
    toast.classList.remove('hidden', 'leaving');
    void toast.offsetHeight;
    toast.style.animation = 'none';
    void toast.offsetHeight;
    toast.style.animation = '';

    carnetToastTimeout = setTimeout(() => {
        toast.classList.add('leaving');
        setTimeout(() => toast.classList.add('hidden'), 350);
    }, 4000);
}

function log(msg) {
    const time = new Date().toLocaleTimeString('es-PE', { hour12: false });
    const line = document.createElement('div');
    line.className = 'console-line';
    line.innerHTML = `<span class="console-time">${time}</span> <span>${msg}</span>`;
    consoleBox.appendChild(line);
    consoleBox.scrollTop = consoleBox.scrollHeight;
    while (consoleBox.children.length > 50) consoleBox.removeChild(consoleBox.firstChild);
}

async function cargarRecientes() {
    const grid = $('#recientesGrid');
    const empty = $('#emptyRecientes');
    try {
        const hoy = formatearFecha(new Date());
        const res = await fetch(`/api/registros?rango=custom&inicio=${hoy}&fin=${hoy}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        const lista = Array.isArray(data) ? data : [];
        lista.sort((a, b) => String(b.fecha_hora || '').localeCompare(String(a.fecha_hora || '')));
        renderRecientes(lista.slice(0, 8));
    } catch (e) {
        grid.innerHTML = '';
        empty.style.display = 'flex';
    }
}

function avatarHtml(color, foto) {
    return foto
        ? `<img src="${foto}" alt="" loading="lazy">`
        : '';
}

function renderRecientes(data) {
    const grid = $('#recientesGrid');
    const empty = $('#emptyRecientes');
    if (!data || data.length === 0) {
        grid.innerHTML = '';
        empty.style.display = 'flex';
        return;
    }
    empty.style.display = 'none';
    const frag = document.createDocumentFragment();
    data.forEach(r => {
        const tipo = (r.tipo || '').toLowerCase();
        const hora = horaAmPm(r.fecha_hora);
        let linea1 = '-- : --';
        let linea2 = '-- : --';
        if (tipo === 'entrada') {
            linea1 = `entrada: ${hora}`;
            linea2 = '-- : --';
        } else if (tipo === 'salida') {
            linea1 = `salida: ${hora}`;
            linea2 = '-- : --';
        }
        const card = document.createElement('div');
        card.className = 'person-card';
        card.innerHTML = `
      <div class="person-header">
        <div class="person-avatar" style="background:${r.color || '#d1d5db'}">${avatarHtml(r.color, r.foto)}</div>
        <div>
          <div class="person-name">${escapeHtml(r.nombre || '')}</div>
          <div class="person-id">${escapeHtml(r.tipo_personal || 'personal')}</div>
        </div>
      </div>
      <div class="person-meta">
        <span>${escapeHtml(r.area || 'Sin área')}</span>
        <span>${linea1}</span>
        <span>${linea2}</span>
      </div>`;
        frag.appendChild(card);
    });
    grid.innerHTML = '';
    grid.appendChild(frag);
}

function initInicio() {
    cargarRecientes();
}

/* ═══════════════════════════════════════════════════════
   PÁGINA: REGISTROS
   ═══════════════════════════════════════════════════════ */
let tabRegistros = 'hoy';
STATE.registrosRaw = [];
STATE.registrosFiltroQ = '';
STATE.registrosFiltroCargo = '';
STATE.registrosFiltroCarrera = '';

function initRegistros() {
    const tabsBar = $('#registrosTabs');
    if (tabsBar && !tabsBar.dataset.initialized) {
        tabsBar.addEventListener('click', e => {
            const btn = e.target.closest('.tab-btn');
            if (!btn) return;
            cambiarTabRegistros(btn.dataset.tab);
        });
        tabsBar.dataset.initialized = '1';
    }
    const selCarrera = $('#regFilterCarrera');
    if (selCarrera && selCarrera.options.length <= 1) {
        (STATE.areasSENATI || []).forEach(a => {
            const o = document.createElement('option');
            o.value = a;
            o.textContent = a;
            selCarrera.appendChild(o);
        });
    }
    $('#regSearch')?.addEventListener('input', debounce(e => {
        STATE.registrosFiltroQ = (e.target.value || '').trim().toLowerCase();
        actualizarSuggestRegistros(STATE.registrosFiltroQ);
        renderRegistrosLista();
    }, 150));
    $('#regFilterCargo')?.addEventListener('change', e => {
        STATE.registrosFiltroCargo = e.target.value || '';
        renderRegistrosLista();
    });
    $('#regFilterCarrera')?.addEventListener('change', e => {
        STATE.registrosFiltroCarrera = e.target.value || '';
        renderRegistrosLista();
    });
    cambiarTabRegistros('hoy');
}

function cambiarTabRegistros(tab) {
    tabRegistros = tab;
    $$('#registrosTabs .tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    cargarRegistros(tab);
}

async function cargarRegistros(tab) {
    if (STATE.abortController) STATE.abortController.abort();
    STATE.abortController = new AbortController();
    try {
        let url;
        if (tab === 'historial') {
            const fin = formatearFecha(new Date());
            const ini = formatearFecha(new Date(Date.now() - 30 * 86400000));
            url = `/api/registros?rango=custom&inicio=${ini}&fin=${fin}`;
        } else {
            const hoy = formatearFecha(new Date());
            url = `/api/registros?rango=custom&inicio=${hoy}&fin=${hoy}`;
        }
        const res = await fetch(url, { signal: STATE.abortController.signal });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        STATE.registrosRaw = await res.json();
        renderRegistrosLista();
    } catch (e) {
        if (e.name === 'AbortError') return;
        STATE.registrosRaw = [];
        renderRegistrosLista();
    }
}

function filtrarRegistrosRaw(lista) {
    return (lista || []).filter(r => {
        const matchQ = !STATE.registrosFiltroQ || (r.nombre || '').toLowerCase().includes(STATE.registrosFiltroQ);
        const matchCargo = !STATE.registrosFiltroCargo || (r.tipo_personal || '') === STATE.registrosFiltroCargo;
        const matchCarrera = !STATE.registrosFiltroCarrera || (r.area || '') === STATE.registrosFiltroCarrera;
        return matchQ && matchCargo && matchCarrera;
    });
}

function actualizarSuggestRegistros(q) {
    const box = $('#regSearchSuggest');
    if (!box) return;
    if (!q || q.length < 2) {
        box.classList.add('hidden');
        box.innerHTML = '';
        return;
    }
    const nombres = [...new Set(
        (STATE.registrosRaw || [])
            .filter(r => (r.nombre || '').toLowerCase().includes(q))
            .map(r => r.nombre)
    )].slice(0, 6);
    if (!nombres.length) {
        box.classList.add('hidden');
        return;
    }
    box.innerHTML = nombres.map(n =>
        `<button type="button" class="suggest-item" data-nombre="${escapeHtml(n)}">${escapeHtml(n)}</button>`
    ).join('');
    box.classList.remove('hidden');
    box.querySelectorAll('.suggest-item').forEach(btn => {
        btn.addEventListener('click', () => {
            const inp = $('#regSearch');
            if (inp) inp.value = btn.dataset.nombre;
            STATE.registrosFiltroQ = (btn.dataset.nombre || '').toLowerCase();
            box.classList.add('hidden');
            renderRegistrosLista();
        });
    });
}

function renderRegistrosLista() {
    const list = $('#registrosList');
    const empty = $('#emptyRegistrosList');
    if (!list) return;
    const items = filtrarRegistrosRaw(STATE.registrosRaw);
    if (!items.length) {
        list.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';
    const frag = document.createDocumentFragment();
    items.forEach(r => {
        const hora = horaDeFecha(r.fecha_hora);
        const fecha = (r.fecha_hora || '').substring(0, 10);
        const esEntrada = r.tipo === 'entrada';
        const row = document.createElement('div');
        row.className = 'reg-row';
        row.innerHTML = `
            <div class="reg-avatar-wrap">
                <div class="reg-avatar" style="background:${r.color || '#d1d5db'}">${avatarHtml(r.color, r.foto)}</div>
                <span class="reg-dot" style="background:${r.color || '#d1d5db'}"></span>
            </div>
            <div class="reg-info">
                <div class="reg-name">${escapeHtml(r.nombre || '')}</div>
                <div class="reg-email">${escapeHtml(r.correo || '--:--')}</div>
            </div>
            <div class="reg-carrera">${escapeHtml(r.area || '—')}</div>
            <div class="reg-tipo"><span class="badge-tipo ${esEntrada ? 'entrada' : 'salida'}">${esEntrada ? 'entrada' : 'salida'}</span></div>
            <div class="reg-hora"><strong>${hora}</strong></div>
            <div class="reg-fecha">${fecha}</div>
        `;
        frag.appendChild(row);
    });
    list.innerHTML = '';
    list.appendChild(frag);
}

/* ═══════════════════════════════════════════════════════
   PÁGINA: CALENDARIO
   ═══════════════════════════════════════════════════════ */

/* ---------- DATE PICKER ---------- */
let dpState = {
    open: false,
    year: new Date().getFullYear(),
    month: new Date().getMonth(),
    week: 0,
};

function toggleDatePicker() {
    dpState.open = !dpState.open;
    const panel = $('#datePickerPanel');
    const wrap = $('.date-picker-wrap');
    if (dpState.open) {
        panel.classList.remove('hidden');
        wrap.classList.add('open');
        initDatePicker();
    } else {
        panel.classList.add('hidden');
        wrap.classList.remove('open');
    }
}

function closeDatePicker() {
    dpState.open = false;
    $('#datePickerPanel')?.classList.add('hidden');
    $('.date-picker-wrap')?.classList.remove('open');
}

function initDatePicker() {
    const base = getSemanaBase();
    dpState.year = base.getFullYear();
    dpState.month = base.getMonth();
    dpState.week = getWeekOfMonth(base);
    renderDpYears();
    renderDpMonths();
    renderDpWeeks();
}

function getWeekOfMonth(date) {
    const d = new Date(date);
    d.setDate(1);
    const firstDay = d.getDay() || 7;
    const offset = firstDay - 1;
    return Math.floor((date.getDate() + offset - 1) / 7);
}

function renderDpYears() {
    const container = $('#dpYears');
    container.innerHTML = '';
    const currentYear = new Date().getFullYear();
    const start = dpState.year - 6;
    const end = dpState.year + 6;
    for (let y = start; y <= end; y++) {
        const btn = document.createElement('button');
        btn.className = 'dp-year' + (y === dpState.year ? ' selected' : '') + (y === currentYear ? ' current' : '');
        btn.textContent = y;
        btn.addEventListener('click', () => { dpState.year = y; renderDpYears(); renderDpWeeks(); });
        container.appendChild(btn);
    }
    $('#dpTitleYear').textContent = dpState.year;
}

function renderDpMonths() {
    const container = $('#dpMonths');
    container.innerHTML = '';
    const meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
    const currentMonth = new Date().getMonth();
    const currentYear = new Date().getFullYear();
    meses.forEach((m, i) => {
        const btn = document.createElement('button');
        const isCurrent = i === currentMonth && dpState.year === currentYear;
        btn.className = 'dp-month' + (i === dpState.month ? ' selected' : '') + (isCurrent ? ' current' : '');
        btn.textContent = m;
        btn.addEventListener('click', () => { dpState.month = i; renderDpMonths(); renderDpWeeks(); });
        container.appendChild(btn);
    });
}

function renderDpWeeks() {
    const container = $('#dpWeeks');
    container.innerHTML = '';
    const currentDate = new Date();
    const currentYear = currentDate.getFullYear();
    const currentMonth = currentDate.getMonth();

    const firstDay = new Date(dpState.year, dpState.month, 1);
    const lastDay = new Date(dpState.year, dpState.month + 1, 0);
    const weeks = [];

    let current = new Date(firstDay);
    current.setDate(current.getDate() - ((current.getDay() || 7) - 1));

    // Solo 4 semanas por mes (evita Semana 5/6 que desalinean el panel)
    while (weeks.length < 4) {
        const mon = new Date(current);
        const sun = new Date(current);
        sun.setDate(sun.getDate() + 6);
        weeks.push({ mon, sun });
        current.setDate(current.getDate() + 7);
    }
    if (dpState.week > 3) dpState.week = 3;

    let currentWeekIdx = -1;
    weeks.forEach((w, i) => {
        if (currentDate >= w.mon && currentDate <= w.sun && dpState.year === currentYear && dpState.month === currentMonth) {
            currentWeekIdx = i;
        }
    });

    weeks.forEach((w, i) => {
        const btn = document.createElement('button');
        const isCurrent = i === currentWeekIdx;
        btn.className = 'dp-week' + (i === dpState.week ? ' selected' : '') + (isCurrent ? ' current' : '');
        btn.innerHTML = `<span>Semana ${i + 1}</span><span class="dp-week-dates">${w.mon.getDate()}-${w.sun.getDate()}</span>`;
        btn.addEventListener('click', () => { dpState.week = i; renderDpWeeks(); });
        container.appendChild(btn);
    });
}

function aplicarDatePicker() {
    const firstDayOfMonth = new Date(dpState.year, dpState.month, 1);
    const firstMonday = new Date(firstDayOfMonth);
    firstMonday.setDate(firstMonday.getDate() - ((firstMonday.getDay() || 7) - 1));
    firstMonday.setDate(firstMonday.getDate() + dpState.week * 7);

    const today = new Date();
    const currentMonday = inicioDeSemana(today);
    const selectedMonday = inicioDeSemana(firstMonday);

    const diffMs = selectedMonday - currentMonday;
    STATE.calOffset = Math.round(diffMs / (7 * 24 * 60 * 60 * 1000));

    closeDatePicker();
    renderCalendario();
}

function irAHoy() {
    STATE.calOffset = 0;
    closeDatePicker();
    renderCalendario();
}

function initCalendario() {
    STATE.calOffset = 0;
    STATE.calFiltroAreas = [];
    STATE.calFiltroAreasPendiente = [];
    STATE.calBusqueda = '';

    if (STATE.calendarioListenersListos) {
        renderCalendario();
        return;
    }
    STATE.calendarioListenersListos = true;

    $('#datePickerTrigger')?.addEventListener('click', toggleDatePicker);
    $('#dpPrevYear')?.addEventListener('click', () => { dpState.year--; renderDpYears(); renderDpWeeks(); });
    $('#dpNextYear')?.addEventListener('click', () => { dpState.year++; renderDpYears(); renderDpWeeks(); });
    $('#dpHoy')?.addEventListener('click', irAHoy);
    $('#dpAplicar')?.addEventListener('click', aplicarDatePicker);
    document.addEventListener('click', e => {
        if (!dpState.open) return;
        if (e.target && e.target.id === 'datePickerBackdrop') {
            closeDatePicker();
            return;
        }
        const wrap = $('.date-picker-wrap');
        const path = e.composedPath ? e.composedPath() : [];
        if (wrap && !path.includes(wrap) && e.target.id !== 'datePickerTrigger') closeDatePicker();
    });
    $('#datePickerBackdrop')?.addEventListener('click', closeDatePicker);

    $('#calPrevWeek')?.addEventListener('click', () => { STATE.calOffset--; renderCalendario(); });
    $('#calNextWeek')?.addEventListener('click', () => { STATE.calOffset++; renderCalendario(); });

    const searchInput = $('#calSearch');
    searchInput?.addEventListener('input', debounce(e => {
        STATE.calBusqueda = e.target.value.toLowerCase().trim();
        renderCalGrid();
    }, 200));

    $('#calFaceIdBtn')?.addEventListener('click', abrirFaceIdModal);
    $('#btnFaceIdClose')?.addEventListener('click', cerrarFaceIdModal);
    $('#btnFaceIdScan')?.addEventListener('click', escanearFaceId);
    $('#faceIdModal')?.addEventListener('click', e => {
        if (e.target === $('#faceIdModal')) cerrarFaceIdModal();
    });

    $('#calFiltrar')?.addEventListener('click', toggleFiltroMenu);

    renderCalendario();
}

function getSemanaBase() {
    const hoy = new Date();
    hoy.setDate(hoy.getDate() + STATE.calOffset * 7);
    return hoy;
}

async function renderCalendario() {
    const base = getSemanaBase();
    const dias = diasDeSemana(base);
    const lunes = dias[0];
    const domingo = dias[6];

    const meses = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
    $('#calMesAnio').textContent = `${meses[lunes.getMonth()]} ${lunes.getFullYear()}`;
    $('#calRango').textContent = `${formatearFechaHumana(lunes)} - ${formatearFechaHumana(domingo)} ${domingo.getFullYear()}`;

    const nombresDias = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
    const headerEl = $('#calDiasHeader');
    headerEl.innerHTML = '<div class="cal-hora-col"></div>';
    const hoyStr = formatearFecha(new Date());
    dias.forEach((d, i) => {
        const div = document.createElement('div');
        div.className = 'cal-dia-col' + (formatearFecha(d) === hoyStr ? ' today' : '');
        div.textContent = nombresDias[i];
        headerEl.appendChild(div);
    });

    await cargarDatosCalendario(lunes, domingo);
}

async function cargarDatosCalendario(lunes, domingo) {
    try {
        const lunesStr = formatearFecha(lunes);
        const domingoStr = formatearFecha(domingo);
        // Pedir el rango exacto de la semana visible (antes solo pedía "semana"
        // del servidor = semana actual, y fallaba al navegar).
        const res = await fetch(`/api/registros?rango=custom&inicio=${lunesStr}&fin=${domingoStr}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        STATE.calData = (data || []).filter(r => {
            const f = (r.fecha_hora || '').substring(0, 10);
            return f >= lunesStr && f <= domingoStr;
        });
    } catch (e) {
        console.error('calendario:', e);
        STATE.calData = [];
    }
    renderCalGrid();
    renderFiltroMenu();
}

function renderCalGrid() {
    const body = $('#calBody');
    if (!body) return;
    body.innerHTML = '';
    body.className = 'cal-body cal-timeline-h';

    const base = getSemanaBase();
    const dias = diasDeSemana(base);
    const nombresDias = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom'];

    let datos = STATE.calData || [];
    if (STATE.calFiltroAreas && STATE.calFiltroAreas.length > 0) {
        const set = new Set(STATE.calFiltroAreas.map(a => a.toLowerCase()));
        datos = datos.filter(r => set.has((r.area || '').toLowerCase()));
    }
    if (STATE.calBusqueda) {
        datos = datos.filter(r =>
            (r.nombre || '').toLowerCase().includes(STATE.calBusqueda) ||
            String(r.id || '').includes(STATE.calBusqueda)
        );
    }

    const filas = [];
    const map = {};
    datos.forEach(r => {
        const fecha = (r.fecha_hora || '').substring(0, 10);
        const idx = dias.findIndex(d => formatearFecha(d) === fecha);
        if (idx < 0) return;
        const key = `${r.nombre}__${fecha}`;
        if (!map[key]) {
            map[key] = {
                key, nombre: r.nombre, diaIdx: idx, fecha,
                entrada: null, salida: null, color: r.color, area: r.area,
                tipo: r.tipo_personal, id: r.id, foto: r.foto, tardanza: false
            };
            filas.push(map[key]);
        }
        if (r.tipo === 'entrada') {
            map[key].entrada = r;
            if (r.es_tardanza) map[key].tardanza = true;
        } else {
            map[key].salida = r;
        }
    });
    filas.sort((a, b) => a.diaIdx - b.diaIdx || (a.nombre || '').localeCompare(b.nombre || ''));

    const minH = 0, maxH = 24;
    const totalH = maxH - minH;

    const axis = document.createElement('div');
    axis.className = 'tl-axis';
    axis.innerHTML = `<div class="tl-axis-label"></div><div class="tl-axis-hours">${Array.from({ length: 13 }, (_, i) => {
        const h = i * 2;
        return `<span style="left:${(h / totalH) * 100}%">${h}:00</span>`;
    }).join('')}</div>`;
    body.appendChild(axis);

    if (!filas.length) {
        const empty = document.createElement('div');
        empty.className = 'empty-col';
        empty.style.display = 'flex';
        empty.innerHTML = '<p>No hay registros en esta semana</p>';
        body.appendChild(empty);
        return;
    }

    filas.forEach(f => {
        const row = document.createElement('div');
        row.className = 'tl-row';
        const hEnt = f.entrada ? horaDecimal(f.entrada.fecha_hora) : null;
        const hSal = f.salida ? horaDecimal(f.salida.fecha_hora) : null;
        let start = hEnt != null ? hEnt : (hSal != null ? hSal : 8);
        let end = hSal != null ? hSal : (hEnt != null ? hEnt + 0.5 : start + 0.5);
        if (end <= start) end = start + 0.4;
        const left = ((start - minH) / totalH) * 100;
        const width = Math.max(((end - start) / totalH) * 100, 1.5);

        row.innerHTML = `
            <div class="tl-name">
                <span class="tl-dot" style="background:${f.color || '#7c8aff'}"></span>
                <div>
                    <strong>${escapeHtml(f.nombre || '')}</strong>
                    <small>${nombresDias[f.diaIdx] || ''} ${f.fecha.slice(5)} · ${escapeHtml(f.area || '')}</small>
                </div>
            </div>
            <div class="tl-track">
                <div class="tl-bar${f.tardanza ? ' late' : ''}${hEnt != null && hSal != null ? ' complete' : ' partial'}"
                    style="left:${left}%;width:${width}%;background:${f.color || '#7c8aff'}"
                    data-nombre="${escapeHtml(f.nombre || '')}"
                    data-id="${f.id || ''}"
                    data-area="${escapeHtml(f.area || '')}"
                    data-tipo="${escapeHtml(f.tipo || '')}"
                    data-entrada="${f.entrada ? horaDeFecha(f.entrada.fecha_hora) : '--:--'}"
                    data-salida="${f.salida ? horaDeFecha(f.salida.fecha_hora) : '--:--'}"
                    data-color="${f.color || '#d1d5db'}"
                    data-tardanzas="${f.tardanza ? '1' : '0'}">
                    <span>${f.entrada ? horaDeFecha(f.entrada.fecha_hora) : ''}${f.entrada && f.salida ? ' → ' : ''}${f.salida ? horaDeFecha(f.salida.fecha_hora) : ''}</span>
                </div>
            </div>`;
        const bar = row.querySelector('.tl-bar');
        bar.addEventListener('mouseenter', mostrarTooltip);
        bar.addEventListener('mouseleave', ocultarTooltip);
        bar.addEventListener('mousemove', moverTooltip);
        body.appendChild(row);
    });
}

/* Tooltip Carnet */
function mostrarTooltip(e) {
    const t = $('#carnetTooltip');
    const d = e.currentTarget.dataset;
    $('#carnetAvatar').style.background = d.color;
    $('#carnetName').textContent = d.nombre;
    $('#carnetId').textContent = `${d.tipo} — ID: ${d.id || '---'}`;
    $('#carnetCarrera').textContent = d.area || 'Sin área';
    $('#carnetEntrada').textContent = `hora de entrada: ${d.entrada}`;
    $('#carnetSalida').textContent = `hora de salida: ${d.salida}`;
    $('#carnetTardanzas').textContent = `tardanzas: ${d.tardanzas}`;
    t.classList.remove('hidden');
    moverTooltip(e);
}

function moverTooltip(e) {
    const t = $('#carnetTooltip');
    const x = e.clientX + 16;
    const y = e.clientY + 16;
    const rect = t.getBoundingClientRect();
    const winW = window.innerWidth;
    const winH = window.innerHeight;
    let left = x;
    let top = y;
    if (left + rect.width > winW) left = e.clientX - rect.width - 8;
    if (top + rect.height > winH) top = e.clientY - rect.height - 8;
    t.style.left = left + 'px';
    t.style.top = top + 'px';
}

function ocultarTooltip() {
    $('#carnetTooltip').classList.add('hidden');
}

/* Filtro dropdown (multi-selección con botón "aplicar") */
function renderFiltroMenu() {
    const menu = $('#calFiltroMenu');
    const areas = [...new Set(STATE.calData.map(r => r.area).filter(Boolean))];
    menu.innerHTML = '';

    if (areas.length === 0) {
        menu.innerHTML = '<div class="dropdown-item">sin carreras</div>';
        return;
    }

    const lista = document.createElement('div');
    lista.className = 'dropdown-list';

    areas.forEach(area => {
        const checked = STATE.calFiltroAreasPendiente.includes(area);
        const item = document.createElement('label');
        item.className = 'dropdown-checkbox-item' + (checked ? ' active' : '');
        item.innerHTML = `
            <input type="checkbox" ${checked ? 'checked' : ''}>
            <span class="dot" style="background:${getColorForArea(area)}"></span>
            <span class="dropdown-checkbox-label">${area}</span>`;
        const checkbox = item.querySelector('input');
        checkbox.addEventListener('change', () => {
            if (checkbox.checked) {
                if (!STATE.calFiltroAreasPendiente.includes(area)) STATE.calFiltroAreasPendiente.push(area);
            } else {
                STATE.calFiltroAreasPendiente = STATE.calFiltroAreasPendiente.filter(a => a !== area);
            }
            item.classList.toggle('active', checkbox.checked);
        });
        lista.appendChild(item);
    });
    menu.appendChild(lista);

    const footer = document.createElement('div');
    footer.className = 'dropdown-footer';
    footer.innerHTML = `
        <button type="button" class="btn btn-sm btn-ghost" id="calFiltroLimpiar">limpiar</button>
        <button type="button" class="btn btn-sm btn-primary" id="calFiltroAplicar">aplicar</button>`;
    menu.appendChild(footer);

    $('#calFiltroLimpiar').addEventListener('click', () => {
        STATE.calFiltroAreasPendiente = [];
        renderFiltroMenu();
    });
    $('#calFiltroAplicar').addEventListener('click', () => {
        STATE.calFiltroAreas = [...STATE.calFiltroAreasPendiente];
        renderCalGrid();
        cerrarFiltroMenu();
    });
}

function getColorForArea(area) {
    let hash = 0;
    for (let i = 0; i < area.length; i++) hash = area.charCodeAt(i) + ((hash << 5) - hash);
    const c = (hash & 0x00FFFFFF).toString(16).toUpperCase();
    return '#' + '00000'.substring(0, 6 - c.length) + c;
}

function toggleFiltroMenu() {
    const menu = $('#calFiltroMenu');
    const abriendo = menu.classList.contains('hidden');
    if (abriendo) {
        STATE.calFiltroAreasPendiente = [...STATE.calFiltroAreas];
        renderFiltroMenu();
    }
    menu.classList.toggle('hidden');
}

function cerrarFiltroMenu() {
    $('#calFiltroMenu').classList.add('hidden');
}

document.addEventListener('click', e => {
    const menu = $('#calFiltroMenu');
    if (!menu || menu.classList.contains('hidden')) return;
    const path = e.composedPath ? e.composedPath() : [e.target];
    const dentro = path.some(el => el.classList && el.classList.contains('dropdown-wrap'));
    if (!dentro) cerrarFiltroMenu();
});

/* FaceID Modal */
function abrirFaceIdModal() {
    $('#faceIdModal').classList.remove('hidden');
    $('#faceIdResult').classList.add('hidden');
    $('#faceIdPlaceholder').style.display = 'flex';
    $('#faceIdVideo').style.display = 'none';
    iniciarFaceIdCamara();
}

function cerrarFaceIdModal() {
    cleanupFaceId();
    $('#faceIdModal').classList.add('hidden');
}

async function iniciarFaceIdCamara() {
    try {
        STATE.calFaceIdStream = await navigator.mediaDevices.getUserMedia({
            video: { width: 720, height: 720, facingMode: 'user' }
        });
        const vid = $('#faceIdVideo');
        vid.srcObject = STATE.calFaceIdStream;
        vid.style.display = 'block';
        $('#faceIdPlaceholder').style.display = 'none';
        $('#faceIdFrame').style.display = 'flex';
    } catch (err) {
        console.error('No se pudo acceder a cámara para FaceID', err);
    }
}

function cleanupFaceId() {
    if (STATE.calFaceIdStream) {
        STATE.calFaceIdStream.getTracks().forEach(t => t.stop());
        STATE.calFaceIdStream = null;
    }
    const frame = $('#faceIdFrame');
    if (frame) frame.style.display = 'none';
}

async function escanearFaceId() {
    const vid = $('#faceIdVideo');
    const canv = $('#faceIdCanvas');
    if (!vid || vid.style.display === 'none') return;

    canv.width = vid.videoWidth;
    canv.height = vid.videoHeight;
    const ctx = canv.getContext('2d');
    ctx.drawImage(vid, 0, 0, canv.width, canv.height);
    const dataUrl = canv.toDataURL('image/jpeg', 0.85);

    $('#btnFaceIdScan').disabled = true;
    $('#btnFaceIdScan').textContent = 'Escaneando...';

    try {
        const res = await fetch('/api/identificar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ imagen: dataUrl })
        });
        const data = await res.json();

        const resultEl = $('#faceIdResult');
        const resultText = $('#faceIdResultText');
        const resultAvatar = $('#faceIdResultAvatar');

        resultEl.classList.remove('hidden');
        if (data.ok) {
            resultAvatar.style.background = '#4dffb0';
            resultText.innerHTML = `<strong>${escapeHtml(data.nombre)}</strong><br><small>identificado — ${data.confianza}% de coincidencia</small>`;
            STATE.calBusqueda = data.nombre.toLowerCase();
            $('#calSearch').value = data.nombre;
            renderCalGrid();
        } else {
            resultAvatar.style.background = '#ff5d5d';
            resultText.innerHTML = `<small>${escapeHtml(data.mensaje || 'No reconocido')}</small>`;
        }
    } catch (e) {
        $('#faceIdResult').classList.remove('hidden');
        $('#faceIdResultAvatar').style.background = '#ff5d5d';
        $('#faceIdResultText').innerHTML = '<small>Error de conexión</small>';
    } finally {
        $('#btnFaceIdScan').disabled = false;
        $('#btnFaceIdScan').textContent = 'Escanear';
    }
}

/* ═══════════════════════════════════════════════════════
   PÁGINA: NÚMEROS TELEFÓNICOS
   ═══════════════════════════════════════════════════════ */
STATE.telefonosCache = [];
STATE.telFiltroQ = '';
STATE.telFiltroCargo = '';
STATE.telFiltroCarrera = '';

async function initTelefonos() {
    const sel = $('#telFilterCarrera');
    if (sel && sel.options.length <= 1) {
        (STATE.areasSENATI || []).forEach(a => {
            const o = document.createElement('option');
            o.value = a;
            o.textContent = a;
            sel.appendChild(o);
        });
    }
    if (!$('#telSearch')?.dataset.bound) {
        $('#telSearch')?.addEventListener('input', debounce(e => {
            STATE.telFiltroQ = (e.target.value || '').trim().toLowerCase();
            renderTelefonos();
        }, 150));
        $('#telFilterCargo')?.addEventListener('change', e => {
            STATE.telFiltroCargo = e.target.value || '';
            renderTelefonos();
        });
        $('#telFilterCarrera')?.addEventListener('change', e => {
            STATE.telFiltroCarrera = e.target.value || '';
            renderTelefonos();
        });
        if ($('#telSearch')) $('#telSearch').dataset.bound = '1';
    }
    try {
        const res = await fetch('/api/personal/publico');
        if (res.ok) {
            STATE.telefonosCache = await res.json();
        } else {
            // Fallback: si no hay endpoint público, lista vacía
            STATE.telefonosCache = [];
        }
    } catch (e) {
        STATE.telefonosCache = [];
    }
    renderTelefonos();
}

function renderTelefonos() {
    const grid = $('#telefonosGrid');
    const empty = $('#emptyTelefonos');
    if (!grid) return;
    const items = (STATE.telefonosCache || []).filter(p => {
        const matchQ = !STATE.telFiltroQ || (p.nombre || '').toLowerCase().includes(STATE.telFiltroQ);
        const matchCargo = !STATE.telFiltroCargo || (p.tipo_personal || '') === STATE.telFiltroCargo;
        const matchCarrera = !STATE.telFiltroCarrera || (p.area || '') === STATE.telFiltroCarrera;
        return matchQ && matchCargo && matchCarrera;
    });
    if (!items.length) {
        grid.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';
    grid.innerHTML = items.map(p => `
        <div class="tel-card">
            <div class="tel-avatar" style="background:${p.color || '#d1d5db'}">${p.icono_path || p.foto_path ? `<img src="${escapeHtml(p.icono_path || p.foto_path)}" alt="">` : ''}</div>
            <div class="tel-body">
                <div class="tel-name">${escapeHtml(p.nombre || '')}</div>
                <div class="tel-role">${escapeHtml(p.tipo_personal || 'personal')}${p.cargo ? ' · ' + escapeHtml(p.cargo) : ''}</div>
                <div class="tel-area">${escapeHtml(p.area || '—')}</div>
                <div class="tel-phone">${escapeHtml(p.telefono || 'sin número')}</div>
            </div>
        </div>
    `).join('');
}

/* ═══════════════════════════════════════════════════════
   PÁGINA: ADMIN
   ═══════════════════════════════════════════════════════ */

function initAdmin() {
    const auth = sessionStorage.getItem('senati_admin_auth');
    if (auth === '1') {
        STATE.adminAutenticado = true;
        mostrarDashboardAdmin();
    } else {
        mostrarLoginAdmin();
    }
}

function mostrarLoginAdmin() {
    $('#adminLogin').classList.remove('hidden');
    $('#adminDashboard').classList.add('hidden');
    $('#adminPass').value = '';
    $('#loginError').classList.add('hidden');
    $('#adminPass').focus();
}

function mostrarDashboardAdmin() {
    $('#adminLogin').classList.add('hidden');
    $('#adminDashboard').classList.remove('hidden');
    cargarPersonal();
    renderAdminTab(STATE.adminTab);
}

/* Login */
$('#btnAdminLogin')?.addEventListener('click', verificarAdminLogin);
$('#adminPass')?.addEventListener('keydown', e => { if (e.key === 'Enter') verificarAdminLogin(); });

async function verificarAdminLogin() {
    const pass = $('#adminPass').value;
    const btn = $('#btnAdminLogin');
    btn.disabled = true;
    try {
        const res = await fetch('/api/admin/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pass })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            sessionStorage.setItem('senati_admin_auth', '1');
            STATE.adminAutenticado = true;
            mostrarDashboardAdmin();
        } else {
            $('#loginError').classList.remove('hidden');
            $('#adminPass').value = '';
            $('#adminPass').focus();
        }
    } catch (e) {
        $('#loginError').classList.remove('hidden');
        $('#loginError').textContent = 'error de conexión con el servidor.';
    } finally {
        btn.disabled = false;
    }
}

$('#btnAdminLogout')?.addEventListener('click', async () => {
    try { await fetch('/api/admin/logout', { method: 'POST' }); } catch (e) { /* ignora errores de red al salir */ }
    sessionStorage.removeItem('senati_admin_auth');
    STATE.adminAutenticado = false;
    cleanupAdminCam();
    mostrarLoginAdmin();
});

/* Tabs internos */
$('#adminTabs')?.addEventListener('click', e => {
    const btn = e.target.closest('.admin-tab');
    if (!btn) return;
    STATE.adminTab = btn.dataset.adminTab;
    renderAdminTab(STATE.adminTab);
});

$('#btnIrAgregar')?.addEventListener('click', () => {
    STATE.adminTab = 'agregar';
    renderAdminTab('agregar');
});

function renderAdminTab(tab) {
    $$('.admin-tab').forEach(b => b.classList.toggle('active', b.dataset.adminTab === tab));
    $$('.nav-item').forEach(el => { if (el.dataset.adminTab) el.classList.toggle('active', el.dataset.page === 'admin' && el.dataset.adminTab === tab); });
    $$('.admin-panel').forEach(p => p.classList.toggle('hidden', p.id !== `panel-${tab}`));
    const activo = $(`#panel-${tab}`);
    if (activo) {
        activo.style.animation = 'none';
        void activo.offsetHeight;
        activo.style.animation = '';
    }
    if (tab === 'personal') cargarPersonal();
    if (tab === 'agregar') initAgregar();
    if (tab === 'reportes') initReportes();
    if (tab === 'config') initConfig();
    if (tab === 'horario') initPanelHorario();
    if (tab === 'reparacion') initReparacion();
}

/* ---------- PANEL HORARIO (admin) ---------- */
STATE.horarioPersonalLista = [];

async function initPanelHorario() {
    const selArea = $('#horarioFilterArea');
    if (selArea && selArea.options.length <= 1) {
        (STATE.areasSENATI || []).forEach(a => {
            const o = document.createElement('option');
            o.value = a;
            o.textContent = a;
            selArea.appendChild(o);
        });
    }
    if (!$('#horarioSearch')?.dataset.bound) {
        $('#horarioSearch')?.addEventListener('input', debounce(() => renderTablaHorarioPersonal(), 150));
        $('#horarioFilterTipo')?.addEventListener('change', () => renderTablaHorarioPersonal());
        $('#horarioFilterArea')?.addEventListener('change', () => renderTablaHorarioPersonal());
        $('#btnAgregarBloqueHorario')?.addEventListener('click', agregarBloqueDesdeEditor);
        if ($('#horarioSearch')) $('#horarioSearch').dataset.bound = '1';
    }
    try {
        const res = await fetch('/api/personal');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        STATE.horarioPersonalLista = await res.json();
    } catch (e) {
        STATE.horarioPersonalLista = [];
    }
    renderTablaHorarioPersonal();
}

function renderTablaHorarioPersonal() {
    const tbody = $('#tablaHorarioPersonal tbody');
    const empty = $('#emptyHorarioPersonal');
    if (!tbody) return;
    const q = ($('#horarioSearch')?.value || '').trim().toLowerCase();
    const tipo = $('#horarioFilterTipo')?.value || '';
    const area = $('#horarioFilterArea')?.value || '';
    const items = (STATE.horarioPersonalLista || []).filter(p => {
        if (q && !(p.nombre || '').toLowerCase().includes(q)) return false;
        if (tipo && p.tipo_personal !== tipo) return false;
        if (area && p.area !== area) return false;
        return true;
    });
    if (!items.length) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = 'flex';
        return;
    }
    if (empty) empty.style.display = 'none';
    tbody.innerHTML = items.map(p => {
        const foto = p.icono_path || p.foto_path;
        const avatar = foto
            ? `<img class="table-avatar-img" src="${escapeHtml(foto)}" alt="">`
            : `<div class="table-avatar" style="background:${p.color || '#d1d5db'}"></div>`;
        return `<tr>
            <td class="col-foto">${avatar}</td>
            <td><strong>${escapeHtml(p.nombre || '')}</strong></td>
            <td><span class="badge tipo-${p.tipo_personal}">${escapeHtml(p.tipo_personal || '')}</span></td>
            <td>${escapeHtml(p.area || '—')}</td>
            <td class="hint-inline" id="bloquesCount-${p.id}">—</td>
            <td><button type="button" class="btn-horario" onclick="abrirEditorHorario('${p.id}', '${escapeHtml(p.nombre || '')}')">HORARIO</button></td>
        </tr>`;
    }).join('');
    // Cargar conteo de bloques en paralelo (ligero)
    items.slice(0, 40).forEach(async p => {
        try {
            const r = await fetch(`/api/personal/${p.id}/horarios`);
            if (!r.ok) return;
            const bloques = await r.json();
            const el = document.getElementById(`bloquesCount-${p.id}`);
            if (el) el.textContent = (bloques || []).length + ' bloque(s)';
        } catch (e) { /* ignore */ }
    });
}

window.abrirEditorHorario = async function (personalId, nombre) {
    const modal = $('#modalHorarioEditor');
    if (!modal) {
        alert('No se encontró el editor de horario. Recarga la página.');
        return;
    }
    const pid = $('#horarioEditorPersonalId');
    if (pid) pid.value = personalId;
    const tit = $('#horarioEditorTitulo');
    if (tit) tit.textContent = `horario — ${nombre}`;
    resetModalPos(modal);
    modal.classList.remove('hidden');

    const selC = $('#heCarrera');
    if (selC && selC.options.length <= 1) {
        (STATE.areasSENATI || []).forEach(a => {
            const o = document.createElement('option');
            o.value = a; o.textContent = a; selC.appendChild(o);
        });
    }
    const btnPdf = $('#btnExportHorarioPdf');
    if (btnPdf) {
        btnPdf.onclick = () => { window.open(`/api/personal/${personalId}/horarios/pdf`, '_blank'); };
    }
    try {
        const st = await fetch('/api/ia/estado').then(r => r.json()).catch(() => ({}));
        const lbl = $('#iaEstadoLabel');
        if (lbl) lbl.textContent = st.gemini_configurado ? `Gemini listo (${st.modelo || 'ok'})` : 'sin GEMINI_API_KEY en .env';
        if ($('#btnIaImportar')) $('#btnIaImportar').disabled = true;
    } catch (e) {
        const lbl = $('#iaEstadoLabel');
        if (lbl) lbl.textContent = 'IA no disponible';
    }
    STATE._iaHorarioDataUrl = null;
    const nm = $('#iaHorarioNombre');
    if (nm) nm.textContent = 'ningún archivo';
    const msg = $('#iaImportMsg');
    if (msg) msg.textContent = '';
    if ($('#btnIaImportar')) $('#btnIaImportar').disabled = true;
    await recargarBloquesEditor(personalId);
};

function initIaHorarioUI() {
    if ($('#btnIaElegirArchivo')?.dataset.bound) return;
    $('#btnIaElegirArchivo')?.addEventListener('click', () => $('#iaHorarioFile')?.click());
    $('#iaHorarioFile')?.addEventListener('change', e => {
        const file = e.target.files?.[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = ev => {
            STATE._iaHorarioDataUrl = ev.target.result;
            const nm = $('#iaHorarioNombre');
            if (nm) nm.textContent = file.name;
            $('#btnIaImportar').disabled = false;
        };
        reader.readAsDataURL(file);
    });
    $('#btnIaImportar')?.addEventListener('click', importarHorarioConIA);
    if ($('#btnIaElegirArchivo')) $('#btnIaElegirArchivo').dataset.bound = '1';
}

async function importarHorarioConIA() {
    const personalId = $('#horarioEditorPersonalId')?.value;
    if (!personalId || !STATE._iaHorarioDataUrl) return;
    const msg = $('#iaImportMsg');
    if (msg) msg.textContent = 'analizando con Gemini…';
    $('#btnIaImportar').disabled = true;
    try {
        const res = await fetch(`/api/personal/${personalId}/horarios/importar-ia`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                imagen: STATE._iaHorarioDataUrl,
                reemplazar: !!$('#iaReemplazar')?.checked,
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        if (!res.ok || !data.ok) {
            if (msg) msg.textContent = data.mensaje || 'No se pudo importar.';
            $('#btnIaImportar').disabled = false;
            return;
        }
        if (msg) msg.textContent = `✓ ${data.insertados} bloque(s) importados de ${data.total_detectados} detectados.`;
        await recargarBloquesEditor(personalId);
        renderTablaHorarioPersonal();
    } catch (e) {
        if (msg) msg.textContent = 'Error de conexión con el servidor.';
    }
    $('#btnIaImportar').disabled = false;
}

document.addEventListener('DOMContentLoaded', () => { try { initIaHorarioUI(); } catch (e) {} });


async function recargarBloquesEditor(personalId) {
    const lista = $('#horarioEditorLista');
    lista.innerHTML = '<p class="hint-inline">cargando...</p>';
    try {
        const res = await fetch(`/api/personal/${personalId}/horarios`);
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        const bloques = await res.json();
        if (!bloques.length) {
            lista.innerHTML = '<p class="hint-inline">sin bloques. agrega el primero abajo.</p>';
            return;
        }
        const dias = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
        lista.innerHTML = bloques.map(b => `
            <div class="horario-bloque-row" style="grid-template-columns: 80px 60px 12px 60px 1.2fr 36px;">
                <span>${dias[b.dia_semana] || b.dia_semana}</span>
                <span>${b.hora_inicio}</span>
                <span>→</span>
                <span>${b.hora_fin}</span>
                <span>${escapeHtml(b.materia || '—')}${b.aula ? ' · salón ' + escapeHtml(b.aula) : ''}${b.carrera ? ' · ' + escapeHtml(b.carrera) : ''}${b.semestre ? ' · sem. ' + escapeHtml(String(b.semestre)) : ''} · tol. ${b.tolerancia_min ?? 5} min</span>
                <button type="button" class="btn-icon-sm danger" title="Eliminar" onclick="eliminarBloqueHorario('${b.id}', '${personalId}')">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </div>
        `).join('');
    } catch (e) {
        lista.innerHTML = '<p class="hint-inline">error al cargar bloques.</p>';
    }
}

window.eliminarBloqueHorario = async function (bloqueId, personalId) {
    try {
        const res = await fetch(`/api/horarios/${bloqueId}`, { method: 'DELETE' });
        if (res.ok) recargarBloquesEditor(personalId);
        else alert('No se pudo eliminar el bloque.');
    } catch (e) {
        alert('Error de conexión.');
    }
};

async function agregarBloqueDesdeEditor() {
    const personalId = $('#horarioEditorPersonalId').value;
    if (!personalId) return;
    const payload = {
        dia_semana: parseInt($('#heDia').value, 10),
        hora_inicio: $('#heInicio').value,
        hora_fin: $('#heFin').value,
        materia: ($('#heMateria').value || '').trim() || null,
        aula: ($('#heAula')?.value || '').trim() || null,
        carrera: ($('#heCarrera')?.value || '').trim() || null,
        semestre: ($('#heSemestre')?.value || '').trim() || null,
        tolerancia_min: parseInt($('#heTolerancia').value, 10) || 5,
        modalidad: 'presencial'
    };
    if (!payload.hora_inicio || !payload.hora_fin) {
        alert('Indica hora de inicio y fin.');
        return;
    }
    try {
        const res = await fetch(`/api/personal/${personalId}/horarios`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok !== false) {
            $('#heMateria').value = '';
            if ($('#heAula')) $('#heAula').value = '';
            await recargarBloquesEditor(personalId);
            renderTablaHorarioPersonal();
        } else {
            alert(data.mensaje || 'No se pudo agregar el bloque.');
        }
    } catch (e) {
        alert('Error de conexión.');
    }
}

/* ---------- PERSONAL ---------- */
async function cargarPersonal() {
    try {
        const res = await fetch('/api/personal');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        STATE.personalCache = await res.json();
        filtrarPersonal();
        poblarSelectAreas();
    } catch (e) {
        STATE.personalCache = [];
        renderTablaPersonal([]);
    }
}

function poblarSelectAreas() {
    const sel = $('#adminFilterArea');
    if (!sel || sel.dataset.poblado) return;
    const areas = [...new Set(STATE.personalCache.map(p => p.area).filter(Boolean))];
    areas.forEach(a => {
        const opt = document.createElement('option');
        opt.value = a;
        opt.textContent = a;
        sel.appendChild(opt);
    });
    sel.dataset.poblado = '1';
}

function filtrarPersonal() {
    const q = ($('#adminSearch')?.value || '').toLowerCase().trim();
    const tipo = $('#adminFilterTipo')?.value || '';
    const area = $('#adminFilterArea')?.value || '';

    STATE.personalFiltrado = STATE.personalCache.filter(p => {
        const matchQ = !q || (p.nombre || '').toLowerCase().includes(q) || (p.dni || '').includes(q) || (p.area || '').toLowerCase().includes(q);
        const matchTipo = !tipo || p.tipo_personal === tipo;
        const matchArea = !area || p.area === area;
        return matchQ && matchTipo && matchArea;
    });
    renderTablaPersonal(STATE.personalFiltrado);
    actualizarStats();
}

function actualizarStats() {
    const total = STATE.personalCache.length;
    const activos = STATE.personalCache.filter(p => p.activo).length;
    $('#statTotal').textContent = total;
    $('#statActivos').textContent = activos;
    $('#statInactivos').textContent = total - activos;
}

function renderTablaPersonal(data) {
    const tbody = $('#tablaPersonal tbody');
    const empty = $('#emptyPersonal');
    if (!data || data.length === 0) {
        tbody.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    const frag = document.createDocumentFragment();
    data.forEach(p => {
        const tr = document.createElement('tr');
        const avatarSrc = p.icono_path || p.foto_path;
        const avatarHtml = avatarSrc
            ? `<img class="table-avatar-img" src="${escapeHtml(avatarSrc)}" alt="${escapeHtml(p.nombre)}">`
            : `<div class="table-avatar" style="background:${p.color || '#d1d5db'}"></div>`;
        tr.innerHTML = `
            <td class="col-foto">${avatarHtml}</td>
            <td class="col-nombre"><strong>${escapeHtml(p.nombre)}</strong></td>
            <td class="col-dni">${escapeHtml(p.dni) || 'N/A'}</td>
            <td class="col-codigo">${escapeHtml(p.codigo) || '—'}</td>
            <td class="col-tipo"><span class="badge tipo-${p.tipo_personal}">${escapeHtml(p.tipo_personal)}</span></td>
            <td class="col-area">${escapeHtml(p.area) || '—'}</td>
            <td class="col-cargo">${escapeHtml(p.cargo) || '—'}</td>
            <td class="col-estado"><span class="badge estado-${p.activo ? 'activo' : 'inactivo'}">${p.activo ? 'activo' : 'inactivo'}</span></td>
            <td class="col-acciones">
                <button class="btn-icon-sm" title="Ver" onclick="verDetalle('${p.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg></button>
                <button class="btn-icon-sm" title="Editar" onclick="abrirEditar('${p.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"></path></svg></button>
                <button class="btn-icon-sm" title="${p.activo ? 'Desactivar' : 'Activar'}" onclick="toggleActivo('${p.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M5 12l5 5L20 7"></path></svg></button>
                <button class="btn-icon-sm danger" title="Eliminar" onclick="confirmarEliminar('${p.id}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
            </td>`;
        frag.appendChild(tr);
    });
    tbody.innerHTML = '';
    tbody.appendChild(frag);
}

$('#adminSearch')?.addEventListener('input', debounce(filtrarPersonal, 150));
$('#adminFilterTipo')?.addEventListener('change', filtrarPersonal);
$('#adminFilterArea')?.addEventListener('change', filtrarPersonal);
$('#btnRefreshPersonal')?.addEventListener('click', cargarPersonal);

/* Acciones tabla */
window.verDetalle = async function (id) {
    const sid = String(id);
    const p = STATE.personalCache.find(x => String(x.id) === sid);
    if (!p) return;
    STATE.detalleActualId = p.id;
    const fotoDetalle = p.icono_path || p.foto_path;
    const detAvatar = $('#detAvatar');
    if (fotoDetalle) {
        detAvatar.style.background = '';
        detAvatar.style.backgroundImage = `url('${fotoDetalle}')`;
        detAvatar.style.backgroundSize = 'cover';
        detAvatar.style.backgroundPosition = 'center';
    } else {
        detAvatar.style.backgroundImage = '';
        detAvatar.style.background = p.color || '#d1d5db';
    }
    $('#detNombre').textContent = p.nombre;
    $('#detMeta').textContent = `${p.tipo_personal.toUpperCase()} — DNI: ${p.dni || 'N/A'}`;
    $('#detBody').innerHTML = `
        <div class="detalle-row"><span>ID / código:</span><span>${escapeHtml(p.codigo || '—')}</span></div>
        <div class="detalle-row"><span>Carrera:</span><span>${escapeHtml(p.area || '—')}</span></div>
        <div class="detalle-row"><span>Cargo:</span><span>${escapeHtml(p.cargo || '—')}</span></div>
        <div class="detalle-row"><span>Carrera a cargo:</span><span>${escapeHtml(p.curso || '—')}</span></div>
        <div class="detalle-row"><span>Semestre:</span><span>${escapeHtml(p.semestre || '—')}</span></div>
        <div class="detalle-row"><span>Estado:</span><span class="badge estado-${p.activo ? 'activo' : 'inactivo'}">${p.activo ? 'activo' : 'inactivo'}</span></div>
        <div class="detalle-row"><span>Registrado:</span><span>${escapeHtml(p.fecha_registro || '—')}</span></div>
        <div class="detalle-stats-loading hint-inline">cargando estadísticas...</div>
    `;
    const md = $('#modalDetalle');
    if (md) {
        resetModalPos(md);
        md.classList.remove('hidden');
    }

    try {
        const res = await fetch(`/api/personal/${p.id}/estadisticas`);
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        const st = await res.json();
        if (!res.ok || !st.ok) {
            const el = $('#detBody')?.querySelector('.detalle-stats-loading');
            if (el) el.textContent = 'no se pudieron cargar las estadísticas.';
            return;
        }
        const statsHtml = `
            <div class="detalle-divider"></div>
            <div class="detalle-section-title">asistencia</div>
            <div class="detalle-row"><span>Asistencia perfecta:</span><span><strong>${st.porcentaje_asistencia}%</strong></span></div>
            <div class="detalle-row"><span>Días con registro:</span><span>${st.dias_con_registro}</span></div>
            <div class="detalle-row"><span>Días completos (entrada+salida):</span><span>${st.dias_completos}</span></div>
            <div class="detalle-row"><span>Días incompletos:</span><span>${st.dias_incompletos}</span></div>
            <div class="detalle-row"><span>Entradas:</span><span>${st.total_entradas}</span></div>
            <div class="detalle-row"><span>Salidas:</span><span>${st.total_salidas}</span></div>
            <div class="detalle-row"><span>Tardanzas:</span><span>${st.tardanzas}</span></div>
            <div class="detalle-row"><span>Confianza promedio:</span><span>${st.confianza_promedio}%</span></div>
            <div class="detalle-divider"></div>
            <div class="detalle-section-title">permisos / justificaciones</div>
            <div class="detalle-row"><span>Total solicitados:</span><span>${st.permisos_total}</span></div>
            <div class="detalle-row"><span>Aprobados:</span><span>${st.permisos_aprobados}</span></div>
            <div class="detalle-row"><span>Pendientes:</span><span>${st.permisos_pendientes}</span></div>
            <div class="detalle-row"><span>Rechazados:</span><span>${st.permisos_rechazados}</span></div>
        `;
        const loading = $('#detBody')?.querySelector('.detalle-stats-loading');
        if (loading) {
            loading.outerHTML = statsHtml;
        } else {
            $('#detBody').innerHTML += statsHtml;
        }
    } catch (e) {
        console.error(e);
        const el = $('#detBody')?.querySelector('.detalle-stats-loading');
        if (el) el.textContent = 'error al cargar estadísticas.';
    }
};

$('#btnEditarDesdeDetalle')?.addEventListener('click', () => {
    if (!STATE.detalleActualId) return;
    cerrarModal('modalDetalle');
    abrirEditar(STATE.detalleActualId);
});

window.toggleActivo = async function (id) {
    try {
        const res = await fetch(`/api/personal/${id}/toggle`, { method: 'POST' });
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        if (res.ok) {
            cargarPersonal();
        } else {
            const data = await res.json().catch(() => ({}));
            alert(data.mensaje || 'No se pudo cambiar el estado.');
        }
    } catch (e) {
        console.error(e);
        alert('Error de conexión al cambiar el estado.');
    }
};

/* ---------- EDITAR PERSONAL ---------- */
window.abrirEditar = function (id) {
    const sid = String(id);
    const p = STATE.personalCache.find(x => String(x.id) === sid);
    if (!p) return;
    poblarSelectCarreras($('#editArea'));
    poblarSelectCarreras($('#editCurso'));

    $('#editId').value = p.id;
    $('#editNombre').value = p.nombre || '';
    $('#editDni').value = p.dni || '';
    $('#editCodigo').value = p.codigo || '';
    $('#editTipo').value = p.tipo_personal || 'instructor';
    $('#editArea').value = p.area || '';
    $('#editCurso').value = p.curso || '';
    $('#editSemestre').value = p.semestre || '';
    $('#editCargo').value = p.cargo || '';

    aplicarCamposEditPorTipo(p.tipo_personal || 'instructor');

    const me = $('#modalEditar');
    if (me) {
        resetModalPos(me);
        me.classList.remove('hidden');
    }
    cargarHorarios(p.id);
};

const DIAS_SEMANA = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];

async function cargarHorarios(personalId) {
    const lista = $('#horarioList');
    lista.innerHTML = '<p class="hint-inline">cargando...</p>';
    try {
        const res = await fetch(`/api/personal/${personalId}/horarios`);
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        const bloques = await res.json();
        renderHorarios(bloques, personalId);
    } catch (e) {
        lista.innerHTML = '<p class="hint-inline">no se pudo cargar el horario.</p>';
    }
}

function renderHorarios(bloques, personalId) {
    const lista = $('#horarioList');
    if (!bloques.length) {
        lista.innerHTML = '<p class="hint-inline">sin bloques registrados todavía.</p>';
        return;
    }
    lista.innerHTML = '';
    bloques.forEach(b => {
        const item = document.createElement('div');
        item.className = 'horario-item';
        item.innerHTML = `
            <span class="dia">${DIAS_SEMANA[b.dia_semana]}</span>
            <span class="rango">${b.hora_inicio} – ${b.hora_fin}</span>
            <span class="materia">${b.materia || 'sin materia'}</span>
            <button type="button" class="del-bloque" title="Eliminar bloque">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
            </button>`;
        item.querySelector('.del-bloque').addEventListener('click', () => eliminarBloque(b.id, personalId));
        lista.appendChild(item);
    });
}

async function eliminarBloque(bloqueId, personalId) {
    try {
        const res = await fetch(`/api/horarios/${bloqueId}`, { method: 'DELETE' });
        if (res.ok) cargarHorarios(personalId);
    } catch (e) { console.error(e); }
}

$('#btnAgregarBloque')?.addEventListener('click', async () => {
    const personalId = $('#editId').value;
    if (!personalId) return;
    const estado = $('#hbEstado');
    estado.textContent = '';
    estado.className = 'repair-status';

    const payload = {
        dia_semana: parseInt($('#hbDia').value),
        hora_inicio: $('#hbInicio').value,
        hora_fin: $('#hbFin').value,
        materia: $('#hbMateria').value.trim() || null
    };
    if (!payload.hora_inicio || !payload.hora_fin) {
        estado.textContent = 'indica hora de inicio y fin.';
        estado.className = 'repair-status error';
        return;
    }

    try {
        const res = await fetch(`/api/personal/${personalId}/horarios`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            $('#hbMateria').value = '';
            cargarHorarios(personalId);
        } else {
            estado.textContent = data.mensaje || 'no se pudo agregar el bloque.';
            estado.className = 'repair-status error';
        }
    } catch (e) {
        estado.textContent = 'error de conexión.';
        estado.className = 'repair-status error';
    }
});

$('#editTipo')?.addEventListener('change', e => aplicarCamposEditPorTipo(e.target.value));

function aplicarCamposEditPorTipo(tipo) {
    const codigoInput = $('#editCodigo');
    const esInstructor = tipo === 'instructor';
    const esPersonal = tipo === 'personal';
    const esJefe = tipo === 'jefe';

    $('#wrapEditCodigo').classList.toggle('hidden', esPersonal);
    codigoInput.required = esInstructor;
    $('#reqEditCodigo').style.display = esInstructor ? 'inline' : 'none';
    $('#hintEditCodigo').style.display = esJefe ? 'block' : 'none';

    $('#wrapEditArea').classList.toggle('hidden', !esInstructor);
    $('#editArea').required = esInstructor;
    $('#wrapEditCurso').classList.toggle('hidden', !esInstructor);
    $('#wrapEditSemestre').classList.toggle('hidden', !esInstructor);

    $('#wrapEditCargo').classList.toggle('hidden', !esPersonal);
    $('#editCargo').required = esPersonal;
}

$('#btnGuardarEdicion')?.addEventListener('click', async () => {
    const id = $('#editId').value;
    if (!id) return;
    const btn = $('#btnGuardarEdicion');
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'guardando...';

    const tipo = $('#editTipo').value;
    const payload = {
        nombre: $('#editNombre').value.trim(),
        dni: $('#editDni').value.trim(),
        codigo: tipo === 'personal' ? null : $('#editCodigo').value.trim(),
        tipo_personal: tipo,
        area: tipo === 'instructor' ? $('#editArea').value : null,
        curso: $('#editCurso').value || null,
        semestre: $('#editSemestre').value || null,
        cargo: tipo === 'personal' ? $('#editCargo').value.trim() : null
    };

    try {
        const res = await fetch(`/api/personal/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
            cerrarModal('modalEditar');
            cargarPersonal();
        } else {
            alert(data.mensaje || 'No se pudo guardar los cambios.');
        }
    } catch (e) {
        alert('Error de conexión al guardar los cambios.');
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
});

window.confirmarEliminar = function (id) {
    const sid = String(id);
    const p = STATE.personalCache.find(x => String(x.id) === sid);
    STATE.adminEliminarId = p ? p.id : id;
    $('#txtEliminar').textContent = `¿Eliminar a ${p ? p.nombre : 'esta persona'}? Esta acción no se puede deshacer.`;
    $('#modalEliminar').classList.remove('hidden');
};

$('#btnCancelEliminar')?.addEventListener('click', () => {
    $('#modalEliminar').classList.add('hidden');
    STATE.adminEliminarId = null;
});

$('#btnConfirmEliminar')?.addEventListener('click', async () => {
    if (!STATE.adminEliminarId) return;
    try {
        const res = await fetch(`/api/personal/${STATE.adminEliminarId}`, { method: 'DELETE' });
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        if (res.ok) {
            cargarPersonal();
            $('#modalEliminar').classList.add('hidden');
            STATE.adminEliminarId = null;
        } else {
            const data = await res.json().catch(() => ({}));
            alert(data.mensaje || 'No se pudo eliminar el personal.');
        }
    } catch (e) {
        console.error(e);
        alert('Error de conexión al eliminar.');
    }
});

window.cerrarModal = function (id) {
    const ov = document.getElementById(id);
    if (!ov) return;
    ov.classList.add('hidden');
    resetModalPos(ov);
};

function resetModalPos(overlay) {
    const card = overlay?.querySelector?.('.modal-card');
    if (!card) return;
    card.style.left = '';
    card.style.top = '';
    card.style.position = '';
    card.style.transform = '';
    card.style.margin = '';
}

function hoistOverlaysToBody() {
    document.querySelectorAll('.modal-overlay').forEach(el => {
        if (el.parentElement !== document.body) document.body.appendChild(el);
    });
    ['datePickerBackdrop', 'datePickerPanel'].forEach(id => {
        const el = document.getElementById(id);
        if (el && el.parentElement !== document.body) document.body.appendChild(el);
    });
}

function initDraggableModals() {
    document.querySelectorAll('.modal-overlay .modal-card').forEach(card => {
        const handle = card.querySelector('.modal-drag-bar') || card.querySelector('h3');
        if (!handle || handle.dataset.dragBound) return;
        handle.dataset.dragBound = '1';
        handle.classList.add('modal-drag-bar');
        handle.addEventListener('mousedown', e => {
            if (e.button !== 0) return;
            if (e.target.closest('button, input, select, a, textarea, label')) return;
            e.preventDefault();
            const rect = card.getBoundingClientRect();
            const ox = e.clientX - rect.left;
            const oy = e.clientY - rect.top;
            card.style.position = 'fixed';
            card.style.margin = '0';
            card.style.left = rect.left + 'px';
            card.style.top = rect.top + 'px';
            card.style.transform = 'none';
            const move = ev => {
                const maxX = window.innerWidth - 80;
                const maxY = window.innerHeight - 48;
                card.style.left = Math.min(maxX, Math.max(8, ev.clientX - ox)) + 'px';
                card.style.top = Math.min(maxY, Math.max(8, ev.clientY - oy)) + 'px';
            };
            const up = () => {
                document.removeEventListener('mousemove', move);
                document.removeEventListener('mouseup', up);
            };
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    hoistOverlaysToBody();
    initDraggableModals();
});

/* ---------- AGREGAR PERSONAL ---------- */
function initAgregar() {
    poblarSelectCarreras($('#agArea'));
    poblarSelectCarreras($('#agCurso'));
    aplicarCamposPorTipo($('#agTipo').value);
}

function poblarSelectCarreras(select) {
    if (!select || select.dataset.poblado === '1') return;
    STATE.areasSENATI.forEach(area => {
        const opt = document.createElement('option');
        opt.value = area;
        opt.textContent = area;
        select.appendChild(opt);
    });
    select.dataset.poblado = '1';
}

$('#agTipo')?.addEventListener('change', e => aplicarCamposPorTipo(e.target.value));

function aplicarCamposPorTipo(tipo) {
    const codigoInput = $('#agCodigo');
    const esInstructor = tipo === 'instructor';
    const esPersonal = tipo === 'personal';
    const esJefe = tipo === 'jefe';

    // ID / código: obligatorio para instructor, opcional para jefe, oculto para personal
    $('#wrapCodigo').classList.toggle('hidden', esPersonal);
    codigoInput.required = esInstructor;
    $('#reqCodigo').style.display = esInstructor ? 'inline' : 'none';
    $('#hintCodigo').style.display = esJefe ? 'block' : 'none';
    codigoInput.placeholder = esJefe ? 'opcional — deja en blanco si no aplica' : 'ej: 001664273';

    // Carrera / carrera a cargo / semestre: solo instructor
    $('#wrapArea').classList.toggle('hidden', !esInstructor);
    $('#agArea').required = esInstructor;
    $('#wrapCurso')?.classList.toggle('hidden', !esInstructor);
    // semestre se define en bloques de horario, no al crear usuario

    // Tipo de personal (función): solo personal
    $('#wrapCargo').classList.toggle('hidden', !esPersonal);
    $('#agCargo').required = esPersonal;
}

/* Ícono de perfil (independiente de la foto facial) */
$('#agIconFile')?.addEventListener('change', e => {
    manejarArchivoIcono(e.target.files[0]);
});

function manejarArchivoIcono(file) {
    if (!file || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = ev => {
        STATE.adminIconBase64 = ev.target.result;
        mostrarPreviewIcono(ev.target.result);
    };
    reader.readAsDataURL(file);
}

function mostrarPreviewIcono(src) {
    const img = $('#agIconPreview');
    img.src = src;
    img.classList.remove('hidden');
    $('#adminIconPlaceholder').classList.add('hidden');
}

function limpiarPreviewIcono() {
    STATE.adminIconBase64 = null;
    $('#agIconPreview').classList.add('hidden');
    $('#agIconPreview').src = '';
    $('#adminIconPlaceholder').classList.remove('hidden');
}

(function initDragDropIcono() {
    const box = $('#adminIconBox');
    if (!box) return;
    let dragCounter = 0;
    ['dragenter', 'dragover'].forEach(evt => box.addEventListener(evt, e => {
        e.preventDefault(); e.stopPropagation();
        dragCounter++; box.classList.add('dragover');
    }));
    ['dragleave', 'dragend'].forEach(evt => box.addEventListener(evt, e => {
        e.preventDefault();
        dragCounter = Math.max(0, dragCounter - 1);
        if (dragCounter === 0) box.classList.remove('dragover');
    }));
    box.addEventListener('drop', e => {
        e.preventDefault(); e.stopPropagation();
        dragCounter = 0; box.classList.remove('dragover');
        manejarArchivoIcono(e.dataTransfer.files && e.dataTransfer.files[0]);
    });
})();

/* Modal: ¿usar la foto facial también como ícono? */
function preguntarUsarComoIcono() {
    if (STATE.adminIconBase64) return; // ya tiene un ícono propio, no interrumpir
    $('#modalUsarIcono').classList.remove('hidden');
}

$('#btnSiUsarIcono')?.addEventListener('click', () => {
    if (STATE.adminFotoBase64) {
        STATE.adminIconBase64 = STATE.adminFotoBase64;
        mostrarPreviewIcono(STATE.adminFotoBase64);
    }
    cerrarModal('modalUsarIcono');
});

$('#btnNoUsarIcono')?.addEventListener('click', () => cerrarModal('modalUsarIcono'));

/* Foto facial: archivo, arrastrar y soltar */
$('#agFotoFile')?.addEventListener('change', e => {
    leerYPrevisualizarArchivo(e.target.files[0]);
});

$('#agHorarioFile')?.addEventListener('change', e => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const esPdf = file.type === 'application/pdf' || /\.pdf$/i.test(file.name);
    if (!esPdf) {
        alert('Solo se permiten archivos PDF para el horario.');
        e.target.value = '';
        return;
    }
    if (file.size > 10 * 1024 * 1024) {
        alert('El PDF no debe superar 10 MB.');
        e.target.value = '';
        return;
    }
    const reader = new FileReader();
    reader.onload = ev => {
        STATE.adminHorarioBase64 = ev.target.result;
        STATE.adminHorarioNombre = file.name;
        const label = $('#agHorarioNombre');
        if (label) label.textContent = file.name;
    };
    reader.readAsDataURL(file);
});

function mostrarPreviewFoto(src) {
    const wrap = $('#agPreviewWrap');
    const img = $('#agPreviewImg');
    img.src = src;
    wrap.classList.remove('hidden');
    preguntarUsarComoIcono();
}

function leerYPrevisualizarArchivo(file) {
    if (!file || !file.type.startsWith('image/')) return;
    const reader = new FileReader();
    reader.onload = ev => {
        STATE.adminFotoBase64 = ev.target.result;
        mostrarPreviewFoto(ev.target.result);
    };
    reader.readAsDataURL(file);
}

/* Arrastrar y soltar una foto sobre la caja de cámara */
(function initDragDropFoto() {
    const box = $('#adminCameraBox');
    if (!box) return;
    let dragCounter = 0;
    ['dragenter', 'dragover'].forEach(evt => {
        box.addEventListener(evt, e => {
            e.preventDefault();
            e.stopPropagation();
            dragCounter++;
            box.classList.add('dragover');
        });
    });
    ['dragleave', 'dragend'].forEach(evt => {
        box.addEventListener(evt, e => {
            e.preventDefault();
            dragCounter = Math.max(0, dragCounter - 1);
            if (dragCounter === 0) box.classList.remove('dragover');
        });
    });
    box.addEventListener('drop', e => {
        e.preventDefault();
        e.stopPropagation();
        dragCounter = 0;
        box.classList.remove('dragover');
        const file = e.dataTransfer.files && e.dataTransfer.files[0];
        leerYPrevisualizarArchivo(file);
    });
})();

/* Cámara admin */
window.adminCamEncender = async function () {
    if (STATE.adminCamStream) return;
    try {
        STATE.adminCamStream = await navigator.mediaDevices.getUserMedia({
            video: { width: 640, height: 480, facingMode: 'user' }
        });
        const vid = $('#adminVideo');
        vid.srcObject = STATE.adminCamStream;
        vid.style.display = 'block';
        $('#adminCamPlaceholder').style.display = 'none';
        $('#adminFaceGuide').style.display = 'flex';
        $('#btnAdminCamOn').disabled = true;
        $('#btnAdminCapturar').disabled = false;
        $('#btnAdminCamOff').disabled = false;
    } catch (err) {
        alert('No se pudo acceder a la cámara');
    }
};

window.adminCamCapturar = function () {
    const vid = $('#adminVideo');
    const canv = $('#adminCanvas');
    if (!STATE.adminCamStream || vid.style.display === 'none') return;
    canv.width = vid.videoWidth;
    canv.height = vid.videoHeight;
    const ctx = canv.getContext('2d');
    ctx.drawImage(vid, 0, 0, canv.width, canv.height);
    STATE.adminFotoBase64 = canv.toDataURL('image/jpeg', 0.9);
    mostrarPreviewFoto(STATE.adminFotoBase64);
};

window.adminCamApagar = function () {
    cleanupAdminCam();
};

function cleanupAdminCam() {
    if (STATE.adminCamStream) {
        STATE.adminCamStream.getTracks().forEach(t => t.stop());
        STATE.adminCamStream = null;
    }
    $('#adminVideo').style.display = 'none';
    $('#adminCamPlaceholder').style.display = 'flex';
    $('#adminFaceGuide').style.display = 'none';
    $('#btnAdminCamOn').disabled = false;
    $('#btnAdminCapturar').disabled = true;
    $('#btnAdminCamOff').disabled = true;
}

window.resetFormAgregar = function () {
    $('#formAgregar').reset();
    STATE.adminFotoBase64 = null;
    STATE.adminHorarioBase64 = null;
    STATE.adminHorarioNombre = null;
    $('#agPreviewWrap').classList.add('hidden');
    const hn = $('#agHorarioNombre');
    if (hn) hn.textContent = 'ningún archivo seleccionado';
    aplicarCamposPorTipo('');
    limpiarPreviewIcono();
    cleanupAdminCam();
};

$('#formAgregar')?.addEventListener('submit', async e => {
    e.preventDefault();

    if (STATE.guardandoPersonal) return;

    if (!STATE.adminFotoBase64) {
        alert('La fotografía facial es obligatoria: enciende la cámara y captura, o sube un archivo.');
        return;
    }

    let tipoPersonal = $('#agTipo').value;

    STATE.guardandoPersonal = true;
    const btn = $('#btnGuardarPersonal');
    btn.disabled = true;
    btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16" class="spin"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> analizando rostro, puede tardar unos segundos...`;

    const payload = {
        nombre: $('#agNombre').value.trim(),
        dni: $('#agDni').value.trim(),
        correo: ($('#agCorreo')?.value || '').trim() || null,
        telefono: ($('#agTelefono')?.value || '').trim() || null,
        codigo: tipoPersonal === 'personal' ? null : $('#agCodigo').value.trim(),
        tipo_personal: tipoPersonal,
        area: tipoPersonal === 'instructor' ? $('#agArea').value : null,
        curso: $('#agCurso').value || null,
                cargo: tipoPersonal === 'personal' ? $('#agCargo').value.trim() : null,
        foto: STATE.adminFotoBase64,
        icono: STATE.adminIconBase64 || null,
        /* horario se gestiona en Admin → Horario */
    };

    try {
        const res = await fetch('/api/personal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (res.ok) {
            resetFormAgregar();
            STATE.adminTab = 'personal';
            renderAdminTab('personal');
        } else {
            const err = await res.json();
            alert(err.mensaje || 'Error al guardar');
        }
    } catch (e) {
        alert('Error de conexión');
    } finally {
        STATE.guardandoPersonal = false;
        btn.disabled = false;
        btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg> guardar personal`;
    }
});

/* ---------- REPORTES ---------- */
function initReportes() {
    const hoy = formatearFecha(new Date());
    const mesAtras = formatearFecha(new Date(Date.now() - 30 * 86400000));
    $('#repFechaInicio').value = mesAtras;
    $('#repFechaFin').value = hoy;
    generarReporte();
    cargarDashboardCharts();
}

let _chartCarrera = null, _chartDia = null;
async function cargarDashboardCharts() {
    if (typeof Chart === 'undefined') return;
    try {
        const res = await fetch('/api/dashboard/stats');
        if (!res.ok) return;
        const data = await res.json();
        if (!data.ok) return;
        const carreras = Object.keys(data.por_carrera || {});
        const entradasC = carreras.map(c => data.por_carrera[c].entradas || 0);
        const tardanzasC = carreras.map(c => data.por_carrera[c].tardanzas || 0);
        const ctx1 = document.getElementById('chartCarrera');
        if (ctx1) {
            if (_chartCarrera) _chartCarrera.destroy();
            _chartCarrera = new Chart(ctx1, {
                type: 'bar',
                data: {
                    labels: carreras,
                    datasets: [
                        { label: 'entradas', data: entradasC, backgroundColor: 'rgba(74, 222, 128, 0.6)' },
                        { label: 'tardanzas', data: tardanzasC, backgroundColor: 'rgba(248, 113, 113, 0.6)' }
                    ]
                },
                options: { responsive: true, plugins: { legend: { labels: { color: '#aaa' } } },
                    scales: { x: { ticks: { color: '#888' } }, y: { ticks: { color: '#888' } } } }
            });
        }
        const dias = (data.por_dia || []).map(d => d.dia);
        const entD = (data.por_dia || []).map(d => d.entradas);
        const salD = (data.por_dia || []).map(d => d.salidas);
        const ctx2 = document.getElementById('chartDia');
        if (ctx2) {
            if (_chartDia) _chartDia.destroy();
            _chartDia = new Chart(ctx2, {
                type: 'line',
                data: {
                    labels: dias,
                    datasets: [
                        { label: 'entradas', data: entD, borderColor: '#4ade80', tension: 0.3 },
                        { label: 'salidas', data: salD, borderColor: '#f87171', tension: 0.3 }
                    ]
                },
                options: { responsive: true, plugins: { legend: { labels: { color: '#aaa' } } },
                    scales: { x: { ticks: { color: '#888' } }, y: { ticks: { color: '#888' } } } }
            });
        }
    } catch (e) { console.error('dashboard', e); }
}

$('#btnGenerarReporte')?.addEventListener('click', generarReporte);
$('#btnExportCsv')?.addEventListener('click', exportarReporteCsv);

async function generarReporte() {
    const inicio = $('#repFechaInicio').value;
    const fin = $('#repFechaFin').value;
    const tipo = $('#repTipo').value;
    try {
        const res = await fetch(`/api/registros?rango=custom&inicio=${inicio}&fin=${fin}&tipo=${tipo}`);
        if (!res.ok) throw new Error('HTTP ' + res.status);
        STATE.reportesCache = await res.json();
        renderReportes(STATE.reportesCache);
    } catch (e) {
        STATE.reportesCache = [];
        renderReportes([]);
    }
}

function renderReportes(data) {
    const tbody = $('#tablaReportes tbody');
    const empty = $('#emptyReportes');
    if (!data || data.length === 0) {
        tbody.innerHTML = '';
        empty.classList.remove('hidden');
        $('#repResumen').classList.add('hidden');
        return;
    }
    empty.classList.add('hidden');
    $('#repResumen').classList.remove('hidden');

    let entradas = 0, salidas = 0, tardanzas = 0;
    const frag = document.createDocumentFragment();
    data.forEach(r => {
        if (r.tipo === 'entrada') entradas++;
        if (r.tipo === 'salida') salidas++;
        if (r.tipo === 'entrada' && esTardanza(horaDeFecha(r.fecha_hora))) tardanzas++;

        const fechaSolo = (r.fecha_hora || '').substring(0, 10);
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${fechaSolo}</td>
            <td><strong>${escapeHtml(r.nombre || '')}</strong></td>
            <td><span class="badge tipo-${r.tipo}">${r.tipo}</span></td>
            <td>${horaDeFecha(r.fecha_hora)}</td>
            <td>${((r.confianza || 0) * 100).toFixed(1)}%</td>
        `;
        frag.appendChild(tr);
    });
    tbody.innerHTML = '';
    tbody.appendChild(frag);

    $('#resTotal').textContent = data.length;
    $('#resEntradas').textContent = entradas;
    $('#resSalidas').textContent = salidas;
    $('#resTardanzas').textContent = tardanzas;
}

function exportarReporteCsv() {
    if (!STATE.reportesCache.length) return;
    const headers = ['Fecha', 'Nombre', 'Tipo', 'Hora', 'Confianza', 'Area'];
    const rows = STATE.reportesCache.map(r => [
        (r.fecha_hora || '').substring(0, 10),
        r.nombre,
        r.tipo,
        horaDeFecha(r.fecha_hora),
        ((r.confianza || 0) * 100).toFixed(1) + '%',
        r.area || ''
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.map(x => `"${x}"`).join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `reporte_SENATI_${formatearFecha(new Date())}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

/* ---------- CONFIGURACIÓN ---------- */
async function initConfig() {
    renderAreasConfig();
    $('#cfgUmbral')?.addEventListener('input', e => {
        $('#cfgUmbralVal').textContent = e.target.value + '%';
    });
    // Antes estos campos siempre mostraban los valores fijos escritos en el HTML (08:00,
    // 17:00, 10 min, 85%, 2.5s, 5s) sin importar la configuración real que estuviera activa.
    // Ahora se piden al backend y se muestran los valores que de verdad están en uso.
    try {
        const res = await fetch('/api/admin/configuracion');
        if (!res.ok) return;
        const cfg = await res.json();
        // Campos de horario laboral global eliminados de la UI
        if ($('#cfgEntrada')) $('#cfgEntrada').value = cfg.hora_entrada;
        if ($('#cfgSalida')) $('#cfgSalida').value = cfg.hora_salida;
        if ($('#cfgTolerancia')) $('#cfgTolerancia').value = cfg.tolerancia_min;
        const umbralPct = Math.round((1 - cfg.umbral_confianza) * 100);
        if ($('#cfgUmbral')) $('#cfgUmbral').value = umbralPct;
        if ($('#cfgUmbralVal')) $('#cfgUmbralVal').textContent = umbralPct + '%';
        if ($('#cfgIntervalo')) $('#cfgIntervalo').value = cfg.intervalo_escaneo;
        if ($('#cfgEspera')) $('#cfgEspera').value = cfg.tiempo_reescaneo;
    } catch (e) { /* si falla, quedan los valores por defecto del HTML */ }
}

async function guardarConfiguracion(payload, boton, mensajeExito) {
    const original = boton.textContent;
    boton.disabled = true;
    boton.textContent = 'guardando...';
    try {
        const res = await fetch('/api/admin/configuracion', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.ok) {
            await cargarConfigSistema(); // aplica el cambio de inmediato (tardanzas, intervalo de escaneo)
            alert(mensajeExito);
        } else {
            alert(data.mensaje || 'No se pudo guardar la configuración.');
        }
    } catch (e) {
        alert('Error de conexión al guardar la configuración.');
    } finally {
        boton.disabled = false;
        boton.textContent = original;
    }
}

$('#btnGuardarHorario')?.addEventListener('click', (e) => {
    guardarConfiguracion({
        hora_entrada: $('#cfgEntrada').value,
        hora_salida: $('#cfgSalida').value,
        tolerancia_min: parseInt($('#cfgTolerancia').value)
    }, e.target, 'Horario laboral actualizado.');
});

$('#btnGuardarSistema')?.addEventListener('click', (e) => {
    const umbralPct = parseInt($('#cfgUmbral').value);
    guardarConfiguracion({
        umbral_confianza: Math.round((1 - umbralPct / 100) * 100) / 100,
        intervalo_escaneo: parseFloat($('#cfgIntervalo').value),
        tiempo_reescaneo: parseInt($('#cfgEspera').value)
    }, e.target, 'Configuración del sistema actualizada.');
});

$$('.dia-btn').forEach(btn => {
    btn.addEventListener('click', () => btn.classList.toggle('active'));
});

function renderAreasConfig() {
    const container = $('#areasList');
    container.innerHTML = '';
    STATE.areasSENATI.forEach((area, i) => {
        const div = document.createElement('div');
        div.className = 'area-item';
        div.innerHTML = `<span>${area}</span><button class="btn-icon-sm danger" onclick="eliminarArea(${i})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="12" height="12"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg></button>`;
        container.appendChild(div);
    });
}

window.eliminarArea = function (idx) {
    STATE.areasSENATI.splice(idx, 1);
    renderAreasConfig();
};

$('#btnAddArea')?.addEventListener('click', () => {
    const val = $('#newAreaName').value.trim();
    if (val && !STATE.areasSENATI.includes(val)) {
        STATE.areasSENATI.push(val);
        $('#newAreaName').value = '';
        renderAreasConfig();
    }
});

/* ---------- REPARACIÓN ---------- */
function initReparacion() {
    STATE.repArmado = false;
    STATE.pwArmado = false;
    $('#repSlider').value = 0;
    actualizarSliderFormateo(0);
    $('#repPassword').value = '';
    $('#btnFormatearBD').disabled = true;
    $('#repEstadoTxt').textContent = '';
    $('#repEstadoTxt').className = 'repair-status';

    $('#pwSlider').value = 0;
    actualizarSliderPassword(0);
    $('#pwActual').value = '';
    $('#pwNueva').value = '';
    $('#pwConfirmar').value = '';
    $('#btnCambiarPassword').disabled = true;
    $('#pwEstadoTxt').textContent = '';
    $('#pwEstadoTxt').className = 'repair-status';

    diagnosticar();
}

$('#btnRevisarEstado')?.addEventListener('click', diagnosticar);

function manejarNoAutorizado(estadoEl) {
    if (estadoEl) {
        estadoEl.textContent = 'sesión no autorizada. vuelve a iniciar sesión en modo admin.';
        estadoEl.className = 'repair-status error';
    }
    sessionStorage.removeItem('senati_admin_auth');
    STATE.adminAutenticado = false;
}

async function diagnosticar() {
    $$('.diag-card').forEach(c => {
        c.classList.remove('ok', 'error');
        c.classList.add('checking');
        c.querySelector('p').textContent = 'verificando...';
    });
    try {
        const res = await fetch('/api/admin/diagnostico');
        if (res.status === 401) {
            manejarNoAutorizado(null);
            $$('.diag-card').forEach(c => {
                c.classList.remove('checking');
                c.classList.add('error');
                c.querySelector('p').textContent = 'sesión no autorizada';
            });
            mostrarLoginAdmin();
            return;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        pintarDiag('camara', data.camara, data.camara ? 'operativa' : 'no detectada');
        pintarDiag('base_datos', data.base_datos, data.base_datos ? 'conectada' : 'error de conexión');
        pintarDiag('modelo', data.modelo, data.modelo ? 'cargado' : 'no instalado');
        const backupTxt = data.ultimo_backup ? data.ultimo_backup : 'sin registrar aún';
        $('#diagBackupTxt').textContent = backupTxt;
        const backupCard = document.querySelector('.diag-card[data-diag="backup"]');
        backupCard.classList.remove('checking');
        backupCard.classList.add(data.ultimo_backup ? 'ok' : 'error');
    } catch (e) {
        $$('.diag-card').forEach(c => {
            c.classList.remove('checking', 'ok');
            c.classList.add('error');
            c.querySelector('p').textContent = 'no se pudo verificar';
        });
    }
}

function pintarDiag(key, ok, texto) {
    const card = document.querySelector(`.diag-card[data-diag="${key}"]`);
    if (!card) return;
    card.classList.remove('checking');
    card.classList.toggle('ok', !!ok);
    card.classList.toggle('error', !ok);
    card.querySelector('p').textContent = texto;
}

/* Copia de seguridad */
$('#btnCrearBackup')?.addEventListener('click', async () => {
    const btn = $('#btnCrearBackup');
    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = 'creando copia...';
    try {
        const res = await fetch('/api/admin/backup', { method: 'POST' });
        if (res.status === 401) { manejarNoAutorizado(null); mostrarLoginAdmin(); return; }
        const data = await res.json();
        if (res.ok && data.ok) {
            diagnosticar();
        } else {
            alert(data.mensaje || 'No se pudo crear la copia de seguridad.');
        }
    } catch (e) {
        alert('Error de conexión al crear la copia de seguridad.');
    } finally {
        btn.disabled = false;
        btn.innerHTML = original;
    }
});

/* Slider "desliza para confirmar" — formatear base de datos */
function actualizarSliderFormateo(val) {
    $('#repSliderFill').style.width = val + '%';
    const wrap = $('#repSliderWrap');
    const txt = $('#repSliderTxt');
    STATE.repArmado = val >= 100;
    wrap.classList.toggle('armed', STATE.repArmado);
    txt.textContent = STATE.repArmado ? '✓ confirmado — listo para formatear' : 'desliza para confirmar →';
    evaluarBotonFormatear();
}

$('#repSlider')?.addEventListener('input', e => actualizarSliderFormateo(parseInt(e.target.value)));
$('#repPassword')?.addEventListener('input', evaluarBotonFormatear);

function evaluarBotonFormatear() {
    const pass = $('#repPassword')?.value || '';
    $('#btnFormatearBD').disabled = !(STATE.repArmado && pass.length > 0);
}

$('#btnFormatearBD')?.addEventListener('click', async () => {
    const password = $('#repPassword').value;
    if (!STATE.repArmado || !password) return;
    const ok = confirm('¿Formatear por completo la base de datos? Se eliminará TODO el personal, fotos y registros de asistencia. Esta acción es irreversible.');
    if (!ok) return;

    const btn = $('#btnFormatearBD');
    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = 'formateando...';
    const estado = $('#repEstadoTxt');

    try {
        const res = await fetch('/api/admin/reset-db', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });
        if (res.status === 401) { manejarNoAutorizado(estado); mostrarLoginAdmin(); return; }
        const data = await res.json();
        if (res.ok && data.ok) {
            estado.textContent = data.mensaje || 'base de datos formateada correctamente.';
            estado.className = 'repair-status success';
            STATE.personalCache = [];
            initReparacion();
        } else {
            estado.textContent = data.mensaje || 'contraseña incorrecta o error al formatear.';
            estado.className = 'repair-status error';
            btn.disabled = false;
        }
    } catch (e) {
        estado.textContent = 'error de conexión con el servidor.';
        estado.className = 'repair-status error';
        btn.disabled = false;
    } finally {
        btn.innerHTML = original;
    }
});

/* Slider "desliza para confirmar" — cambiar contraseña */
function actualizarSliderPassword(val) {
    $('#pwSliderFill').style.width = val + '%';
    const wrap = $('#pwSliderWrap');
    const txt = $('#pwSliderTxt');
    STATE.pwArmado = val >= 100;
    wrap.classList.toggle('armed', STATE.pwArmado);
    txt.textContent = STATE.pwArmado ? '✓ confirmado — listo para actualizar' : 'desliza para confirmar →';
    evaluarBotonPassword();
}

$('#pwSlider')?.addEventListener('input', e => actualizarSliderPassword(parseInt(e.target.value)));
['#pwActual', '#pwNueva', '#pwConfirmar'].forEach(sel => $(sel)?.addEventListener('input', evaluarBotonPassword));

function evaluarBotonPassword() {
    const actual = $('#pwActual')?.value || '';
    const nueva = $('#pwNueva')?.value || '';
    const confirmar = $('#pwConfirmar')?.value || '';
    const listo = STATE.pwArmado && actual.length > 0 && nueva.length >= 6 && nueva === confirmar;
    $('#btnCambiarPassword').disabled = !listo;
}

$('#btnCambiarPassword')?.addEventListener('click', async () => {
    const actual = $('#pwActual').value;
    const nueva = $('#pwNueva').value;
    const confirmar = $('#pwConfirmar').value;
    const estado = $('#pwEstadoTxt');

    if (nueva.length < 6) {
        estado.textContent = 'la nueva contraseña debe tener al menos 6 caracteres.';
        estado.className = 'repair-status error';
        return;
    }
    if (nueva !== confirmar) {
        estado.textContent = 'la confirmación no coincide con la nueva contraseña.';
        estado.className = 'repair-status error';
        return;
    }

    const btn = $('#btnCambiarPassword');
    btn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = 'actualizando...';

    try {
        const res = await fetch('/api/admin/change-password', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password_actual: actual, password_nueva: nueva })
        });
        if (res.status === 401) { manejarNoAutorizado(estado); mostrarLoginAdmin(); return; }
        const data = await res.json();
        if (res.ok && data.ok) {
            estado.textContent = 'contraseña actualizada correctamente.';
            estado.className = 'repair-status success';
            initReparacion();
        } else {
            estado.textContent = data.mensaje || 'no se pudo actualizar la contraseña.';
            estado.className = 'repair-status error';
            btn.disabled = false;
        }
    } catch (e) {
        estado.textContent = 'error de conexión con el servidor.';
        estado.className = 'repair-status error';
        btn.disabled = false;
    } finally {
        btn.innerHTML = original;
    }
});

/* ═══════════════════════════════════════════════════════
   INICIALIZACIÓN
   ═══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', async () => {
    await cargarConfigSistema();
    initRouter();
});

window.encenderCamara = encenderCamara;
window.apagarCamara = apagarCamara;