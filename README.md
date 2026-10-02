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
  - Lee **PDFs con texto**, y también **fotos, capturas de pantalla y PDFs escaneados** con un lector local (OCR), sin internet ni costo.
  - Opcional, con `ANTHROPIC_API_KEY`: usa IA (Claude) para leer con más precisión.
- **Carga automática**: subís (o arrastrás) uno o varios comprobantes —facturas o transferencias bancarias— y el movimiento se guarda solo:
  - Detecta monto, fecha, n.º de factura o referencia, contraparte, CUIT, CBU, motivo y concepto.
  - Decide si es **ingreso o egreso** según el CUIT de tu empresa (se configura una vez; la app lo sugiere al primer comprobante).
  - **Aprende**: si corregís el nombre o la categoría de un proveedor, los próximos comprobantes de ese CUIT ya vienen con esos datos.
  - Avisa si un comprobante **ya estaba cargado** (mismo n.º y monto).
  - Los movimientos cargados solos quedan marcados "⚡ a revisar" hasta que los abrís y guardás.
- **Desglose bruto → retenciones → neto**: en avisos de pago (y donde haya retenciones o deducciones) el movimiento se registra por el **neto** efectivamente cobrado o pagado, guardando el bruto y cada retención (IVA, Ganancias, Ingresos Brutos, Seguridad Social, etc.). Se puede ver y cargar a mano desde la sección "Desglose" del formulario; el dashboard suma las **retenciones sufridas** por tipo (pagos a cuenta de impuestos) y las exportaciones las incluyen.
- **Proveedor/cliente, CUIT, categoría y detalle/referencias.**
- **Resumen** de ingresos, egresos y saldo por moneda, con **filtros** por tipo, fechas y texto.
- **Exportación a CSV** (se abre en Excel).
- **Dashboard** (pestaña "Dashboard"):
  - Indicadores del período: ingresos, egresos, saldo y margen.
  - Gráfico de ingresos vs. egresos por mes y saldo acumulado.
  - **Segmentación** por categoría, proveedor/cliente o con factura / manual: ranking de egresos e ingresos con % y una matriz segmento × mes.
  - **Hoja de resumen mensual** (mes, ingresos, egresos, saldo, acumulado, cantidad), descargable para Excel.
  - Filtros de período (últimos 12 meses, este año, año anterior, últimos 6 meses o personalizado) y moneda.
- **Varias empresas** (vienen creadas **Solvencias** y **Agencia**): se elige arriba y todo queda separado —movimientos, resumen, dashboard, categorías, exportaciones y CUIT propio—.
  - Se pueden renombrar (✎), eliminar (🗑, solo si no tienen movimientos) y agregar más (+ Empresa).
  - La carga automática manda cada comprobante a la empresa cuyo CUIT figura en él, aunque estés viendo la otra.
  - Un movimiento se puede pasar a otra empresa desde su edición.
  - Lo cargado antes de separar por empresas quedó en la primera (Solvencias).
- **Base de datos**: SQLite en `data/csa.db`. Las facturas se guardan en `data/facturas/`.

## Cómo probarla (doble clic)

1. Instalá Python desde https://www.python.org/downloads/ (en Windows tildá **"Add Python to PATH"**).
2. Descargá este proyecto (botón **Code → Download ZIP**) y descomprimilo.
3. Doble clic en **`iniciar.bat`** (Windows) o **`iniciar.command`** (Mac).
4. Se abre el navegador solo. La ventana negra muestra también la dirección para entrar desde el celular.

## Cómo levantarla (por consola)

```bash
pip install -r requirements.txt
pip install -r requirements-ocr.txt   # opcional: lector de fotos
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
| POST | `/api/carga-automatica` | Lee el comprobante y guarda el movimiento (`forzar=true` ignora duplicados) |
| GET / PUT | `/api/ajustes` | CUIT propios de la empresa (`{"cuits_propios": [...]}`) |
| GET / POST | `/api/empresas` | Listar / crear empresas |
| PUT / DELETE | `/api/empresas/{id}` | Renombrar o cambiar CUIT / borrar (solo si no tiene movimientos) |
| GET | `/api/resumen` | Totales por moneda |
| GET | `/api/exportar.csv` | Exportación |
| GET | `/api/dashboard?desde=&hasta=&moneda=&segmento=categoria\|tercero\|factura` | Resumen mensual y segmentación |
| GET | `/api/dashboard.csv` | Hoja de resumen mensual para Excel |

Todas las rutas de movimientos, resumen, dashboard y exportación aceptan `?empresa={id}` (si no se indica, usan la primera).
