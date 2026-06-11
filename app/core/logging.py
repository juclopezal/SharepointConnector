import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_EXTRA_FIELDS = frozenset({
    "request_id",
    "client_app_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "site_id",
    "list_id",
    "drive_id",
    "item_id",
    "file_name",
    "graph_url",
    "graph_status",
})


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                log_obj[field] = getattr(record, field)
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    log_dir: str = "",
    log_file: str = "api_server_sp_connector.log",
) -> None:
    formatter = JSONFormatter()
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    # Console output (stdout/stderr) — picked up by Docker, k8s, etc.
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Persistent file output for traceability. Rotates at 10 MB, keeps 5 backups.
    if log_dir:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path / log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
