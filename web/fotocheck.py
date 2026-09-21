"""
Genera el carnet horizontal (foto, insignia de rol, nombre, carrera/semestre,
ID/DNI y un QR grande con estética institucional) para el check-in rápido sin
cámara -- mismo estilo que el Fotocheck de SENATI.

El QR codifica un token opaco y aleatorio (no el DNI ni el ID), guardado en
personal.qr_token -- así aunque alguien vea o fotografíe el carnet ajeno no
puede fabricar uno propio ni adivinar el de otra persona.

El QR usa corrección de errores alta (nivel H, ~30% de tolerancia) porque el
logo de SENATI se dibuja encima, en el centro -- el propio estándar QR está
pensado para tolerar justamente esto sin perder legibilidad, así que la
cámara lo sigue leyendo sin problema.

Requiere: pip install qrcode[pil]   (Pillow ya es dependencia del proyecto)

Migración pendiente en Supabase (correr una vez en el SQL Editor):

    ALTER TABLE personal ADD COLUMN IF NOT EXISTS qr_token text UNIQUE;

Uso desde app.py: ver la ruta /api/personal/<id>/fotocheck.
"""
import io
import os
import secrets
import urllib.request

import qrcode
from PIL import Image, ImageDraw, ImageFont

ANCHO, ALTO = 1200, 620
COLOR_FONDO = (26, 43, 92)        # azul oscuro (mismo tono que el logo, a propósito)
COLOR_HEADER = (18, 30, 68)
COLOR_TEXTO = (255, 255, 255)
COLOR_TEXTO_SEC = (200, 210, 230)
COLOR_PILL_TEXTO = (26, 43, 92)
COLOR_QR_MODULO = (21, 37, 82)    # mismo azul institucional, para el QR

# El logo vive en web/static/img/senati_logo.png -- si no está, simplemente no
# se dibuja la marca de agua ni el centro del QR (el carnet se genera igual,
# sin romperse).
_LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "senati_logo.png")


def asegurar_qr_token(supabase, personal_id):
    """Devuelve el qr_token de la persona, generándolo y guardándolo si
    todavía no tiene uno."""
    res = supabase.table("personal").select("qr_token").eq("id", personal_id).limit(1).execute()
    if not res.data:
        raise ValueError("Personal no encontrado.")
    token = res.data[0].get("qr_token")
    if token:
        return token
    token = secrets.token_urlsafe(24)
    supabase.table("personal").update({"qr_token": token}).eq("id", personal_id).execute()
    return token


def _cargar_fuente(tamano, negrita=False):
    candidatos = ["DejaVuSans-Bold.ttf", "arialbd.ttf"] if negrita else ["DejaVuSans.ttf", "arial.ttf"]
    for nombre in candidatos:
        try:
            return ImageFont.truetype(nombre, tamano)
        except Exception:
            continue
    return ImageFont.load_default()


def _descargar_foto(url):
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            return Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception as e:
        print(f"[fotocheck] no se pudo descargar la foto ({url}): {e}")
        return None


def _recorte_cover(img, ancho, alto):
    """Recorta y escala una foto para llenar exactamente ancho x alto
    (estilo 'cover' de CSS), centrada."""
    ratio_destino = ancho / alto
    w, h = img.size
    ratio_actual = w / h
    if ratio_actual > ratio_destino:
        nuevo_w = int(h * ratio_destino)
        offset = (w - nuevo_w) // 2
        img = img.crop((offset, 0, offset + nuevo_w, h))
    else:
        nuevo_h = int(w / ratio_destino)
        offset = (h - nuevo_h) // 2
        img = img.crop((0, offset, w, offset + nuevo_h))
    return img.resize((ancho, alto))


def _dibujar_marca_de_agua(base_rgba):
    """Pega el logo de SENATI a muy baja opacidad, centrado en la mitad
    izquierda del carnet -- como el logo y el fondo del carnet son azules
    parecidos, el cuadrado del logo casi desaparece y solo queda una marca de
    agua tenue del símbolo, sin estorbar el texto ni el QR."""
    if not os.path.exists(_LOGO_PATH):
        return
    try:
        logo = Image.open(_LOGO_PATH).convert("RGBA")
    except Exception as e:
        print(f"[fotocheck] no se pudo cargar el logo para la marca de agua: {e}")
        return
    lado = 480
    logo = logo.resize((lado, lado))
    alpha = logo.split()[3].point(lambda a: int(a * 0.10))
    logo.putalpha(alpha)
    pos = (int(ANCHO * 0.25 - lado / 2), int(ALTO / 2 - lado / 2))
    base_rgba.alpha_composite(logo, pos)


def _envolver(draw, texto, fuente, ancho_max):
    palabras, lineas, actual = (texto or "").split(), [], ""
    for palabra in palabras:
        candidata = (actual + " " + palabra).strip()
        if not actual or draw.textlength(candidata, font=fuente) <= ancho_max:
            actual = candidata
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def _ajustar_a_lineas(draw, texto, ancho_max, max_lineas, tam_inicial, tam_minimo, negrita=True, paso=2):
    """Prueba tamaños de fuente decrecientes hasta que el texto entre en
    max_lineas dentro de ancho_max. Devuelve (fuente, lineas) -- así el
    layout nunca desborda la tarjeta ni queda descentrado por un nombre o
    carrera más largos de lo normal."""
    tam = tam_inicial
    while tam >= tam_minimo:
        fuente = _cargar_fuente(tam, negrita=negrita)
        lineas = _envolver(draw, texto, fuente, ancho_max)
        if len(lineas) <= max_lineas:
            return fuente, lineas
        tam -= paso
    fuente = _cargar_fuente(tam_minimo, negrita=negrita)
    return fuente, _envolver(draw, texto, fuente, ancho_max)[:max_lineas]


def _generar_qr_estilizado(token, lado):
    """QR con estética institucional: módulos redondeados en azul SENATI (en
    vez de los cuadrados negros por defecto) y el logo al centro sobre una
    placa blanca. Corrección de errores alta (H) porque el logo tapa parte
    del centro -- el estándar QR está pensado para tolerar justo esto."""
    qr = qrcode.QRCode(border=2, box_size=10, error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(token)
    qr.make(fit=True)
    matriz = qr.get_matrix()
    n = len(matriz)

    celda = 10
    lienzo = Image.new("RGB", (n * celda, n * celda), (255, 255, 255))
    draw = ImageDraw.Draw(lienzo)
    # Estos valores no son estéticos al azar: los probé decodificando con
    # OpenCV, incluso simulando desenfoque/compresión de cámara real, antes
    # de elegirlos. Con más margen/radio que esto, el QR se ve más "punteado"
    # y más bonito, pero deja de leerse -- no vale la pena el riesgo en un
    # sistema que depende de esto para marcar asistencia.
    radio = celda * 0.15
    margen = celda * 0.03
    for fila in range(n):
        for col in range(n):
            if not matriz[fila][col]:
                continue
            x0, y0 = col * celda + margen, fila * celda + margen
            x1, y1 = x0 + celda - margen * 2, y0 + celda - margen * 2
            draw.rounded_rectangle([x0, y0, x1, y1], radius=radio, fill=COLOR_QR_MODULO)

    lienzo = lienzo.resize((lado, lado), Image.LANCZOS)

    if os.path.exists(_LOGO_PATH):
        try:
            logo = Image.open(_LOGO_PATH).convert("RGB")
            lado_logo = int(lado * 0.22)
            logo = logo.resize((lado_logo, lado_logo))
            marco = lado_logo + 16
            placa = Image.new("RGB", (marco, marco), (255, 255, 255))
            placa.paste(logo, ((marco - lado_logo) // 2, (marco - lado_logo) // 2))
            lienzo.paste(placa, ((lado - marco) // 2, (lado - marco) // 2))
        except Exception as e:
            print(f"[fotocheck] no se pudo insertar el logo en el QR: {e}")

    return lienzo


def generar_imagen_fotocheck(persona, token):
    """persona: dict con nombre, dni, tipo_personal, curso, semestre,
    area/cargo, codigo/id, foto_path/icono_path.
    token: qr_token ya asegurado con asegurar_qr_token(). El QR codifica
    directamente el token -- lo lee qr_checkin.js con la cámara y lo manda a
    /api/qr/checkin, sin necesidad de un dominio público."""
    img = Image.new("RGBA", (ANCHO, ALTO), COLOR_FONDO + (255,))
    _dibujar_marca_de_agua(img)
    draw = ImageDraw.Draw(img)

    # ── Header ──────────────────────────────────────────────
    ALTO_HEADER = 74
    draw.rectangle([0, 0, ANCHO, ALTO_HEADER], fill=COLOR_HEADER)
    f_header = _cargar_fuente(30, negrita=True)
    draw.text((ANCHO / 2, ALTO_HEADER / 2), "FOTOCHECK", font=f_header, fill=COLOR_TEXTO, anchor="mm")

    # ── Geometría del QR (se define antes que el texto para saber cuánto
    #    espacio real le queda disponible a la columna de datos) ──
    lado_qr = 340
    pad_qr = 24
    centro_x = ANCHO * 3 // 4
    centro_y = ALTO_HEADER + (ALTO - ALTO_HEADER) // 2
    borde_izq_qr = centro_x - lado_qr // 2 - pad_qr

    # ── Mitad izquierda: badge, foto y datos ───────────────────
    MARGEN = 44
    x = MARGEN
    y = ALTO_HEADER + 30

    tipo = (persona.get("tipo_personal") or "").strip().lower()
    cargo = (persona.get("cargo") or "").strip()
    if tipo == "trabajador" and cargo:
        texto_pill_base = cargo  # p.ej. "Limpieza", "Seguridad"
    elif tipo:
        texto_pill_base = tipo
    else:
        texto_pill_base = ""
    pill_h = 0
    if texto_pill_base:
        f_pill = _cargar_fuente(20, negrita=True)
        texto_pill = texto_pill_base[:1].upper() + texto_pill_base[1:]
        pad_x, pad_y = 22, 12
        bbox = draw.textbbox((0, 0), texto_pill, font=f_pill)
        pill_w = (bbox[2] - bbox[0]) + pad_x * 2
        pill_h = (bbox[3] - bbox[1]) + pad_y * 2
        draw.rounded_rectangle([x, y, x + pill_w, y + pill_h], radius=pill_h / 2, fill=COLOR_TEXTO)
        draw.text((x + pill_w / 2, y + pill_h / 2 - bbox[1] / 2), texto_pill, font=f_pill,
                   fill=COLOR_PILL_TEXTO, anchor="mm")
        y += pill_h + 22

    foto_y = y
    foto_w, foto_h = 220, ALTO - foto_y - 30
    foto = _descargar_foto(persona.get("foto_path") or persona.get("icono_path"))
    if foto:
        foto = _recorte_cover(foto, foto_w, foto_h)
        img.paste(foto, (x, foto_y))
    else:
        draw.rectangle([x, foto_y, x + foto_w, foto_y + foto_h], fill=(50, 65, 110))
    draw.rectangle([x, foto_y, x + foto_w, foto_y + foto_h], outline=(255, 255, 255, 70), width=2)

    # ── Columna de datos, a la derecha de la foto, ajustada al ancho real
    #    disponible (hasta justo antes de la caja del QR) ──
    tx = x + foto_w + 40
    ancho_texto = borde_izq_qr - 24 - tx

    nombre = (persona.get("nombre") or "").upper()
    f_nombre, lineas_nombre = _ajustar_a_lineas(draw, nombre, ancho_texto, max_lineas=2,
                                                 tam_inicial=34, tam_minimo=22)
    alto_linea_nombre = int(f_nombre.size * 1.22)

    carrera = persona.get("curso") or persona.get("area") or ""
    f_carrera, lineas_carrera = _ajustar_a_lineas(draw, carrera, ancho_texto, max_lineas=2,
                                                   tam_inicial=21, tam_minimo=16)
    alto_linea_carrera = int(f_carrera.size * 1.3)

    semestre = persona.get("semestre") or ""
    campos = [(et, va) for et, va in
              (("ID", persona.get("codigo") or persona.get("id")), ("DNI", persona.get("dni"))) if va]
    f_label = _cargar_fuente(17)
    f_dato = _cargar_fuente(23, negrita=True)

    # Alto total del bloque de texto, para poder centrarlo verticalmente
    # junto a la foto sin importar si el nombre/carrera son cortos o largos.
    GAP_SECCION = 18
    alto_total = len(lineas_nombre) * alto_linea_nombre
    if lineas_carrera or semestre:
        alto_total += GAP_SECCION
        alto_total += len(lineas_carrera) * alto_linea_carrera
        if semestre:
            alto_total += alto_linea_carrera
    if campos:
        alto_total += GAP_SECCION
        alto_total += len(campos) * (22 + 34)

    ty = foto_y + max(0, (foto_h - alto_total) // 2)

    for linea in lineas_nombre:
        draw.text((tx, ty), linea, font=f_nombre, fill=COLOR_TEXTO)
        ty += alto_linea_nombre

    if lineas_carrera or semestre:
        ty += GAP_SECCION
        for linea in lineas_carrera:
            draw.text((tx, ty), linea, font=f_carrera, fill=COLOR_TEXTO_SEC)
            ty += alto_linea_carrera
        if semestre:
            draw.text((tx, ty), f"{semestre}° semestre", font=f_carrera, fill=COLOR_TEXTO_SEC)
            ty += alto_linea_carrera

    if campos:
        ty += GAP_SECCION
        for etiqueta, valor in campos:
            draw.text((tx, ty), etiqueta, font=f_label, fill=COLOR_TEXTO_SEC)
            ty += 22
            draw.text((tx, ty), str(valor), font=f_dato, fill=COLOR_TEXTO)
            ty += 34

    # ── Mitad derecha: QR institucional, con caja blanca para contraste ──
    img_qr = _generar_qr_estilizado(token, lado_qr)
    caja = [borde_izq_qr, centro_y - lado_qr // 2 - pad_qr,
            centro_x + lado_qr // 2 + pad_qr, centro_y + lado_qr // 2 + pad_qr]
    draw.rounded_rectangle(caja, radius=18, fill=(255, 255, 255))
    img.paste(img_qr, (centro_x - lado_qr // 2, centro_y - lado_qr // 2))

    # Línea divisoria sutil, justo antes de la caja del QR
    draw.line([(borde_izq_qr - 12, ALTO_HEADER + 20), (borde_izq_qr - 12, ALTO - 20)], fill=(60, 78, 130), width=2)

    salida = io.BytesIO()
    img.convert("RGB").save(salida, format="PNG")
    salida.seek(0)
    return salida