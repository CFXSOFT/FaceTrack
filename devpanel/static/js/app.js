let TABLA_ACTUAL = null;
let COLS_ACTUAL = null;
let PK_ACTUAL = null;
let PAGINA_ACTUAL = 1;
let MODO_MODAL = 'editar'; // 'editar' | 'crear'
let PK_EDITANDO = null;

/* ── Login ─────────────────────────────────────────────── */
async function login() {
    const password = document.getElementById('loginPassword').value;
    const res = await fetch('/api/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
    });
    const data = await res.json();
    if (data.ok) {
        document.getElementById('loginBox').classList.add('hidden');
        document.getElementById('app').classList.remove('hidden');
        irA('dashboard');
    } else {
        document.getElementById('loginError').classList.remove('hidden');
    }
}

async function logout() {
    await fetch('/api/logout', { method: 'POST' });
    location.reload();
}

/* ── Navegación ────────────────────────────────────────── */
function irA(vista) {
    document.querySelectorAll('.vista').forEach(v => v.classList.add('hidden'));
    document.getElementById('vista-' + vista).classList.remove('hidden');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`.nav-item[data-vista="${vista}"]`).classList.add('active');
    if (vista === 'dashboard') cargarDashboard();
    if (vista === 'pendientes') cargarPendientes();
    if (vista === 'tablas') cargarListaTablas();
}

/* ── Dashboard ─────────────────────────────────────────── */
async function cargarDashboard() {
    const res = await fetch('/api/dashboard');
    if (!res.ok) return;
    const d = await res.json();
    document.getElementById('kpiActivos').textContent = d.personal_activo;
    document.getElementById('kpiHoy').textContent = d.entradas_hoy;

    const tCarrera = document.getElementById('tablaCarrera');
    if (!d.por_carrera.length) {
        tCarrera.innerHTML = '<p class="muted">Sin datos todavía.</p>';
    } else {
        tCarrera.innerHTML = `<table><tr><th>Carrera</th><th>Entradas</th><th>Tardanzas</th><th>% puntualidad</th></tr>
      ${d.por_carrera.map(r => {
            const pct = r.entradas ? Math.round(100 * (r.entradas - r.tardanzas) / r.entradas) : 0;
            return `<tr><td>${r.carrera || '—'}</td><td>${r.entradas}</td>
        <td class="${r.tardanzas > 0 ? 'badge-tardanza' : ''}">${r.tardanzas}</td><td>${pct}%</td></tr>`;
        }).join('')}</table>`;
    }

    const tMes = document.getElementById('tablaMes');
    if (!d.por_mes.length) {
        tMes.innerHTML = '<p class="muted">Sin datos todavía.</p>';
    } else {
        tMes.innerHTML = `<table><tr><th>Mes</th><th>Entradas</th><th>Tardanzas</th></tr>
      ${d.por_mes.map(r => `<tr><td>${r.mes}</td><td>${r.entradas}</td>
        <td class="${r.tardanzas > 0 ? 'badge-tardanza' : ''}">${r.tardanzas}</td></tr>`).join('')}</table>`;
    }
}

/* ── Pendientes ────────────────────────────────────────── */
async function cargarPendientes() {
    const res = await fetch('/api/pendientes');
    const data = await res.json();
    const cont = document.getElementById('listaPendientes');
    if (!data.length) {
        cont.innerHTML = '<p class="muted">No hay pendientes. Buen trabajo 🎉</p>';
        return;
    }
    cont.innerHTML = data.map(p => `
    <div class="pendiente-item ${p.hecho ? 'hecho' : ''}">
      <input type="checkbox" ${p.hecho ? 'checked' : ''} onchange="togglePendiente('${p.id}', this.checked)">
      <span>${escapeHtml(p.texto)}</span>
      <button class="btn-icon" onclick="eliminarPendiente('${p.id}')">🗑️</button>
    </div>`).join('');
}

async function crearPendiente() {
    const input = document.getElementById('nuevoPendiente');
    const texto = input.value.trim();
    if (!texto) return;
    await fetch('/api/pendientes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texto })
    });
    input.value = '';
    cargarPendientes();
}

async function togglePendiente(id, hecho) {
    await fetch(`/api/pendientes/${id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hecho })
    });
    cargarPendientes();
}

async function eliminarPendiente(id) {
    await fetch(`/api/pendientes/${id}`, { method: 'DELETE' });
    cargarPendientes();
}

/* ── Tablas genéricas ──────────────────────────────────── */
async function cargarListaTablas() {
    const res = await fetch('/api/tablas');
    const data = await res.json();
    document.getElementById('listaTablas').innerHTML = data.map(t => `
    <button class="tabla-btn ${t.tabla === TABLA_ACTUAL ? 'active' : ''}" onclick="abrirTabla('${t.tabla}')">
      <span>${t.tabla}</span><span class="muted">${t.filas}</span>
    </button>`).join('');
}

async function abrirTabla(tabla) {
    TABLA_ACTUAL = tabla;
    PAGINA_ACTUAL = 1;
    document.getElementById('tablaActual').textContent = tabla;
    document.getElementById('buscarFila').classList.remove('hidden');
    document.getElementById('buscarFila').value = '';
    document.getElementById('btnNuevaFila').classList.remove('hidden');
    document.querySelectorAll('.tabla-btn').forEach(b => b.classList.remove('active'));
    cargarListaTablas();

    const resCols = await fetch(`/api/tablas/${tabla}/columnas`);
    const dataCols = await resCols.json();
    COLS_ACTUAL = dataCols.columnas;
    PK_ACTUAL = dataCols.clave_primaria;

    cargarFilas(1);
}

async function cargarFilas(pagina) {
    if (!TABLA_ACTUAL) return;
    PAGINA_ACTUAL = pagina;
    const q = document.getElementById('buscarFila').value.trim();
    const res = await fetch(`/api/tablas/${TABLA_ACTUAL}?pagina=${pagina}&q=${encodeURIComponent(q)}`);
    const data = await res.json();
    const cont = document.getElementById('contenedorFilas');

    if (!data.filas.length) {
        cont.innerHTML = '<p class="muted" style="padding:20px">Sin filas.</p>';
        document.getElementById('paginacion').classList.add('hidden');
        return;
    }

    cont.innerHTML = `<table>
    <tr>${data.columnas.map(c => `<th>${c}</th>`).join('')}<th></th></tr>
    ${data.filas.map(f => `<tr>
      ${data.columnas.map(c => `<td title="${escapeHtml(String(f[c] ?? ''))}">${escapeHtml(String(f[c] ?? ''))}</td>`).join('')}
      <td>
        <button class="btn-icon" onclick='abrirModalEditar(${JSON.stringify(f)})'>✏️</button>
        <button class="btn-icon" onclick="eliminarFila('${f[data.clave_primaria]}')">🗑️</button>
      </td>
    </tr>`).join('')}
  </table>`;

    const totalPaginas = Math.max(1, Math.ceil(data.total / data.por_pagina));
    const pagDiv = document.getElementById('paginacion');
    if (totalPaginas > 1) {
        pagDiv.classList.remove('hidden');
        pagDiv.innerHTML = Array.from({ length: totalPaginas }, (_, i) => i + 1).map(p =>
            `<button class="btn ${p === pagina ? 'btn-primary' : 'btn-ghost'}" onclick="cargarFilas(${p})">${p}</button>`
        ).join('');
    } else {
        pagDiv.classList.add('hidden');
    }
}

async function eliminarFila(valorPk) {
    if (!confirm('¿Eliminar esta fila? No se puede deshacer.')) return;
    const res = await fetch(`/api/tablas/${TABLA_ACTUAL}/${valorPk}`, { method: 'DELETE' });
    const data = await res.json();
    if (!data.ok) { alert(data.mensaje || 'No se pudo eliminar.'); return; }
    cargarFilas(PAGINA_ACTUAL);
}

function abrirModalEditar(fila) {
    MODO_MODAL = 'editar';
    PK_EDITANDO = fila[PK_ACTUAL];
    document.getElementById('modalFilaTitulo').textContent = `Editar — ${TABLA_ACTUAL}`;
    renderCamposModal(fila);
    document.getElementById('modalFila').classList.remove('hidden');
}

function abrirModalNuevaFila() {
    MODO_MODAL = 'crear';
    PK_EDITANDO = null;
    document.getElementById('modalFilaTitulo').textContent = `Nueva fila — ${TABLA_ACTUAL}`;
    renderCamposModal({});
    document.getElementById('modalFila').classList.remove('hidden');
}

function renderCamposModal(fila) {
    const cont = document.getElementById('modalFilaCampos');
    cont.innerHTML = COLS_ACTUAL.map(c => {
        const protegida = c.protegida;
        const esClave = c.es_clave;
        const valor = fila[c.column_name] ?? '';
        const disabled = (protegida || (esClave && MODO_MODAL === 'editar')) ? 'disabled' : '';
        return `<div class="campo ${protegida ? 'protegido' : ''}">
      <label>${c.column_name} <span class="muted">(${c.data_type})</span></label>
      <input type="text" data-campo="${c.column_name}" value="${escapeHtml(String(protegida && valor ? '••• (protegido)' : valor))}" ${disabled}>
    </div>`;
    }).join('');
}

async function guardarFila() {
    const inputs = document.querySelectorAll('#modalFilaCampos input:not([disabled])');
    const cambios = {};
    inputs.forEach(inp => { cambios[inp.dataset.campo] = inp.value; });

    let res;
    if (MODO_MODAL === 'editar') {
        res = await fetch(`/api/tablas/${TABLA_ACTUAL}/${PK_EDITANDO}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cambios)
        });
    } else {
        res = await fetch(`/api/tablas/${TABLA_ACTUAL}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cambios)
        });
    }
    const data = await res.json();
    if (!data.ok) { alert(data.mensaje || 'No se pudo guardar.'); return; }
    cerrarModal('modalFila');
    cargarFilas(PAGINA_ACTUAL);
}

function cerrarModal(id) {
    document.getElementById(id).classList.add('hidden');
}

function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
