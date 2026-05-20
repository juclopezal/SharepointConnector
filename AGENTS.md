# Guía de Desarrollo para Agentes AI (AGENTS.md)

Este documento contiene las reglas inviolables y la metodología de trabajo que cualquier Agente de Inteligencia Artificial (especialmente Antigravity) debe seguir al momento de analizar, planificar o codificar dentro de este repositorio (`NewsLetterJiraSM`).

## 1. Patrones Arquitectónicos y Estilo
- **OOP y Segmentación Estricta:** El código sigue un paradigma de Programación Orientada a Objetos. La lógica debe estar encapsulada en Clases. Aplica firmemente la regla de "Una Clase Por Archivo" y los nombres de los archivos deben coincidir con la convención de la clase o su propósito.
- **Arquitectura de 3 Capas (3-Tier):** Mantener una separación absoluta entre:
  1. `Routers / Controllers` (Puntos de entrada de la API).
  2. `Services / Logic` (Lógica de negocio pura, orquestación).
  3. `Repositories / Data` (Acceso a Bases de Datos SQLite, APIs externas como Jira).
- **Inyección de Dependencias (DI):** Nunca instanciar clases fuertemente acopladas dentro de los métodos. Las dependencias se inicializan en capas superiores (como `api_main.py` o los constructores) pasándolas mediante variables o el archivo `dependencies.py`.
- **Flujo y Estructuras (Guard Clauses):** Programación lineal y predecible. Regla estricta: devolver el control (`return` temprano) al inicio de las funciones si hay condiciones negativas o estados inválidos. Evitar el anidamiento profundo de condicionales.

## 2. Metodología de Implementación (SDD - Spec Driven Development)
- **Las Especificaciones como fuente de verdad:** Antes de realizar modificaciones de alto impacto o insertar nuevas funcionalidades (Features), el Agente siempre debe solicitar o proponer la redacción de un documento `requirements/SPEC-XXX_Nombre.md`.
- **Evolución sin Destrucción:** Si una SPEC requiere enmiendas después de implementada, no reescribir la historia original. En su lugar, anexar bloques indicando los nuevos comportamientos bajo la pestaña de `⚠️ PENDIENTE DE IMPLEMENTACIÓN` al final del documento.
- **Flujo finalizado:** Al concluir la implementación de una `SPEC`:
  1. Actualizar sistemáticamente la variable `APP_VERSION` en el archivo `api/version.py`.
  2. Redactar una entrada detallada pero concisa del trabajo realizado en `doc/change.log` agrupado bajo la versión nueva.
  3. Cambiar el estado de la SPEC a `✅ REVISIÓN IMPLEMENTADA`.

## 3. Logs y Observabilidad
- **El Tracker de Versión:** Cada línea de log que reporte el sistema (Backend) debe llevar obligatoriamente prefijada la constante global de versión de la aplicación importada desde `version.py`.
- **Formato Estricto:** `[{APP_VERSION}] [{LEVEL}] [{ClassName}] - Mensaje informativo o error`.
- **Registro Contextualizado:** Los logs de tipo error o warning generados en integraciones (Webhooks de SharePoint, llamadas a Jira, procesos de AI) no deben interrumpir la lógica paralela. Deben atrapar la excepción y registrarse debidamente para el consumo del desarrollador.

## 4. Gestión de Componentes Frontend (Angular)
- Conservar los diseños limpios y modernos. Utilizar CSS Grid y Flexbox.
- Consumir todas las APIs del backend de manera tipada definiendo una interfaz estricta en `frontend/src/app/models/api.models.ts`.
- Preservar la seguridad utilizando los Guards (ej. `adminGuard`) para módulos sensibles y asegurar que Angular acceda a variables de configuración dinámicas por HTTP si es necesario, sin hardcodear credenciales ni valores cambiantes en los HTML de la vista.

## 5. Control de Entorno y Servidor
- **Entornos Aislados:** Todo código Python nuevo que requiera librerías o dependencias debe declararse rigurosamente en `requirements.txt`. El agente nunca debe tratar de instalar paquetes en el sistema global del host, asumiendo su ejecución bajo un entorno virtual (`venv`) preparado o Docker.
