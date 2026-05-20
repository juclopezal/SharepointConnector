# SharePoint Connector

Microservicio REST que reemplaza Power Automate para operaciones sobre SharePoint vía Microsoft Graph API.

## Endpoints

### `POST /upload`
Sube un archivo a una biblioteca de documentos.

```json
{
  "folder": "DailyDelivery/Old",
  "filename": "LATSUP-5734.json",
  "data": "<base64>",
  "drive_name": "Documents"   // opcional, usa DEFAULT_DRIVE_NAME si se omite
}
```

El campo `token` se acepta pero se ignora (compatibilidad con callers que antes apuntaban a Power Automate).

### `POST /list`
Crea un ítem en una lista de SharePoint. Acepta cualquier combinación de campos y tipos.

```json
{
  "list_name": "MiLista",     // opcional, usa DEFAULT_LIST_NAME si se omite
  "fields": {
    "Title": "LATSUP-6585",
    "organization": "Acme",
    "score": 9.5,
    "resolved": true,
    "tags": ["soporte", "crítico"]
  }
}
```

Los nombres de campo deben ser los **nombres internos** de las columnas en SharePoint (no el display name).

### `GET /health`
Devuelve `{"status": "ok"}`.

---

## Configuración

Copia `.env.example` a `.env` y rellena los valores:

| Variable | Descripción |
|---|---|
| `TENANT_ID` | ID del tenant Azure AD |
| `CLIENT_ID` | ID del App Registration |
| `CLIENT_SECRET` | Secreto del App Registration |
| `SITE_URL` | URL del site SharePoint con acceso `Sites.Selected` |
| `DEFAULT_LIST_NAME` | Lista por defecto si el caller no la especifica |
| `DEFAULT_DRIVE_NAME` | Biblioteca de documentos por defecto (`Documents`) |
| `SP_PORT` | Puerto expuesto en el host (default: `8001`) |

## Arranque

```bash
cp .env.example .env
# editar .env con los valores reales
docker compose up -d
```

## Notas sobre `Sites.Selected`

El App Registration debe tener concedido acceso al site específico configurado en `SITE_URL`.  
Un administrador de SharePoint debe ejecutar esto una vez:

```powershell
# PowerShell con módulo PnP.PowerShell
Grant-PnPAzureADAppSitePermission `
  -AppId "<CLIENT_ID>" `
  -DisplayName "SharePoint Connector" `
  -Site "<SITE_URL>" `
  -Permissions Write
```

Si el acceso no está concedido, Graph devolverá `403` con mensaje claro.

## Límite de tamaño de archivos

La subida simple (`PUT .../content`) soporta hasta **4 MB**.  
Para archivos mayores es necesario usar upload sessions (no implementado en v1).
```
