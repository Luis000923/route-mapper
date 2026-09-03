# route-mapper

Crawler modular en Python para mapear las rutas internas de un sitio web y
detectar enlaces rotos. Recorre un dominio a partir de una URL inicial, sigue
solo los enlaces internos y genera un reporte con el estado de cada ruta.

## Características

- Rastreo limitado por numero de paginas y por profundidad de enlaces.
- Respeta `robots.txt` (se puede desactivar).
- Peticiones concurrentes configurables, con reintentos y backoff.
- Opcion de seguir subdominios del mismo dominio.
- Reportes en varios formatos: `txt`, `json`, `csv` y `html`.
- Arquitectura por capas: configuracion, HTTP, parseo, crawler y reporters,
  cada una en su modulo y con responsabilidad unica.

## Requisitos

- Python 3.10 o superior.
- [uv](https://docs.astral.sh/uv/) para gestionar el entorno y las dependencias.

## Instalación

```bash
uv sync
```

Esto crea el entorno virtual `.venv` e instala el paquete en modo editable.

## Uso

```bash
uv run route-mapper https://example.com
```

Tambien se puede ejecutar como modulo:

```bash
uv run python -m route_mapper https://example.com
```

### Opciones principales

| Opcion | Descripcion | Por defecto |
| --- | --- | --- |
| `-m`, `--max-pages` | Maximo de paginas a analizar | 500 |
| `--max-depth` | Profundidad maxima de enlaces | sin limite |
| `-d`, `--delay` | Pausa entre lotes en segundos | 0.2 |
| `-t`, `--timeout` | Timeout por peticion en segundos | 10 |
| `-c`, `--concurrency` | Peticiones en paralelo | 1 |
| `--retries` | Reintentos ante fallos de red | 2 |
| `--max-redirects` | Redirecciones máximas por petición | 5 |
| `--global-timeout` | Tiempo máximo total del crawl en segundos | 300 |
| `--max-links-per-page` | Enlaces máximos extraídos por página | 1000 |
| `-H`, `--header` | Cabecera HTTP personalizada `Nombre: Valor` (repetible) | - |
| `--login-url` | URL del formulario/endpoint de login (activa la autenticación previa) | - |
| `--login-user` / `--login-pass` | Credenciales para el login (obligatorias con `--login-url`) | - |
| `--user-field` / `--pass-field` | Nombre de los campos de usuario/contraseña en el formulario o JSON | `username` / `password` |
| `--auth-type` | Tipo de autenticación: `form` o `json` | `form` |
| `--token-key` | Clave del token en la respuesta JSON → `Authorization: Bearer <token>` | - |
| `--proxy` | Canaliza todo el tráfico por un proxy `http(s)://` o `socks5(h)://` (Burp, Tor) | - |
| `--ua-file` | Archivo con User-Agents (uno por línea) para rotación aleatoria por petición | - |
| `--jitter` | Variación aleatoria ±segundos sobre la pausa entre lotes | 0 |
| `--sitemap` | Siembra la cola con las URLs declaradas en `/sitemap.xml` | desactivado |
| `--parse-js` / `--no-parse-js` | Minado de endpoints (`/api/...`, `/admin/...`) en archivos `.js` | activado |
| `--include-subdomains` | Seguir enlaces a subdominios del mismo dominio | desactivado |
| `--ignore-robots` | No respetar `robots.txt` | desactivado |
| `-f`, `--format` | Formato del reporte (`txt`, `json`, `csv`, `html`) | `txt` |
| `-o`, `--output` | Archivo de salida | stdout |
| `-v`, `--verbose` | Aumenta el detalle de logs (`-v`, `-vv`) | - |
| `-q`, `--quiet` | No mostrar progreso por stderr | - |

### Ejemplos

```bash
# Reporte HTML de las primeras 200 paginas
uv run route-mapper https://example.com -m 200 -f html -o reporte.html

# Rastreo rapido con 4 peticiones en paralelo y sin robots.txt
uv run route-mapper https://example.com -c 4 --ignore-robots

# A traves de Burp, rotando User-Agent y con jitter para difuminar el patron
uv run route-mapper https://example.com \
  --proxy http://127.0.0.1:8080 --ua-file agents.txt --jitter 0.5

# Sembrar desde sitemap.xml y minar endpoints de los .js descubiertos
uv run route-mapper https://example.com --sitemap --parse-js

# Escaneo autenticado enviando cabeceras personalizadas
uv run route-mapper https://example.com \
  -H "Authorization: Bearer eyJhbGci..." \
  -H "Cookie: session=abc123"

# Login por formulario: el crawler obtiene la cookie de sesión y la reutiliza
uv run route-mapper https://example.com \
  --login-url https://example.com/login \
  --login-user admin --login-pass 's3cret'

# Login JSON/API: extrae el token del cuerpo de la respuesta
uv run route-mapper https://example.com \
  --login-url https://example.com/api/auth \
  --login-user admin --login-pass 's3cret' \
  --auth-type json --token-key access_token
```

### Codigos de salida

- `0`: rastreo completado sin enlaces rotos.
- `1`: rastreo completado con enlaces rotos, o error en tiempo de ejecucion.
- `2`: error de uso (argumentos o URL inicial invalida).

## Uso como libreria

```python
from route_mapper import CrawlConfig, Crawler

config = CrawlConfig(start_url="https://example.com", max_pages=100)
result = Crawler(config).run()
print(result.summary())
```

## Desarrollo

```bash
uv run pytest        # ejecutar los tests
uv run ruff check .  # linting
uv run mypy src      # comprobacion de tipos
```

## Estructura del proyecto

```
src/route_mapper/
  config.py          Configuracion validada del crawler
  http_client.py     Capa HTTP con reintentos
  robots.py          Lectura y cache de robots.txt
  parser.py          Extraccion de enlaces del HTML
  url_utils.py       Normalizacion y filtrado de URLs
  crawler.py         Orquestacion del rastreo
  models.py          Modelos de dominio inmutables
  reporters.py       Formatos de salida (txt, json, csv)
  html_report.py     Reporter HTML
  logging_setup.py   Configuracion de logs
  cli.py             Punto de entrada de linea de comandos
tests/               Suite de pruebas con pytest
```
