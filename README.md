# SharePoint Connector

Microservicio REST que reemplaza Power Automate para operaciones sobre SharePoint vía Microsoft Graph API. Expone una API genérica y versionada que opera sobre cualquier site, lista y biblioteca de documentos de forma dinámica.

**Versión actual:** 2.0.0

---

## Endpoints

Todos los endpoints están bajo `/v1/graph`. Ver [ARQUITECTURA.md](ARQUITECTURA.md) para la referencia completa.

### Discovery

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/graph/sites` | Lista los sites accesibles por la aplicación |
| `GET` | `/v1/graph/sites/{site_id}/lists` | Lista las listas de un site |
| `GET` | `/v1/graph/sites/{site_id}/drives` | Lista las bibliotecas de documentos de un site |
| `GET` | `/v1/graph/sites/{site_id}/drives/{drive_id}/items` | Navega carpetas y archivos de un drive |

### List Items

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/v1/graph/sites/{site_id}/lists/{list_id}/items` | Lee ítems de una lista |
| `POST` | `/v1/graph/sites/{site_id}/lists/{list_id}/items` | Inserta un ítem en una lista |

### Files

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/v1/graph/sites/{site_id}/drives/{drive_id}/files` | Sube un archivo (`multipart/form-data`) |
| `GET` | `/v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}` | Metadatos de un archivo o carpeta |
| `GET` | `/v1/graph/sites/{site_id}/drives/{drive_id}/items/{item_id}/download` | Descarga el contenido de un archivo |

### Health

```
GET /health  →  { "status": "ok", "service": "SharePoint Connector", "version": "2.0.0" }
```

---

## Flujo de uso típico

```
1. GET /v1/graph/sites?search=soporte         → obtener site_id
2. GET /v1/graph/sites/{site_id}/lists        → obtener list_id
3. GET /v1/graph/sites/{site_id}/drives       → obtener drive_id
4. POST .../lists/{list_id}/items             → crear ítem
5. POST .../drives/{drive_id}/files?folder=X  → subir archivo
```

Los IDs de site, lista y drive son estables; solo es necesario hacer discovery una vez.

---

## Configuración

Copia `devops/.env.example` a `devops/.env` y rellena los valores:

| Variable | Requerida | Descripción |
|---|---|---|
| `TENANT_ID` | Sí | ID del tenant Azure AD |
| `CLIENT_ID` | Sí | ID del App Registration |
| `CLIENT_SECRET` | Sí | Secreto del App Registration |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `INFO`) |
| `SP_PORT` | No | Puerto expuesto en el host (default: `8003`) |

### Permisos Azure AD requeridos

El App Registration necesita los permisos de aplicación:
- `Sites.Read.All` — para discovery y lectura
- `Sites.ReadWrite.All` — para escritura (crear ítems, subir archivos)

---

## Arranque

```bash
cp devops/.env.example devops/.env
# Editar devops/.env con los valores reales
docker compose -f devops/docker-compose.yml up -d --build
```

La documentación interactiva (Swagger UI) queda disponible en `http://localhost:8003/docs` (puerto configurable con `SP_PORT`).

---

## Subida de archivos

El endpoint de subida recibe `multipart/form-data`:

```bash
curl -X POST "http://localhost:8003/v1/graph/sites/{site_id}/drives/{drive_id}/files?folder=DailyDelivery" \
  -H "X-App-ID: mi-app" \
  -F "file=@informe.json"
```

La carpeta se crea automáticamente si no existe. El tamaño máximo es **4 MB** (límite de `PUT .../content` en Graph API).

---

## Creación de ítem en lista

```bash
curl -X POST "http://localhost:8003/v1/graph/sites/{site_id}/lists/{list_id}/items" \
  -H "Content-Type: application/json" \
  -H "X-App-ID: mi-app" \
  -d '{
    "fields": {
      "Title": "LATSUP-6585",
      "organization": "Acme Corp",
      "score": 9.5,
      "resolved": true
    }
  }'
```

Los nombres de campo deben ser los **nombres internos** de las columnas en SharePoint.

---

## Cabeceras HTTP

| Cabecera | Dirección | Descripción |
|---|---|---|
| `X-App-ID` | Request | Identificador del caller — se registra en todos los logs |
| `X-Request-ID` | Response | UUID de trazabilidad generado por el middleware |

---

Ver [ARQUITECTURA.md](ARQUITECTURA.md) para la referencia completa de la API, diagramas de arquitectura y detalles de despliegue.
