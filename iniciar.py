"""Arranca la app: instala lo necesario, muestra las direcciones y abre el navegador."""
import socket
import subprocess
import sys
import threading
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


def main() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Se necesita Python 3.10 o más nuevo. Bajalo de https://www.python.org/downloads/")

    print("Instalando / verificando lo necesario (la primera vez tarda un poco)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                    "-r", str(CARPETA / "requirements.txt")], check=True)

    ip = ip_local()
    print("\n" + "=" * 60)
    print(f"  En esta computadora:  http://localhost:{PUERTO}")
    if ip:
        print(f"  Desde el celular:     http://{ip}:{PUERTO}  (mismo Wi-Fi)")
    print("  Para cerrar la app, cerrá esta ventana o apretá Ctrl+C.")
    print("=" * 60 + "\n")

    threading.Timer(2.5, lambda: webbrowser.open(f"http://localhost:{PUERTO}")).start()
    import uvicorn
    sys.path.insert(0, str(CARPETA))
    uvicorn.run("app.main:app", host="0.0.0.0", port=PUERTO)


if __name__ == "__main__":
    main()
