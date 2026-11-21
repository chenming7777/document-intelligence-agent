from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    provider: Literal["demo", "gemini"] = Field("demo", alias="DOCINTEL_PROVIDER")
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.8-flash"
    data_dir: Path = Field(Path("data"), alias="DOCINTEL_DATA_DIR")
    max_upload_mb: int = Field(10, ge=1, le=50)
    max_pages: int = Field(10, ge=1, le=50)
    max_text_chars: int = Field(60000, ge=100, le=200000)
    max_image_pixels: int = Field(20000000, ge=1000, le=40000000)
    max_rechecks: int = Field(1, ge=0, le=1)
    ocr_language: str = "eng"
    tesseract_cmd: str = ""
    ocr_timeout_seconds: int = Field(30, ge=1, le=120)
    llm_timeout_seconds: int = Field(60, ge=1, le=180)
    max_concurrent_documents: int = Field(2, ge=1, le=8)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024
