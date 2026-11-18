"""Runtime configuration from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _env_list(name: str, default: str) -> list[str]:
    return [s.strip() for s in os.environ.get(name, default).split(",") if s.strip()]


@dataclass(frozen=True)
class Settings:
    storage_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TPA_STORAGE_DIR", REPO_ROOT / "storage"))
    )
    database_url: str = field(default_factory=lambda: os.environ.get("TPA_DATABASE_URL", ""))
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.environ.get("TPA_MAX_UPLOAD_MB", "4096")) * 1024 * 1024
    )
    cors_origins: list[str] = field(
        default_factory=lambda: _env_list(
            "TPA_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        )
    )
    api_host: str = field(default_factory=lambda: os.environ.get("TPA_API_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: int(os.environ.get("TPA_API_PORT", "8000")))
    pose_stride: int = field(default_factory=lambda: int(os.environ.get("TPA_POSE_STRIDE", "1")))
    ai_adapter: str = field(
        default_factory=lambda: os.environ.get("TPA_AI_ADAPTER", "claude_code"),
        metadata={"doc": "AIClient adapter: claude_code (default) or anthropic_api (future)"},
    )
    ai_language: str = field(default_factory=lambda: os.environ.get("TPA_AI_LANGUAGE", "it"))

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.storage_dir / 'tpa.sqlite3'}"

    @property
    def videos_dir(self) -> Path:
        return self.storage_dir / "videos"

    @property
    def analyses_dir(self) -> Path:
        return self.storage_dir / "analyses"

    @property
    def ai_dir(self) -> Path:
        """Evidence Packs: storage/ai/<analysis_id>/<pack_id>/."""
        return self.storage_dir / "ai"


def get_settings() -> Settings:
    return Settings()
