from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    tenant_id: str
    client_id: str
    client_secret: str
    site_url: str  # https://tenant.sharepoint.com/sites/sitename
    default_list_name: str = ""
    default_drive_name: str = "Documents"


settings = Settings()
