# CSA · Ingresos y Gastos

App web para registrar ingresos y egresos, con o sin factura. Se usa desde la
computadora o el celular, y en el celular se puede instalar como app
("Agregar a pantalla de inicio").

## Qué hace

- **Ingreso o egreso**: se elige con un botón en cada movimiento.
- **Monto, moneda y fecha de la factura**: la fecha de la factura viene con el día de hoy y se puede cambiar.
- **Fecha de carga automática**: se guarda sola al crear el movimiento y no se puede editar.
- **Factura adjunta (opcional)**: PDF o foto, también directo con la cámara del celular. Si no hay factura, el gasto se carga a mano.
- **Lectura automática de la factura**: al adjuntarla se completan monto, fecha, n.º de factura, proveedor, CUIT y detalle. Revisá los datos antes de guardar.
  - Sin configuración extra: lee **PDFs con texto** (las facturas electrónicas de AFIP, por ejemplo).
  - Con `ANTHROPIC_API_KEY`: lee también **fotos y PDFs escaneados** usando IA (Claude).
- **Proveedor/cliente, CUIT, categoría y detalle/referencias.**
- **Resumen** de ingresos, egresos y saldo por moneda, con **filtros** por tipo, fechas y texto.
- **Exportación a CSV** (se abre en Excel).
- **Base de datos**: SQLite en `data/csa.db`. Las facturas se guardan en `data/facturas/`.

## Cómo levantarla

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Abrí `http://localhost:8000`. Desde el celular, en la misma red Wi-Fi, abrí
`http://IP-DE-LA-PC:8000`.

### Variables de entorno (opcionales)

| Variable | Para qué sirve |
|---|---|
| `ANTHROPIC_API_KEY` | Activa la lectura de fotos y PDFs escaneados con IA |
| `APP_USER` / `APP_PASSWORD` | Pide usuario y clave para entrar. Recomendado si la app está publicada en internet |
| `CSA_DATA_DIR` | Carpeta de la base de datos y las facturas (por defecto `./data`) |

## Tests

```bash
pip install pytest httpx
python -m pytest -q
```

## Estructura

```
app/main.py       API (FastAPI) y servidor de la web
app/db.py         Base de datos SQLite
app/extractor.py  Lectura de datos de facturas
static/           Interfaz web (HTML/CSS/JS), instalable como app
tests/            Tests automáticos
```

## API

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/movimientos?tipo=&desde=&hasta=&q=&categoria=` | Listado con filtros |
| POST | `/api/movimientos` | Alta (form-data, `archivo` opcional) |
| GET / PUT / DELETE | `/api/movimientos/{id}` | Ver, editar o borrar |
| GET | `/api/movimientos/{id}/adjunto` | Ver la factura adjunta |
| POST | `/api/leer-factura` | Devuelve los datos leídos de un PDF o una imagen |
| GET | `/api/resumen` | Totales por moneda |
| GET | `/api/exportar.csv` | Exportación |
