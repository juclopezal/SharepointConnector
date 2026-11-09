## Context

El conector no autentica a sus clientes: `X-App-ID` es informativa y opcional, y
el servicio opera con permisos de aplicación amplios sobre el tenant. Esta change
añade una capa de autenticación por clave (`X-API-Key`) delante de los endpoints
y un registro de auditoría que liga cada petición a una identidad (`principal`).
El estado es un proceso único sin base de datos salvo cachés en memoria; esta
change introduce el primer almacén persistente del servicio (SQLite).

Referencia funcional previa: `requirements/SPEC-005_API_Key_Auth_And_Audit.md`.

## Goals / Non-Goals

**Goals:**
- Verificar una credencial por llamante en cada petición y resolver su
  `principal`.
- Persistir las claves habilitadas fuera del código, hasheadas, con alta/baja sin
  redespliegue.
- Trazar en el log estructurado quién llamó, a qué y con qué código, incluidos
  los `401`.
- No introducir dependencias de terceros: stdlib (`sqlite3`, `hmac`, `hashlib`,
  `secrets`) + lo ya presente (FastAPI, pydantic-settings).

**Non-Goals:**
- Identidad verificada de persona vía Entra ID / OAuth2 (`Bearer`): fase
  posterior, fuera de esta change.
- Autorización por roles o scopes (qué puede hacer cada `principal`): aquí solo
  autenticación (quién es) y auditoría.
- Rotación automática de claves, expiración por tiempo o rate-limiting.
- Multi-instancia del conector compartiendo claves (SQLite asume instancia única;
  si llega, se migra a Postgres).

## Decisions

### Credencial: clave en `X-API-Key`, no en el body
Un header aplica por igual a GET y POST, no se mezcla con los datos que se
auditan y se declara con `APIKeyHeader` (visible en OpenAPI). Se descarta la
clave en el body (propuesta inicial del usuario) porque los GET no tienen body.

### Almacén: SQLite, un archivo en volumen persistente
Tabla `api_keys(principal TEXT, key_hash TEXT, active INTEGER, created_at TEXT,
last_used TEXT)`. Se elige SQLite frente a `.env` (permite alta/baja sin
redesplegar y guarda metadatos: identidad, fechas, último uso) y frente a un
motor de BD (innecesario: escrituras rarísimas, una sola instancia, sin red).
El archivo vive en un volumen Docker, nunca dentro de la imagen ni en git, con
permisos restringidos. Acceso vía `sqlite3` de la stdlib; lecturas cacheables en
memoria con invalidación simple si más adelante hace falta.

### Hash: HMAC-SHA256 con secreto de servidor
Se guarda `HMAC-SHA256(clave, API_KEY_HMAC_SECRET)`, no un SHA-256 a secas: un
SHA-256 sin sal de una clave corta es buscable en tablas públicas (demostrado con
`jdiaz`). El HMAC con secreto hace inútil cualquier tabla precomputada aunque se
filtre el archivo. Comparación con `secrets.compare_digest` (tiempo constante)
para no filtrar información por tiempos.

### Validación: dependencia FastAPI inyectable
Una dependencia `Depends/Security` compartida resuelve el `principal` o lanza
`401`. Se registra como singleton en `app/core/dependencies.py` (patrón DI del
proyecto) para poder sustituirla en tests con `app.dependency_overrides`. El
`principal` se expone por `ContextVar` (`app/core/context.py`) junto a
`request_id`/`client_app_id`, de modo que el middleware y los servicios puedan
registrarlo sin pasarlo por parámetros.

### Auditoría: extender el log existente, no crear uno nuevo
Se añade `principal` a la whitelist `_EXTRA_FIELDS` de `app/core/logging.py` y al
log de "petición completada" del middleware de `app/main.py`. Los `401` se
registran igual. Por defecto NO se separa el log de auditoría en otro
destino/retención (decisión abierta menor en la SPEC): basta el campo `principal`
en el log JSON actual, correlacionable por `request_id`.

### Gestión de claves: alta por script, resto por SQL
La gestión del almacén NO se expone en la API; se opera por consola/SSH.
- **Alta:** un mini-script Python fuera del camino de las peticiones genera
  `secrets.token_urlsafe(32)` y calcula su HMAC. Imprime la clave en claro (una
  sola vez, para entregarla por canal seguro) y el HMAC listo para insertar. La
  clave en claro no se persiste. Este paso debe ser código porque ni la
  generación aleatoria ni el HMAC se pueden hacer con SQL.
- **Revocar / reactivar / borrar / consultar:** sentencias SQL directas sobre
  filas existentes (`UPDATE api_keys SET active=0 WHERE principal=...`, etc.). No
  requieren script.
- **Rotación de la clave de un titular:** no se edita el hash — se revoca la fila
  vieja y se emite una nueva clave (alta).

### Configuración
`Settings` (`app/core/config.py`) gana `api_key_hmac_secret` y la ruta del
archivo SQLite (p. ej. `api_key_db_path`), documentados en `devops/.env.example`.
`docker-compose.yml` monta el volumen del archivo.

### Alcance de la protección
Los endpoints funcionales `/v1/...` quedan protegidos. `/health` permanece
**público** (lo usan los health checks del contenedor). Se confirmará la lista
exacta al implementar.

## Risks / Trade-offs

- **Cambio incompatible (BREAKING):** los clientes actuales deben empezar a
  enviar `X-API-Key`. Mitigación: ya se avisó a los usuarios de incluir la
  cabecera; coordinar la activación con el reparto de claves.
- **Pérdida del secreto HMAC:** si cambia `API_KEY_HMAC_SECRET`, todos los
  `key_hash` dejan de validar y hay que reemitir las claves. Mitigación: tratar
  el secreto como credencial estable y respaldada.
- **SQLite = instancia única:** varias instancias del conector no comparten el
  archivo de forma segura para escritura. Aceptado: hoy es proceso único; migrar
  a Postgres si escala.
- **Persistencia del archivo:** si el archivo no está en un volumen, se pierde al
  recrear el contenedor. Mitigación: volumen obligatorio, cubierto en los
  criterios.
- **La clave identifica al llamante, no a la persona:** quien tenga la clave puede
  usarla desde cualquier cliente. Aceptado para esta fase; Entra ID lo resuelve
  después.
