"""Lectura de datos de facturas y comprobantes (transferencias, recibos, tickets).

Estrategias, en orden:
1. Si hay ANTHROPIC_API_KEY, se envía el PDF o la foto a Claude, que devuelve
   los datos estructurados.
2. Sin clave, todo se resuelve en la computadora:
   - PDF con texto: se extrae el texto respetando columnas.
   - Fotos, capturas y PDFs escaneados: se leen con OCR local (RapidOCR),
     si está instalado.
   Sobre ese texto se buscan los campos por etiqueta ("Importe:", "Fecha",
   "CUIT", "Titularidad"...) y, como respaldo, con reglas generales.

En todos los casos se identifica quién paga (emisor/ordenante) y quién cobra
(receptor/beneficiario). Con los CUIT propios configurados se decide si el
movimiento es ingreso o egreso y quién es la contraparte.
"""
import base64
import io
import json
import logging
import os
import re
import threading
import unicodedata
from datetime import date

log = logging.getLogger(__name__)

CLAUDE_MODEL = os.environ.get("CSA_CLAUDE_MODEL", "claude-opus-5-5")
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

CAMPOS_SCHEMA = {
    "type": "object",
    "properties": {
        "tipo_documento": {"type": "string", "enum": ["factura", "transferencia", "recibo", "ticket", "otro"]},
        "monto": {"type": ["number", "null"], "description": "Importe total final (o importe transferido)"},
        "moneda": {"type": ["string", "null"], "description": "Código ISO: ARS, USD, EUR..."},
        "fecha": {"type": ["string", "null"], "description": "Fecha de emisión u operación, YYYY-MM-DD"},
        "numero": {"type": ["string", "null"], "description": "N.º de factura (tipo y número) o n.º de referencia/operación"},
        "emisor_nombre": {"type": ["string", "null"], "description": "Quien emite la factura, o el ordenante que envía la transferencia"},
        "emisor_cuit": {"type": ["string", "null"]},
        "receptor_nombre": {"type": ["string", "null"], "description": "Cliente de la factura, o beneficiario de la transferencia"},
        "receptor_cuit": {"type": ["string", "null"]},
        "descripcion": {"type": ["string", "null"], "description": "Resumen breve: conceptos facturados o motivo/concepto de la transferencia"},
    },
    "required": ["tipo_documento", "monto", "moneda", "fecha", "numero", "emisor_nombre", "emisor_cuit",
                 "receptor_nombre", "receptor_cuit", "descripcion"],
    "additionalProperties": False,
}

PROMPT = (
    "Sos un asistente contable argentino. Extraé los datos de este comprobante (factura, transferencia "
    "bancaria, recibo o ticket). Si un dato no aparece, devolvé null. El monto es el TOTAL final como número "
    "(punto decimal, sin separadores de miles). En una transferencia, el emisor es el ordenante y el receptor "
    "el beneficiario. Respetá los espacios de los nombres. La descripción debe ser corta (máx. 200 caracteres)."
)


# ================================================================ Entrada principal

def extraer(contenido: bytes, content_type: str, cuits_propios: list[str] | None = None) -> dict:
    """Devuelve los campos del movimiento listos para guardar, más 'metodo' y 'documento'."""
    crudo, metodo, aviso = leer_crudo(contenido, content_type)
    if crudo is None:
        return {"metodo": "ninguno", "aviso": aviso}
    return {**resolver(crudo, cuits_propios or []), "metodo": metodo}


def leer_crudo(contenido: bytes, content_type: str):
    """Lee el comprobante sin interpretar a quién pertenece. Devuelve (datos, metodo, aviso)."""
    if os.environ.get("ANTHROPIC_API_KEY") and (content_type == "application/pdf" or content_type in IMAGE_TYPES):
        try:
            return _extraer_con_claude(contenido, content_type), "ia", None
        except Exception as exc:  # si falla la IA, se intenta en modo local
            log.warning("Falló la extracción con Claude: %s", exc)

    filas, metodo, aviso = _filas_de_archivo(contenido, content_type)
    if not filas:
        return None, "ninguno", aviso
    return analizar_filas(filas), metodo, None


# ================================================================ Claude

def _extraer_con_claude(contenido: bytes, content_type: str) -> dict:
    import anthropic

    data = base64.standard_b64encode(contenido).decode("utf-8")
    if content_type == "application/pdf":
        bloque = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
    else:
        bloque = {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": data}}

    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CAMPOS_SCHEMA}},
        messages=[{"role": "user", "content": [bloque, {"type": "text", "text": PROMPT}]}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("El modelo rechazó la solicitud")
    d = json.loads(next(b.text for b in resp.content if b.type == "text"))
    d["documento"] = d.pop("tipo_documento", None)
    d["fecha"] = _parse_fecha(d["fecha"]) if d.get("fecha") else None
    for k in ("emisor_cuit", "receptor_cuit"):
        d[k] = _norm_cuit(d.get(k))
    return d


# ================================================================ Texto: PDF y OCR
# Una "fila" es una lista de celdas (x, texto) ordenadas de izquierda a derecha.

def _filas_de_archivo(contenido: bytes, content_type: str):
    if content_type == "application/pdf":
        filas = _filas_pdf_texto(contenido)
        if sum(len(f) for f in filas) >= 3:
            return filas, "texto", None
        imagenes = _pdf_a_imagenes(contenido)
        if not imagenes:
            return [], "ninguno", "El PDF no tiene texto y no se pudo convertir a imagen."
        filas = []
        for img in imagenes:
            filas += _filas_ocr(img) or []
        if not filas:
            return [], "ninguno", _aviso_ocr()
        return filas, "ocr", None

    if content_type in IMAGE_TYPES:
        filas = _filas_ocr(contenido)
        if filas is None:
            return [], "ninguno", _aviso_ocr()
        return filas, "ocr", None if filas else "No se encontró texto en la imagen."
    return [], "ninguno", "Tipo de archivo no soportado para lectura automática."


def _aviso_ocr() -> str:
    return ("Para leer fotos o PDFs escaneados instalá el lector local "
            "(pip install -r requirements-ocr.txt) o configurá ANTHROPIC_API_KEY.")


def _filas_pdf_texto(contenido: bytes) -> list:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(contenido))
        texto = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in reader.pages)
    except Exception as exc:
        log.warning("No se pudo leer el PDF: %s", exc)
        return []
    return filas_de_texto(texto)


def filas_de_texto(texto: str) -> list:
    """Separa cada renglón en celdas: dos o más espacios seguidos marcan una nueva columna."""
    filas = []
    for linea in texto.splitlines():
        celdas = [(m.start(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", linea)]
        if celdas:
            filas.append(celdas)
    return filas


def _pdf_a_imagenes(contenido: bytes, max_paginas: int = 3) -> list:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    try:
        pdf = pdfium.PdfDocument(contenido)
        salida = []
        for i in range(min(len(pdf), max_paginas)):
            img = pdf[i].render(scale=2.5).to_pil()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            salida.append(buf.getvalue())
        return salida
    except Exception as exc:
        log.warning("No se pudo convertir el PDF a imagen: %s", exc)
        return []


_ocr_motor = None
_ocr_lock = threading.Lock()


def ocr_disponible() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _filas_ocr(contenido: bytes):
    """Devuelve filas de celdas leídas de la imagen, o None si no hay OCR instalado."""
    global _ocr_motor
    try:
        import cv2
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None

    img = cv2.imdecode(np.frombuffer(contenido, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return []
    # Las capturas chicas se leen mejor ampliadas (respeta mejor los espacios)
    lado = max(img.shape[:2])
    if lado < 1600:
        f = min(2.5, 1600 / lado)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)

    with _ocr_lock:
        if _ocr_motor is None:
            _ocr_motor = RapidOCR()
        resultado, _ = _ocr_motor(img)

    cajas = []
    for caja, texto, conf in resultado or []:
        if conf < 0.5 or not texto.strip():
            continue
        ys = [p[1] for p in caja]
        cajas.append({"x": min(p[0] for p in caja), "y0": min(ys), "y1": max(ys), "t": texto.strip()})
    return agrupar_en_filas(cajas)


def agrupar_en_filas(cajas: list) -> list:
    """Agrupa cajas de texto que están a la misma altura en una misma fila."""
    filas = []
    for c in sorted(cajas, key=lambda c: (c["y0"] + c["y1"]) / 2):
        centro, alto = (c["y0"] + c["y1"]) / 2, c["y1"] - c["y0"]
        if filas and abs(centro - filas[-1]["centro"]) < max(alto, filas[-1]["alto"]) * 0.5:
            f = filas[-1]
            f["celdas"].append(c)
            f["centro"] = (f["centro"] * (len(f["celdas"]) - 1) + centro) / len(f["celdas"])
        else:
            filas.append({"centro": centro, "alto": alto, "celdas": [c]})
    return [[(c["x"], c["t"]) for c in sorted(f["celdas"], key=lambda c: c["x"])] for f in filas]


# ================================================================ Análisis por etiquetas

def _clave(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", s)


# Etiquetas conocidas -> campo. Se compara el texto normalizado (sin espacios ni signos).
ETIQUETAS = [
    ("importetotal", "total"), ("totalapagar", "total"), ("totalfactura", "total"), ("total", "total"),
    ("importe", "importe"), ("monto", "importe"), ("montotransferido", "importe"), ("importetransferido", "importe"),
    ("fechadeemision", "fecha"), ("fechaemision", "fecha"), ("fechadeoperacion", "fecha"), ("fechaoperacion", "fecha"),
    ("fechadetransferencia", "fecha"), ("fecha", "fecha"),
    ("ndereferencia", "numero"), ("nreferencia", "numero"), ("nroreferencia", "numero"), ("nrodereferencia", "numero"),
    ("numerodereferencia", "numero"), ("nrodeoperacion", "numero"), ("numerodeoperacion", "numero"),
    ("nrooperacion", "numero"), ("ndeoperacion", "numero"), ("nrodecomprobante", "numero"),
    ("nrocomprobante", "numero"), ("ndecomprobante", "numero"), ("idoperacion", "numero"), ("codigodeoperacion", "numero"),
    ("titularidad", "nombre"), ("titular", "nombre"), ("razonsocial", "nombre"), ("nombre", "nombre"),
    ("apellidoynombre", "nombre"), ("apellidoynombrerazonsocial", "nombre"), ("denominacion", "nombre"),
    ("cuitcuilcdi", "cuit"), ("cuitcuil", "cuit"), ("cuit", "cuit"), ("cuil", "cuit"),
    ("cbucvudestino", "cbu"), ("cbudestino", "cbu"), ("cvudestino", "cbu"), ("cbucvu", "cbu"), ("cbu", "cbu"),
    ("motivo", "motivo"), ("concepto", "concepto"), ("referencia", "referencia"),
    ("cuentaorigen", "ignorar"), ("cuentadestino", "ignorar"), ("hora", "ignorar"), ("seuo", "ignorar"),
    ("moneda", "moneda"),
]
SECCIONES = [
    ("datosordenante", "emisor"), ("datosdelordenante", "emisor"), ("ordenante", "emisor"),
    ("datosdeorigen", "emisor"), ("origen", "emisor"), ("remitente", "emisor"),
    ("datosbeneficiario", "receptor"), ("datosdelbeneficiario", "receptor"), ("beneficiario", "receptor"),
    ("destinatario", "receptor"), ("datosdedestino", "receptor"), ("destino", "receptor"),
]


def _etiqueta(texto: str):
    """Si la celda es una etiqueta devuelve (campo, valor_en_linea). Si no, None."""
    if ":" in texto:
        izq, _, der = texto.partition(":")
        k = _clave(izq)
    else:
        k, der = _clave(texto), ""
    if not k or len(k) > 30:
        return None
    for nombre, seccion in SECCIONES:
        if k == nombre:
            return ("seccion:" + seccion, der.strip())
    for nombre, campo in ETIQUETAS:
        if k == nombre:
            return (campo, der.strip())
    return None


_RE_FECHA = re.compile(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4}|\d{2})\b")
_RE_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RE_CUIT = re.compile(r"\b(\d{2})\s*-?\s*(\d{8})\s*-?\s*(\d)\b")
_RE_MONTO = re.compile(r"-?\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?")


def _valido(campo: str, valor: str) -> bool:
    if campo == "fecha":
        return _parse_fecha(valor) is not None
    if campo == "cuit":
        return _RE_CUIT.search(valor) is not None
    if campo in ("importe", "total"):
        return _monto_de(valor) is not None
    if campo == "numero":
        return re.search(r"\d{3,}", valor) is not None
    if campo == "cbu":
        return re.search(r"\d{10,}", valor) is not None
    return bool(valor.strip())


def analizar_filas(filas: list) -> dict:
    """Busca los campos por etiqueta en las filas y completa lo que falte con reglas generales."""
    texto = "\n".join("   ".join(t for _, t in f) for f in filas)
    campos: dict[str, list] = {}
    seccion = None

    def guardar(campo, valor):
        valor = valor.strip()
        if campo in ("nombre", "cuit") and seccion:
            campo = f"{seccion}_{campo}"
        campos.setdefault(campo, []).append(valor)

    for i, fila in enumerate(filas):
        usadas = set()
        for j, (x, t) in enumerate(fila):
            if j in usadas:
                continue
            et = _etiqueta(t)
            if not et:
                continue
            campo, en_linea = et
            if campo.startswith("seccion:"):
                seccion = campo.split(":")[1]
                continue
            if campo == "ignorar":
                continue
            if en_linea and _valido(campo, en_linea):
                guardar(campo, en_linea)
                continue
            # Valor a la derecha en la misma fila (la primera celda que no sea otra etiqueta)
            valor = None
            for k in range(j + 1, len(fila)):
                if _etiqueta(fila[k][1]):
                    break
                if _valido(campo, fila[k][1]):
                    valor = fila[k][1]
                    usadas.add(k)
                break
            # Si no hay, el valor suele estar debajo, en la misma columna
            if valor is None and i + 1 < len(filas):
                abajo = [c for c in filas[i + 1] if not _etiqueta(c[1])]
                if abajo:
                    cx, ct = min(abajo, key=lambda c: abs(c[0] - x))
                    if _valido(campo, ct):
                        valor = ct
            if valor is not None:
                guardar(campo, valor)

    primero = lambda k: (campos.get(k) or [None])[0]
    clave_texto = _clave(texto)
    if "transferencia" in clave_texto or primero("emisor_cuit") and primero("receptor_cuit") and primero("cbu"):
        documento = "transferencia"
    elif "factura" in clave_texto:
        documento = "factura"
    elif "recibo" in clave_texto:
        documento = "recibo"
    elif "ticket" in clave_texto:
        documento = "ticket"
    else:
        documento = "otro"

    # Monto: en facturas manda el total (el mayor, porque "Subtotal" también aparece); si no, el importe
    totales = [m for m in map(_monto_de, campos.get("total", [])) if m]
    importes = [m for m in map(_monto_de, campos.get("importe", [])) if m]
    if documento == "factura" and totales:
        monto = max(totales)
    else:
        monto = importes[0] if importes else (max(totales) if totales else None)

    d = {
        "documento": documento,
        "monto": monto,
        "fecha": _parse_fecha(primero("fecha") or "") if primero("fecha") else None,
        "numero": _limpiar_numero(primero("numero")),
        "emisor_nombre": primero("emisor_nombre"),
        "emisor_cuit": _norm_cuit(primero("emisor_cuit")),
        "receptor_nombre": primero("receptor_nombre"),
        "receptor_cuit": _norm_cuit(primero("receptor_cuit")),
        "moneda": _moneda(texto),
    }

    # Sin secciones (típico de facturas): el primer nombre/CUIT es el emisor y el segundo el receptor
    nombres = campos.get("nombre", [])
    cuits = [c for c in map(_norm_cuit, campos.get("cuit", [])) if c]
    d["emisor_nombre"] = d["emisor_nombre"] or (nombres[0] if nombres else None)
    d["receptor_nombre"] = d["receptor_nombre"] or (nombres[1] if len(nombres) > 1 else None)
    d["emisor_cuit"] = d["emisor_cuit"] or (cuits[0] if cuits else None)
    d["receptor_cuit"] = d["receptor_cuit"] or (cuits[1] if len(cuits) > 1 else None)

    _completar_con_reglas(d, texto, filas)

    detalle = []
    if documento == "transferencia":
        detalle.append("Transferencia")
    for etiqueta, k in (("Motivo", "motivo"), ("Concepto", "concepto"), ("Ref.", "referencia")):
        if primero(k):
            detalle.append(f"{etiqueta}: {primero(k)}")
    if primero("cbu"):
        cbu = re.sub(r"\D", "", primero("cbu"))
        detalle.append(f"CBU/CVU destino: {cbu}")
    if len(detalle) <= 1:
        lineas = ["  ".join(t for _, t in f) for f in filas[:6]]
        detalle.append(" | ".join(lineas))
    d["descripcion"] = " · ".join(detalle)[:300]
    return d


# ================================================================ Reglas generales (respaldo)

_RE_TOTAL = re.compile(r"(?:importe\s*total|total\s*a\s*pagar|total)\s*[:$]?\s*(?:\$|ARS|USD|U\$S)?\s*(" + _RE_MONTO.pattern + ")", re.I)
_RE_NUMERO_FACT = re.compile(r"(?:n[°º]|nro\.?|n[uú]mero|comp\.?\s*nro\.?)\s*[:.]?\s*([A-C]?\s*\d{4,5}\s*-\s*\d{6,8})", re.I)
_RE_PV_NUM = re.compile(r"\b(\d{4,5})\s*-\s*(\d{8})\b")
_RE_PV_SEPARADO = re.compile(r"punto\s+de\s+venta\s*:?\s*(\d{1,5}).{0,40}?comp\.?\s*nro\.?\s*:?\s*(\d{1,8})", re.I | re.S)
_RE_RAZON = re.compile(r"(?:raz[oó]n\s+social|proveedor|emisor)\s*[:.]?\s*(.+)", re.I)
_RE_MONEDA = {"USD": re.compile(r"(\bUSD\b|U\$S|US\$|d[oó]lares?)", re.I), "EUR": re.compile(r"(€|\bEUR\b|euros?)", re.I)}


def _completar_con_reglas(d: dict, texto: str, filas: list) -> None:
    if d["monto"] is None:
        totales = [m for m in (_monto_de(m.group(1)) for m in _RE_TOTAL.finditer(texto)) if m]
        if totales:
            d["monto"] = max(totales)
    if d["fecha"] is None:
        d["fecha"] = _parse_fecha(texto)
    if d["numero"] is None:
        if (m := _RE_NUMERO_FACT.search(texto)):
            d["numero"] = re.sub(r"\s+", " ", m.group(1)).strip()
        elif (m := _RE_PV_NUM.search(texto)):
            d["numero"] = f"{m.group(1)}-{m.group(2)}"
        elif (m := _RE_PV_SEPARADO.search(texto)):
            d["numero"] = f"{int(m.group(1)):05d}-{int(m.group(2)):08d}"
    if d["emisor_cuit"] is None:
        cuits = [_norm_cuit(m.group()) for m in _RE_CUIT.finditer(texto)]
        cuits = list(dict.fromkeys(c for c in cuits if c))
        if cuits:
            d["emisor_cuit"] = cuits[0]
            if d["receptor_cuit"] is None and len(cuits) > 1:
                d["receptor_cuit"] = cuits[1]
    if d["emisor_nombre"] is None:
        if (m := _RE_RAZON.search(texto)):
            d["emisor_nombre"] = re.split(r"\s{2,}", m.group(1).strip())[0][:120]
        elif filas:
            d["emisor_nombre"] = filas[0][0][1][:120]


def _moneda(texto: str) -> str:
    for codigo, rx in _RE_MONEDA.items():
        if rx.search(texto):
            return codigo
    return "ARS"


def parse_monto(s: str) -> float | None:
    s = s.strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        # el último separador es el decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        partes = s.split(",")
        s = s.replace(",", ".") if len(partes[-1]) in (1, 2) else s.replace(",", "")
    elif s.count(".") == 1 and len(s.split(".")[-1]) == 3:
        s = s.replace(".", "")  # 1.500 -> 1500
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _monto_de(s: str | None) -> float | None:
    if not s:
        return None
    m = _RE_MONTO.search(s.replace("$", " "))
    v = parse_monto(m.group()) if m else None
    return abs(v) if v else None


def _parse_fecha(s: str) -> str | None:
    m = _RE_FECHA_ISO.search(s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = _RE_FECHA.search(s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _norm_cuit(s: str | None) -> str | None:
    if not s:
        return None
    m = _RE_CUIT.search(s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def norm_cuit(s: str | None) -> str | None:
    """Normaliza un CUIT a XX-XXXXXXXX-X (acepta con o sin guiones)."""
    digitos = re.sub(r"\D", "", s or "")
    return f"{digitos[:2]}-{digitos[2:10]}-{digitos[10]}" if len(digitos) == 11 else None


def _limpiar_numero(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", s).strip()[:60]


# ================================================================ Contraparte e ingreso/egreso

def resolver(d: dict, cuits_propios: list[str]) -> dict:
    """Decide ingreso/egreso y la contraparte a partir de emisor/receptor y los CUIT propios."""
    propios = {c for c in map(norm_cuit, cuits_propios) if c}
    emisor = (d.get("emisor_nombre"), d.get("emisor_cuit"))
    receptor = (d.get("receptor_nombre"), d.get("receptor_cuit"))
    documento = d.get("documento") or "otro"

    propio = None  # el CUIT propio que aparece en el comprobante, si alguno
    if documento == "transferencia":
        # Transferencia: emisor = ordenante (paga), receptor = beneficiario (cobra)
        if emisor[1] and emisor[1] in propios:
            tipo, contra, motivo, propio = "egreso", receptor, "el ordenante es tu CUIT", emisor[1]
        elif receptor[1] and receptor[1] in propios:
            tipo, contra, motivo, propio = "ingreso", emisor, "el beneficiario es tu CUIT", receptor[1]
        else:
            tipo, contra, motivo = "egreso", receptor, "transferencia enviada (por defecto)"
    else:
        # Factura/recibo: emisor = quien cobra
        if emisor[1] and emisor[1] in propios:
            tipo, contra, motivo, propio = "ingreso", receptor, "la emitiste vos", emisor[1]
        elif receptor[1] and receptor[1] in propios:
            tipo, contra, motivo, propio = "egreso", emisor, "la recibiste vos", receptor[1]
        else:
            tipo, contra, motivo = "egreso", emisor, "comprobante recibido (por defecto)"

    nombre_contra = _separar_nombre(contra[0], d.get("descripcion") or "")
    salida = {
        "documento": documento,
        "tipo": tipo,
        "tipo_motivo": motivo,
        "monto": d.get("monto"),
        "moneda": (d.get("moneda") or "ARS").upper(),
        "fecha_factura": d.get("fecha"),
        "numero_factura": d.get("numero"),
        "tercero": (nombre_contra or "").strip()[:120] or None,
        "cuit": contra[1],
        "descripcion": d.get("descripcion"),
        "cuit_propio": propio,
    }
    # Si no hay CUIT propio configurado, se sugiere el del lado "propio" según el tipo asumido
    if not propios:
        propio = emisor if tipo == "egreso" else receptor
        if propio[1] and propio[1] != contra[1]:
            salida["sugerencia_cuit_propio"] = {"cuit": propio[1], "nombre": propio[0]}
    return {k: v for k, v in salida.items() if v not in (None, "")}


def _separar_nombre(nombre: str | None, pistas: str) -> str | None:
    """El OCR a veces junta palabras en mayúsculas ("MALDONADONELSON"). Si otra parte del
    comprobante trae la primera palabra separada (p. ej. el motivo), se separa ahí."""
    if not nombre or " " in nombre.strip() or len(nombre) < 10:
        return nombre
    candidatas = sorted({w for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{4,}", pistas)}, key=len, reverse=True)
    for w in candidatas:
        if nombre.upper().startswith(w.upper()) and len(nombre) - len(w) >= 3:
            return f"{nombre[:len(w)]} {nombre[len(w):]}"
    return nombre


# Compatibilidad: análisis directo de un texto plano
def extraer_de_texto(texto: str, cuits_propios: list[str] | None = None) -> dict:
    return resolver(analizar_filas(filas_de_texto(texto)), cuits_propios or [])
