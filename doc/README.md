# Proyecto Jirito NewsLetter

Jirito NewsLetter es una plataforma Full-Stack de automatización para el análisis de tickets de Jira Service Management utilizando IA Generativa (Google Gemini) y sincronización con SharePoint.

## Arquitectura
- **Backend**: FastAPI (Python) con orquestación interna de tareas.
- **Frontend**: Angular 18 con interfaz de monitoreo en tiempo real.
- **Integración**: Jira JQL API y SharePoint Webhooks.

## Requisitos Previos
- Python 3.9+
- Node.js & Angular CLI (para desarrollo del frontend)
- Cuenta Atlassian (Jira) y Clave API de Google AI.

## Instalación y Configuración

1. **Entorno Virtual**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configuración**:
   Asegúrate de editar `config.json` con las URLs correctas de tu instancia de Jira y el Webhook de SharePoint.

3. **Variables de Entorno**:
   El sistema requiere las siguientes variables:
   - `JIRA_API_TOKEN`: Token de acceso a Atlassian.
   - `GOOGLE_AI_API_KEY`: Clave para el motor de IA (Gemini).

## Ejecución

Para iniciar el servidor completo (API + Frontend):

```bash
./start.sh
```

El servidor estará disponible en [http://localhost:8000](http://localhost:8000).

## Documentación Detallada
Para más detalles sobre el funcionamiento y la arquitectura, consulte el directorio `doc/`:
- [Guía Funcional](doc/docFuncional.md)
- [Arquitectura de Integración](doc/arquitecturaintegracion.md)
