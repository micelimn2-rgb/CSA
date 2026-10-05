"""Arranca la app: instala lo necesario, muestra las direcciones y abre el navegador."""
import json
import socket
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

PUERTO = 8000
CARPETA = Path(__file__).resolve().parent


def ip_local() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def version_en_ejecucion() -> str | None:
    """Si ya hay algo usando el puerto, devuelve la versión de la app que responde ("?" si no es esta app)."""
    try:
        socket.create_connection(("127.0.0.1", PUERTO), timeout=1).close()
    except OSError:
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO}/api/version", timeout=3) as r:
            return json.load(r).get("version", "?")
    except Exception:
        return "?"


def version_local() -> str:
    texto = (CARPETA / "app" / "__init__.py").read_text(encoding="utf-8")
    return texto.split('VERSION = "')[1].split('"')[0]


def probar_lector_de_fotos() -> None:
    """Lee una imagen de prueba con el lector de fotos y avisa en esta ventana si funciona."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("  Lector de fotos: no instalado (solo se leen automaticamente PDFs con texto).")
        return
    img = np.full((80, 400, 3), 255, np.uint8)
    cv2.putText(img, "Importe 1234,56", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    png = cv2.imencode(".png", img)[1].tobytes()
    try:
        proc = subprocess.run([sys.executable, "-m", "app.ocr_worker"], input=png, capture_output=True,
                              cwd=CARPETA, timeout=180)
        ok = proc.returncode == 0 and b"1234" in proc.stdout
    except subprocess.TimeoutExpired:
        ok, proc = False, None
    if ok:
        print("  Lector de fotos: funcionando OK")
    else:
        motivo = "tardo demasiado" if proc is None else (proc.stderr.decode("utf-8", "replace").strip().splitlines()
                                                          or [f"codigo {proc.returncode}"])[-1][:150]
        print(f"  Lector de fotos: NO funciona ({motivo}).")
        print("  Las fotos y PDFs escaneados se van a tener que completar a mano.")


def esperar_y_salir(codigo: int = 0) -> None:
    input("\nApretá Enter para cerrar esta ventana...")
    sys.exit(codigo)


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Se necesita Python 3.10 o más nuevo. Bajalo de https://www.python.org/downloads/")

    otra = version_en_ejecucion()
    if otra == version_local():
        print("La app ya está abierta en otra ventana. Abro el navegador.")
        webbrowser.open(f"http://localhost:{PUERTO}")
        esperar_y_salir()
    elif otra is not None:
        print("=" * 60)
        print("  Hay OTRA copia de la app abierta (una versión anterior).")
        print("  Cerrá todas las demás ventanas negras de la app y volvé")
        print("  a abrir iniciar.bat.")
        print("=" * 60)
        esperar_y_salir(1)

    print("Instalando / verificando lo necesario (la primera vez tarda un poco)...")
    pip = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r"]
    subprocess.run(pip + [str(CARPETA / "requirements.txt")], check=True)
    # El lector de fotos es opcional: si no se puede instalar, la app igual funciona
    if subprocess.run(pip + [str(CARPETA / "requirements-ocr.txt")]).returncode != 0:
        print("\nAviso: no se pudo instalar el lector de fotos. La app funciona igual,")
        print("pero solo va a leer automáticamente PDFs con texto.\n")

    ip = ip_local()
    print("\n" + "=" * 60)
    print(f"  En esta computadora:  http://localhost:{PUERTO}")
    if ip:
        print(f"  Desde el celular:     http://{ip}:{PUERTO}  (mismo Wi-Fi)")
    print("  Para cerrar la app, cerrá esta ventana o apretá Ctrl+C.")
    print("=" * 60 + "\n")

    threading.Thread(target=probar_lector_de_fotos, daemon=True).start()
    threading.Timer(2.5, lambda: webbrowser.open(f"http://localhost:{PUERTO}")).start()
    import uvicorn
    sys.path.insert(0, str(CARPETA))
    uvicorn.run("app.main:app", host="0.0.0.0", port=PUERTO)


if __name__ == "__main__":
    main()
