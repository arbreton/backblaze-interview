"""Runtime configuration, loaded from environment variables / .env."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Backblaze B2 (S3-compatible)
    b2_key_id: str = ""
    b2_application_key: str = ""
    b2_bucket: str = ""
    b2_endpoint: str = ""
    b2_prefix: str = "uploads/"

    # Claude
    anthropic_api_key: str = ""
    claude_key_file: str = "~/Desktop/claudekey.txt"
    claude_model: str = "claude-opus-5-5"

    # Service
    max_upload_mb: int = 50
    local_path_root: str = "~/Desktop"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def b2_endpoint_url(self) -> str:
        ep = self.b2_endpoint.strip()
        if ep and not ep.startswith("http"):
            ep = "https://" + ep
        return ep

    @property
    def b2_region(self) -> str:
        # s3.us-west-004.backblazeb2.com -> us-west-004
        host = self.b2_endpoint_url.removeprefix("https://").removeprefix("http://")
        parts = host.split(".")
        return parts[1] if len(parts) > 2 and parts[0] == "s3" else "us-east-1"

    def b2_missing(self) -> list[str]:
        fields = {
            "B2_KEY_ID": self.b2_key_id,
            "B2_APPLICATION_KEY": self.b2_application_key,
            "B2_BUCKET": self.b2_bucket,
            "B2_ENDPOINT": self.b2_endpoint,
        }
        return [k for k, v in fields.items() if not v.strip()]

    def resolve_anthropic_key(self) -> str:
        """Env var wins; otherwise read the key file. Never logged."""
        if self.anthropic_api_key.strip():
            return self.anthropic_api_key.strip()
        path = Path(os.path.expanduser(self.claude_key_file))
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
        return ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
