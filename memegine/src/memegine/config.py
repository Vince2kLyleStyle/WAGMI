from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_prefix="MEMEGINE_",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")

    ideation_model: str = "claude-opus-4-7"
    utility_model: str = "claude-sonnet-4-6"
    vibe_model: str = "claude-haiku-4-5"

    data_dir: Path = REPO_ROOT / "data"
    codex_path: Path = REPO_ROOT / "data" / "codex" / "style.md"
    references_dir: Path = REPO_ROOT / "data" / "references"
    outputs_dir: Path = REPO_ROOT / "data" / "outputs"
    logs_dir: Path = REPO_ROOT / "data" / "logs"

    # Raid system
    raid_group_id: int | None = Field(default=None, validation_alias="MEMEGINE_RAID_GROUP_ID")
    raid_app_url: str = "http://localhost:3000"
    win_like_threshold: int = 1000
    win_rt_threshold: int = 500
    banned_words_str: str = Field(default="", validation_alias="MEMEGINE_BANNED_WORDS")

    @property
    def banned_words(self) -> list[str]:
        return [w.strip().lower() for w in self.banned_words_str.split(",") if w.strip()]

    def ensure_dirs(self) -> None:
        for p in [self.data_dir, self.codex_path.parent, self.references_dir, self.outputs_dir, self.logs_dir]:
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
