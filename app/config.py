from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    line_channel_secret: str = ""
    line_channel_access_token: str = ""

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""
    google_sheets_template_id: str = ""

    google_application_credentials: str = ""
    firestore_project_id: str = ""

    encryption_key: str = ""

    admin_line_user_id: str = ""

    tick_shared_secret: str = ""

    port: int = 8000
    tz: str = "Asia/Taipei"


@lru_cache
def get_settings() -> Settings:
    return Settings()
