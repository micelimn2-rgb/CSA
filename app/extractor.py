"""Lectura de datos de facturas.

Dos estrategias:
1. Si hay ANTHROPIC_API_KEY, se envía el PDF o la foto a Claude, que devuelve
   los datos estructurados (funciona también con fotos y PDFs escaneados).
2. Sin clave, se extrae el texto del PDF y se aplican reglas (regex) para
   encontrar total, fecha, número, CUIT y razón social. Las fotos no se pueden
   leer en este modo.
"""
import base64
import io
import json
import logging
import os
import re
from datetime import date

log = logging.getLogger(__name__)

CLAUDE_MODEL = os.environ.get("CSA_CLAUDE_MODEL", "claude-opus-5-5")
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

CAMPOS_SCHEMA = {
    "type": "object",
    "properties": {
        "monto": {"type": ["number", "null"], "description": "Importe total final de la factura"},
        "moneda": {"type": ["string", "null"], "description": "Código ISO: ARS, USD, EUR..."},
        "fecha_factura": {"type": ["string", "null"], "description": "Fecha de emisión en formato YYYY-MM-DD"},
        "numero_factura": {"type": ["string", "null"], "description": "Tipo y número, ej. 'A 0001-00001234'"},
        "tercero": {"type": ["string", "null"], "description": "Razón social del emisor"},
        "cuit": {"type": ["string", "null"], "description": "CUIT/RUT/NIF del emisor"},
        "descripcion": {"type": ["string", "null"], "description": "Resumen breve de los conceptos facturados"},
    },
    "required": ["monto", "moneda", "fecha_factura", "numero_factura", "tercero", "cuit", "descripcion"],
    "additionalProperties": False,
}

PROMPT = (
    "Sos un asistente contable. Extraé los datos de esta factura o comprobante. "
    "Si un dato no aparece, devolvé null. El monto debe ser el TOTAL final como número "
    "(sin separadores de miles, punto decimal). La descripción debe ser corta (máx. 200 caracteres) "
    "y listar los principales conceptos o referencias."
)


def extraer(contenido: bytes, content_type: str) -> dict:
    """Devuelve un dict con los campos encontrados y 'metodo' usado."""
    if os.environ.get("ANTHROPIC_API_KEY") and (content_type == "application/pdf" or content_type in IMAGE_TYPES):
        try:
            datos = _extraer_con_claude(contenido, content_type)
            return {**_limpiar(datos), "metodo": "ia"}
        except Exception as exc:  # si falla la IA, se intenta el modo local
            log.warning("Fallo la extracción con Claude: %s", exc)

    if content_type == "application/pdf":
        texto = _texto_pdf(contenido)
        if texto.strip():
            return {**_limpiar(extraer_de_texto(texto)), "metodo": "texto"}
        return {"metodo": "ninguno", "aviso": "El PDF no tiene texto (¿escaneado?). Configurá ANTHROPIC_API_KEY para leerlo."}

    if content_type in IMAGE_TYPES:
        return {"metodo": "ninguno", "aviso": "Para leer fotos de facturas configurá ANTHROPIC_API_KEY."}
    return {"metodo": "ninguno", "aviso": "Tipo de archivo no soportado para lectura automática."}


# ---------------------------------------------------------------- Claude

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
    texto = next(b.text for b in resp.content if b.type == "text")
    return json.loads(texto)


# ---------------------------------------------------------------- Modo local (texto + reglas)

def _texto_pdf(contenido: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(contenido))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        log.warning("No se pudo leer el PDF: %s", exc)
        return ""


_NUM = r"(?:\$|ARS|USD|U\$S|US\$|€)?\s*(-?\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|-?\d+(?:[.,]\d{1,2})?)"
_RE_TOTAL = re.compile(r"(?:importe\s+total|total\s+a\s+pagar|total\s+factura|total)\s*[:$]?\s*" + _NUM, re.I)
_RE_FECHA_ETIQ = re.compile(r"fecha(?:\s+de)?(?:\s+emisi[oó]n)?\s*[:.]?\s*(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4})", re.I)
_RE_FECHA = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")
_RE_FECHA_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RE_CUIT = re.compile(r"\b(\d{2})[- ]?(\d{8})[- ]?(\d)\b")
_RE_NUMERO = re.compile(
    r"(?:(?:factura|fact\.?|comprobante|ticket|recibo)\s*(?:[A-C]\b)?\s*)?(?:n[°ºro.]*|nro\.?|n[uú]mero)\s*[:.]?\s*([A-C]?\s*\d{4,5}\s*-\s*\d{6,8})",
    re.I,
)
_RE_PV_NUM = re.compile(r"\b(\d{4,5})\s*-\s*(\d{8})\b")
_RE_PV_SEPARADO = re.compile(r"punto\s+de\s+venta\s*:?\s*(\d{1,5}).{0,40}?comp\.?\s*nro\.?\s*:?\s*(\d{1,8})", re.I | re.S)
_RE_RAZON = re.compile(r"(?:raz[oó]n\s+social|proveedor|emisor)\s*[:.]?\s*(.+)", re.I)
_RE_MONEDA = {"USD": re.compile(r"\b(USD|U\$S|US\$|d[oó]lares?)\b", re.I), "EUR": re.compile(r"(€|\bEUR\b|euros?)", re.I)}


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


def extraer_de_texto(texto: str) -> dict:
    datos: dict = {}

    totales = [parse_monto(m.group(1)) for m in _RE_TOTAL.finditer(texto)]
    totales = [t for t in totales if t is not None and t > 0]
    if totales:
        # "Subtotal" también matchea; el total final suele ser el mayor
        datos["monto"] = max(totales)

    m = _RE_FECHA_ETIQ.search(texto)
    datos["fecha_factura"] = _parse_fecha(m.group(1) if m else texto)

    m = _RE_NUMERO.search(texto)
    if m:
        datos["numero_factura"] = re.sub(r"\s+", " ", m.group(1)).strip()
    elif (m := _RE_PV_NUM.search(texto)):
        datos["numero_factura"] = f"{m.group(1)}-{m.group(2)}"
    elif (m := _RE_PV_SEPARADO.search(texto)):
        datos["numero_factura"] = f"{int(m.group(1)):05d}-{int(m.group(2)):08d}"

    m = _RE_CUIT.search(texto)
    if m:
        datos["cuit"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = _RE_RAZON.search(texto)
    if m:
        datos["tercero"] = m.group(1).strip()[:120]
    else:
        primera = next((l.strip() for l in texto.splitlines() if len(l.strip()) > 3), None)
        if primera:
            datos["tercero"] = primera[:120]

    datos["moneda"] = "ARS"
    for codigo, rx in _RE_MONEDA.items():
        if rx.search(texto):
            datos["moneda"] = codigo
            break

    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    datos["descripcion"] = " | ".join(lineas[:6])[:300] if lineas else None
    return datos


def _limpiar(datos: dict) -> dict:
    return {k: v for k, v in datos.items() if v not in (None, "")}
