from contextvars import ContextVar

# Set per-request in the logging middleware; consumed by services for structured logs.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
client_app_id_var: ContextVar[str] = ContextVar("client_app_id", default="unknown")
