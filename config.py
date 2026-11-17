"""
Curriculum Designer configuration.

All settings are read from environment variables (or a .env file).
Copy .env.example -> .env to get started.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Service ───────────────────────────────────────────────────────────────
    service_port: int = 8003
    service_name: str = "curriculum-designer"
    version: str = "0.1.0"

    # ── Azure OpenAI (Stage B) ────────────────────────────────────────────────
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment_name: str = "gpt-4o"   # env var: AZURE_OPENAI_DEPLOYMENT_NAME
    azure_openai_api_version: str = "2024-08-01-preview"

    # ── LLM backend ──────────────────────────────────────────────────────────
    # "azure" = real Azure OpenAI calls; "mock" = stub data for dev/test
    llm_backend: str = "azure"

    # ── Microsoft sign-in (see auth.py) ──────────────────────────────────────
    # The instructor UI's Microsoft app (client) ID. Empty = no check, so local
    # dev and the tests need no sign-in.
    auth_ms_client_id: str = ""
    # The Microsoft directory sign-ins must come from. Default: Cal Poly.
    auth_ms_tenant_id: str = "1b0d02db-fc9e-4495-9537-1d379cca2ae7"
    auth_allowed_email_domain: str = "calpoly.edu"

    # ── Storage ───────────────────────────────────────────────────────────────
    # "memory" = dict, lost on restart.  "mongo" = MongoDB (see docs/data-model.md).
    storage_backend: str = "memory"

    # Connection string from MongoDB Atlas (Connect → Drivers → Python).
    # Required only when storage_backend == "mongo".
    mongodb_uri: str = ""
    mongodb_db: str = "aied"


settings = Settings()
