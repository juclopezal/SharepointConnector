from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _read_version() -> str:
    version_file = Path(__file__).parent.parent.parent / "VERSION"
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        return "0.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Azure AD / Microsoft Identity Platform
    tenant_id: str
    client_id: str
    client_secret: str

    # Application
    app_name: str = "SharePoint Connector"
    app_version: str = _read_version()
    log_level: str = "INFO"

    # Logging to file (in addition to console). Empty log_dir disables the file log.
    # Set via LOG_DIR env var / .env when persistent file logs are needed.
    log_dir: str = ""
    log_file: str = "api_server_sp_connector.log"


settings = Settings()
