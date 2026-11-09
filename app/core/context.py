from contextvars import ContextVar

# Set per-request in the logging middleware; consumed by services for structured logs.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
client_app_id_var: ContextVar[str] = ContextVar("client_app_id", default="unknown")
# Identidad resuelta por la autenticación por clave de API (SPEC-005). "-" cuando
# la petición no se autenticó (p. ej. rechazada con 401 o auth deshabilitada).
principal_var: ContextVar[str] = ContextVar("principal", default="-")
