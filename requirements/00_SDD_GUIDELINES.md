# Spec Driven Development (SDD) - Guías del Proyecto

En este proyecto utilizamos la variante **Spec-Anchored** de Spec Driven Development (SDD). 

## La Filosofía Spec-Anchored
El enfoque *Spec-Anchored* establece que **el documento de especificación (Spec) es la fuente única y definitiva de la verdad**. Antes de que se escriba o modifique una sola línea de código, el ciclo de desarrollo dicta que el problema, la solución propuesta y los criterios para darlo por finalizado deben estar definidos en este repositorio de documentos. El código es estrictamente una consecuencia orientada a cumplir con la especificación.

## Estructura de las Especificaciones (Specs)
Todas las especificaciones vivirán dentro del directorio `requirements/` y utilizarán el formato Markdown con la convención de nomenclatura `SPEC-<numero>_<NombreDescriptivo>.md`.

Cada documento *Spec* debe estar estructurado en 4 secciones funcionales:
1. **Contexto (El Problema)**: Qué sucede actualmente y por qué es necesario el cambio o la adopción de la nueva característica.
2. **Propuesta (La Solución)**: Descripción funcional abordando la manera en que el sistema deberá resolver o suplir la necesidad detallada en el contexto. No es estrictamente técnico, se enfoca en el *Qué* y no necesariamante hiperdetallado en el *Cómo*.
3. **Criterios de Aceptación**: Una lista acotada de hitos verificables. Si todos se cumplen, la especificación está implementada exitosamente.
4. **Bitácora de IA / Historial de Implementación**: 🌟 *Este es el paso vital para el futuro de este repositorio*. Cada agente lógico/IA o desarrollador humano que trabaje resolviendo el requerimiento, debe **añadir al final del archivo** un resumen de su implementación (archivos modificados, decisiones clave de diseño, etc). Cuando otra IA deba analizar el código nuevamente y parta desde los requerimientos, podrá leer rápidamente la bitácora y sabrá exactamente en qué estado está y qué componentes conforman la funcionalidad sin tener que inferirlo leyendo todo el código fuente desde cero.

---
## Instrucciones para Agentes (LLMs)
Cuando se te asigne la implementación de un requerimiento que sigue este patrón:
1. Localiza y lee exhaustivamente el `SPEC` antes de proponerte a codificar.
2. Satisface e implementa todos los *Criterios de Aceptación*.
3. Al terminar tu implementación, **debes hacer un append (o sobrescritura agregando el final)** en el archivo `.md` de la Spec rellenando la sección de **Bitácora de IA** (O creando la entrada con título `# Historial de Implementación`) con lo siguiente:
   - Fecha y Rol (ej: `2026-XX-YY - Agente LLM`).
   - Un resumen condensado de los archivos de código fuente que modificaste y para qué.
   - Si existieron desviaciones del diseño original y por qué.
