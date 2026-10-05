"""Lector de imágenes (OCR) que corre en un proceso aparte.

Se ejecuta como `python -m app.ocr_worker`: recibe la imagen por la entrada estándar y
devuelve por la salida estándar un JSON con las cajas de texto encontradas. Al correr
separado, si el OCR se cuelga, se queda sin memoria o se cierra de golpe, la app
principal sigue funcionando y solo informa que no pudo leer la imagen.
"""
import json
import sys

LADO_MIN = 1600  # las capturas chicas se leen mejor ampliadas (respeta mejor los espacios)
LADO_MAX = 2000  # las imágenes enormes no mejoran la lectura y usan mucha memoria


def leer_cajas(contenido: bytes) -> list[dict]:
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    img = cv2.imdecode(np.frombuffer(contenido, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return []
    lado = max(img.shape[:2])
    if lado < LADO_MIN:
        f = min(2.5, LADO_MIN / lado)
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    elif lado > LADO_MAX:
        f = LADO_MAX / lado
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)

    resultado, _ = RapidOCR()(img)
    cajas = []
    for caja, texto, conf in resultado or []:
        if conf < 0.5 or not texto.strip():
            continue
        ys = [float(p[1]) for p in caja]
        cajas.append({"x": float(min(p[0] for p in caja)), "y0": min(ys), "y1": max(ys), "t": texto.strip()})
    return cajas


if __name__ == "__main__":
    salida = leer_cajas(sys.stdin.buffer.read())
    sys.stdout.write(json.dumps(salida, ensure_ascii=True))
