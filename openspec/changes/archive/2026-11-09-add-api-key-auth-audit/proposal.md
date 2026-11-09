## Why

El conector opera con permisos de aplicación amplios sobre todo el tenant
(`Sites.ReadWrite.All`) pero **no valida quién le llama**: la cabecera `X-App-ID`
es informativa y ni siquiera obligatoria. No hay forma fiable de saber —ni de
auditar después— qué llamante ejecutó cada operación. Se necesita una credencial
por llamante que el servicio verifique y un registro de auditoría que ligue cada
petición a una identidad y a su código de respuesta.

## What Changes

- Toda petición a los endpoints protegidos debe incluir una clave en la cabecera
  **`X-API-Key`**. Ausente o inválida → **`401`** (la petición no llega a Graph).
  **BREAKING**: los clientes actuales deben empezar a enviar la cabecera.
- Se introduce un **almacén SQLite** de claves habilitadas. Cada fila asocia un
  `principal` (identidad que se audita, p. ej. `jdiaz`) con el **HMAC-SHA256** de
  su clave (nunca la clave en claro), más `active`, `created_at` y `last_used`.
- La validación es una **dependencia FastAPI** común: lee la cabecera, calcula el
  HMAC, busca una fila `active=1` y compara en tiempo constante. Resuelve el
  `principal` para el resto de la petición.
- El **registro de auditoría** añade el campo `principal` al log estructurado,
  junto al `status_code` y la `path` que ya se registran, de modo que cada
  petición —aceptada o rechazada con `401`— queda trazada por identidad. La clave
  nunca se registra.
- **Utilidad de administración** fuera del camino de las peticiones para emitir
  una clave aleatoria, calcular su HMAC y dar de alta/baja titulares sin
  redesplegar el servicio.
- Nueva configuración en `Settings`: secreto HMAC y ruta del archivo SQLite,
  documentados en `devops/.env.example`; el archivo vive en un volumen
  persistente contemplado por Docker.

## Capabilities

### New Capabilities
- `api-key-auth`: autenticación de los clientes del conector mediante clave en
  `X-API-Key`, validada contra el almacén de claves habilitadas; define los
  resultados observables (`401` sin clave / clave revocada / clave desconocida,
  paso normal con clave válida) y la resolución del `principal`.
- `request-audit-log`: registro de auditoría por petición que liga `principal`,
  método, ruta y `status_code`, incluyendo los intentos rechazados, sin exponer
  nunca la clave.

### Modified Capabilities
<!-- Sin cambios de requisitos en capacidades existentes: la autenticación es
     una capa nueva por delante de los endpoints, no modifica su contrato
     funcional. -->

## Impact

- **Código:** nueva dependencia de autenticación y almacén de claves en
  `app/core/` (p. ej. `auth`/`dependencies`), ampliación del middleware y la
  whitelist `_EXTRA_FIELDS` de `app/core/logging.py` con `principal`, propagación
  por `ContextVar` en `app/core/context.py`, y nuevos campos en `Settings`
  (`app/core/config.py`).
- **API:** todos los endpoints protegidos pasan a exigir `X-API-Key` (declarado
  vía `APIKeyHeader`, visible en OpenAPI). Nuevo resultado `401`.
- **Dependencias:** SQLite (stdlib `sqlite3`, sin paquete nuevo); HMAC vía
  `hmac`/`hashlib` de la stdlib.
- **Infra/Despliegue:** volumen persistente para el archivo SQLite en
  `docker-compose.yml`; nuevas variables de entorno (`API_KEY_HMAC_SECRET`, ruta
  del archivo) en `devops/.env.example`.
- **Operación:** proceso de emisión y entrega de claves por canal seguro; la
  base no se versiona en git.
- **Docs:** `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`, `CHANGELOG.md`,
  `VERSION` y la spec `requirements/SPEC-005_API_Key_Auth_And_Audit.md`.
