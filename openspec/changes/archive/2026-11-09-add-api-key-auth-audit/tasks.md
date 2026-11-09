## 1. Configuración y almacén

- [x] 1.1 Añadir a `Settings` (`app/core/config.py`) `api_key_hmac_secret` y la ruta del archivo SQLite (p. ej. `api_key_db_path`); documentarlos en `devops/.env.example`.
- [x] 1.2 Crear el módulo de almacén de claves (p. ej. `app/core/api_keys.py`) con acceso `sqlite3`: creación de la tabla `api_keys(principal, key_hash, active, created_at, last_used)` si no existe, y función de búsqueda por `key_hash` (solo `active=1`) que devuelve el `principal`.
- [x] 1.3 Implementar el cálculo HMAC-SHA256 (clave + secreto) y la comparación en tiempo constante (`secrets.compare_digest`); actualizar `last_used` al validar.

## 2. Autenticación

- [x] 2.1 Implementar la dependencia de autenticación (`APIKeyHeader` + `Depends/Security`) que lee `X-API-Key`, valida contra el almacén y resuelve el `principal`; `401` ante ausente / vacía / desconocida / revocada.
- [x] 2.2 Registrar la dependencia como singleton en `app/core/dependencies.py` (patrón DI, sustituible en tests).
- [x] 2.3 Aplicar la dependencia a los endpoints protegidos `/v1/...`; dejar `/health` público.

## 3. Auditoría

- [x] 3.1 Añadir `principal` a la whitelist `_EXTRA_FIELDS` (`app/core/logging.py`) y propagarlo por `ContextVar` en `app/core/context.py`.
- [x] 3.2 Incluir `principal` en el log de "petición completada" del middleware (`app/main.py`) y registrar también los intentos rechazados (`401`) con `path` y `status_code`, sin exponer la clave.

## 4. Administración de claves

- [x] 4.1 Crear el mini-script de **alta**: genera `secrets.token_urlsafe(32)`, calcula su HMAC e imprime la clave en claro (una sola vez) y el HMAC listo para insertar. No persiste la clave en claro.
- [x] 4.2 Documentar las sentencias SQL de **revocar / reactivar / borrar / consultar** (`UPDATE api_keys SET active=... WHERE principal=...`, `DELETE`, `SELECT`) como operación manual; la rotación es revocar + nueva alta.

## 5. Despliegue

- [x] 5.1 Montar el volumen persistente del archivo SQLite en `devops/docker-compose.yml` y asegurar que el archivo no se incluye en la imagen ni en git (`.gitignore`), con permisos restringidos.

## 6. Tests

- [x] 6.1 Tests `pytest` con `dependency_overrides`: `401` sin cabecera; `401` con clave revocada/desconocida; paso normal con clave válida y `principal` correcto en el log; verificación de que no se persiste ni registra la clave en claro.

## 7. Documentación y cierre

- [x] 7.1 Actualizar la Bitácora de IA de `requirements/SPEC-005_API_Key_Auth_And_Audit.md` (estado → Implementada) y mantener coherentes `README.md`, `ARQUITECTURA.md`, `arquitecturasUML.md`.
- [x] 7.2 Subir `VERSION` (SemVer) y añadir la entrada al inicio de `doc/CHANGELOG.md`; dejar la suite `pytest` en verde.
