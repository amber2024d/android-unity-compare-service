from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8080
    public_base_url: str = "http://localhost:8080"
    data_dir: Path = Path("./data")
    work_dir: Path = Path("./work")
    db_path: Path | None = None

    auth_enabled: bool = False
    auth_api_key_enabled: bool = True
    api_keys: str | None = None
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_auth_base: str = "https://accounts.feishu.cn"
    feishu_api_base: str = "https://open.feishu.cn"
    session_ttl_hours: float = 24.0
    http_timeout_seconds: float = 30.0

    aps_base_url: str = ""
    aps_api_key: str | None = None
    aps_download_timeout_seconds: float = 21600.0
    aps_job_poll_seconds: float = 10.0

    task_concurrency: int = Field(default=2, ge=1)
    download_concurrency: int = Field(default=4, ge=1)
    dump_concurrency: int = Field(default=2, ge=1)
    compare_concurrency: int = Field(default=2, ge=1)
    il2cpp_dumper_path: Path | None = None
    il2cpp_dumper_timeout_seconds: float = 3600.0
    dll_analyzer_path: Path | None = None
    dll_analyzer_timeout_seconds: float = 300.0

    keep_failed_work_dir: bool = False
    work_dir_ttl_hours: float = 24.0
    worker_poll_seconds: float = 2.0

    report_storage_backend: str = "local"
    report_signed_url_ttl_seconds: int = 3600
    report_storage_prefix: str = "unity-compare-reports"
    report_gcs_bucket: str | None = None
    report_gcs_credentials_json: str | None = None
    report_s3_bucket: str | None = None
    report_s3_region: str | None = None
    report_s3_endpoint_url: str | None = None
    report_s3_access_key_id: str | None = None
    report_s3_secret_access_key: str | None = None

    @field_validator(
        "api_keys",
        "aps_api_key",
        "feishu_app_id",
        "feishu_app_secret",
        "il2cpp_dumper_path",
        "dll_analyzer_path",
        "report_gcs_bucket",
        "report_gcs_credentials_json",
        "report_s3_bucket",
        "report_s3_region",
        "report_s3_endpoint_url",
        "report_s3_access_key_id",
        "report_s3_secret_access_key",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def task_db_path(self) -> Path:
        return self.db_path or self.data_dir / "tasks.sqlite"

    @property
    def auth_db_path(self) -> Path:
        return self.data_dir / "auth.sqlite"

    @property
    def accepted_api_keys(self) -> set[str]:
        return {key.strip() for key in (self.api_keys or "").split(",") if key.strip()}

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.task_db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
