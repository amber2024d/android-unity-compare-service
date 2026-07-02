from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskType(StrEnum):
    UNITY_CHECK = "unity_check"
    PAIR_COMPARE = "pair_compare"
    BATCH_COMPARE = "batch_compare"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VersionStatus(StrEnum):
    DOWNLOAD_PENDING = "download_pending"
    DOWNLOAD_RUNNING = "download_running"
    DOWNLOAD_SUCCEEDED = "download_succeeded"
    DUMP_RUNNING = "dump_running"
    UNITY_DUMPABLE = "unity_dumpable"
    UNITY_UNSUPPORTED = "unity_unsupported"
    FAILED = "failed"
    CLEANED = "cleaned"


class PairStatus(StrEnum):
    PENDING = "pending"
    COMPARING = "comparing"
    UPLOADING = "uploading"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class VersionRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version_code: str | None = Field(default=None, alias="versionCode")
    version_name: str | None = Field(default=None, alias="versionName")

    @field_validator("version_code", "version_name", mode="before")
    @classmethod
    def _stringify(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def label(self) -> str:
        return self.version_name or self.version_code or "unknown"


class UnityCheckRequest(VersionRef):
    package_name: str = Field(alias="packageName")
    app_name: str | None = Field(default=None, alias="appName")


class PairCompareRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package_name: str = Field(alias="packageName")
    app_name: str | None = Field(default=None, alias="appName")
    old_version: VersionRef = Field(alias="oldVersion")
    new_version: VersionRef = Field(alias="newVersion")


class BatchCompareRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    package_name: str = Field(alias="packageName")
    app_name: str | None = Field(default=None, alias="appName")
    versions: list[VersionRef] = Field(min_length=2)


class Progress(BaseModel):
    versions_total: int = Field(alias="versionsTotal")
    versions_downloaded: int = Field(alias="versionsDownloaded")
    versions_dumped: int = Field(alias="versionsDumped")
    comparisons_total: int = Field(alias="comparisonsTotal")
    comparisons_completed: int = Field(alias="comparisonsCompleted")
    comparisons_failed: int = Field(alias="comparisonsFailed")
