import json
from email.message import Message
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse
from zipfile import is_zipfile

import httpx

from app.config import Settings
from app.models import VersionRef


class ApsClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def download(self, package_name: str, version: VersionRef, target: Path) -> Path:
        if not self.settings.aps_base_url:
            raise ValueError("APS_BASE_URL is required.")
        params = {}
        if version.version_code:
            params["versionCode"] = version.version_code
        elif version.version_name:
            params["versionName"] = version.version_name
        headers = {"Authorization": f"Bearer {self.settings.aps_api_key}"} if self.settings.aps_api_key else {}
        url = f"{self.settings.aps_base_url.rstrip('/')}/api/v1/android/apps/{quote(package_name)}/download"
        async with httpx.AsyncClient(follow_redirects=True, timeout=self.settings.aps_download_timeout_seconds) as client:
            target = await self._download_response(client, url, target, headers=headers, params=params)
        if not target.exists() or target.stat().st_size == 0 or not is_zipfile(target):
            raise ValueError(f"APS returned an invalid package file: {target}")
        return target

    async def _download_response(
        self,
        client: httpx.AsyncClient,
        url: str,
        target: Path,
        *,
        headers: dict[str, str],
        params: dict | None = None,
    ) -> Path:
        async with client.stream("GET", url, params=params, headers=headers) as response:
            if response.status_code == 202:
                data = json.loads((await response.aread()).decode("utf-8"))
                return await self._wait_job(client, data, target, headers)
            response.raise_for_status()
            target = _target_with_response_suffix(target, response, url)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)
            return target

    async def _wait_job(self, client: httpx.AsyncClient, data: dict, target: Path, headers: dict[str, str]) -> Path:
        import asyncio

        status_url = data.get("statusUrl")
        if not status_url:
            raise ValueError("APS 202 response missing statusUrl")
        status_url = self._absolute_url(status_url)
        while True:
            status = await client.get(status_url, headers=headers)
            status.raise_for_status()
            body = status.json()
            if body.get("status") == "failed":
                raise ValueError(body.get("error") or "APS download job failed")
            if body.get("status") == "succeeded":
                file_url = body.get("fileUrl")
                if not file_url:
                    raise ValueError("APS job succeeded without fileUrl")
                return await self._download_response(client, self._absolute_url(file_url), target, headers=headers)
            await asyncio.sleep(self.settings.aps_job_poll_seconds)

    def _absolute_url(self, url_or_path: str) -> str:
        return urljoin(f"{self.settings.aps_base_url.rstrip('/')}/", url_or_path)


def _target_with_response_suffix(target: Path, response: object, url: str) -> Path:
    final_url = str(getattr(response, "url", url))
    suffix = _package_suffix(_response_filename(response)) or _package_suffix(unquote(Path(urlparse(final_url).path).name))
    if suffix and target.suffix.lower() != suffix:
        return target.with_suffix(suffix)
    return target


def _response_filename(response: object) -> str:
    content_disposition = getattr(response, "headers", {}).get("content-disposition", "")
    message = Message()
    message["content-disposition"] = content_disposition
    return message.get_filename() or ""


def _package_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return suffix if suffix in {".apk", ".xapk", ".apks"} else ""
