#!/bin/bash
# Doble clic en Mac para arrancar la app
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null; then
  echo "No se encontró Python. Instalalo desde https://www.python.org/downloads/"
  read -p "Enter para cerrar"; exit 1
fi
python3 iniciar.py
