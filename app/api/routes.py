from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_api_key
from app.config import Settings, get_settings
from app.db import TaskStore
from app.models import BatchCompareRequest, PairCompareRequest, TaskStatus, UnityCheckRequest
from app.storage import build_report_storage

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])
discover_router = APIRouter()


def get_store(settings: Settings = Depends(get_settings)) -> TaskStore:
    settings.ensure_directories()
    return TaskStore(settings.task_db_path)


@router.post("/unity-checks", status_code=202)
async def create_unity_check(request: UnityCheckRequest, store: TaskStore = Depends(get_store)):
    return {"taskId": store.create_unity_check(request), "status": "queued"}


@router.post("/comparisons", status_code=202)
async def create_comparison(request: PairCompareRequest, store: TaskStore = Depends(get_store)):
    return {"taskId": store.create_pair_compare(request), "status": "queued"}


@router.post("/batch-comparisons", status_code=202)
async def create_batch_comparison(request: BatchCompareRequest, store: TaskStore = Depends(get_store)):
    return {"taskId": store.create_batch_compare(request), "status": "queued"}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, settings: Settings = Depends(get_settings), store: TaskStore = Depends(get_store)):
    task = store.get_task(task_id)
    if task is None:
        return JSONResponse({"error": "NOT_FOUND", "message": "Task not found."}, status_code=404)
    add_signed_urls(task, settings)
    return JSONResponse(task)


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, store: TaskStore = Depends(get_store)):
    status = store.cancel_task(task_id)
    if status is None:
        return JSONResponse({"error": "NOT_FOUND", "message": "Task not found."}, status_code=404)
    if status != TaskStatus.CANCELLED:
        return JSONResponse({"error": "TASK_FINISHED", "message": "Finished tasks cannot be cancelled."}, status_code=409)
    return {"taskId": task_id, "status": status}


@router.post("/tasks/{task_id}/retry", status_code=202)
async def retry_task(task_id: str, store: TaskStore = Depends(get_store)):
    new_task_id = store.retry_task(task_id)
    if new_task_id is None:
        return JSONResponse({"error": "NOT_FOUND", "message": "Task not found."}, status_code=404)
    return {"taskId": new_task_id, "status": "queued", "retryOf": task_id}


def add_signed_urls(task: dict, settings: Settings) -> None:
    all_artifacts = [task["artifacts"], *(item["artifacts"] for item in task["comparisons"])]
    if not any(all_artifacts):
        return
    storage = build_report_storage(settings)
    for artifacts in all_artifacts:
        for artifact in artifacts:
            url = storage.signed_url(
                artifact["objectKey"],
                expires_in=settings.report_signed_url_ttl_seconds,
                filename=artifact["name"],
            )
            if url:
                artifact["url"] = url


@discover_router.get("/discover")
async def discover(request: Request, settings: Settings = Depends(get_settings)):
    base_url = str(request.base_url).rstrip("/")
    data_endpoints = {
        "unity_check": {
            "method": "POST",
            "path": "/api/v1/unity-checks",
            "auth": "api_key",
            "request": {"packageName": "string", "versionCode": "string optional", "versionName": "string optional"},
            "response": {"status": 202, "body": {"taskId": "string", "status": "queued"}},
        },
        "single_compare": {
            "method": "POST",
            "path": "/api/v1/comparisons",
            "auth": "api_key",
            "request": {
                "packageName": "string",
                "oldVersion": {"versionCode": "string optional", "versionName": "string optional"},
                "newVersion": {"versionCode": "string optional", "versionName": "string optional"},
            },
            "response": {"status": 202, "body": {"taskId": "string", "status": "queued"}},
        },
        "batch_compare": {
            "method": "POST",
            "path": "/api/v1/batch-comparisons",
            "auth": "api_key",
            "request": {
                "packageName": "string",
                "versions": [{"versionCode": "string optional", "versionName": "string optional"}],
            },
            "response": {"status": 202, "body": {"taskId": "string", "status": "queued"}},
        },
        "get_task": {"method": "GET", "path": "/api/v1/tasks/{taskId}", "auth": "api_key"},
        "cancel_task": {"method": "POST", "path": "/api/v1/tasks/{taskId}/cancel", "auth": "api_key"},
        "retry_task": {"method": "POST", "path": "/api/v1/tasks/{taskId}/retry", "auth": "api_key"},
    }
    return {
        "name": "Android Unity Compare Service",
        "version": "0.1",
        "description": "Android Unity 包校验、相邻版本对比、批量对比和报告生成服务。",
        "base_url": base_url,
        "auth": {
            "type": "api_key",
            "description": "数据 API 使用 Authorization: Bearer <key>，兼容 X-API-Key。Key 可在 /admin 创建/吊销；AUTH_ENABLED=false 时放行。",
            "scheme": {"in": "header", "name": "Authorization", "format": "Bearer <key>", "alt_header": "X-API-Key"},
            "public_endpoints": ["/", "/health", "/discover", "/auth/login", "/auth/callback", "/auth/logout"],
            "api_key_endpoints": [
                item["path"] for item in data_endpoints.values()
            ],
            "session_endpoints": ["/admin", "/admin/api-keys", "/admin/api-keys/{keyId}/revoke"],
        },
        "concepts": {
            "package_name": "Android 包名，是所有任务的应用标识。",
            "version_selection": "versionCode 优先，versionName 兜底。",
            "task": "顶层异步任务，包含一个 Unity 校验、一个 pair 对比，或一个批量相邻对比。",
            "version": "任务内的某个版本，包含下载和 dump 状态。",
            "pair": "相邻两个版本的对比段。",
            "report_artifact": "上传到报告对象存储的报告文件；SQLite 存 objectKey，查询任务时实时补 signed URL。",
            "cancel": "queued/running 任务可取消；running 任务在下载、dump、compare 阶段边界协作停止。",
            "retry": "重试会基于原 payload 创建新 queued 任务，不复用旧 artifact。",
        },
        "statuses": {
            "task": ["queued", "running", "succeeded", "partial_failed", "failed", "cancelled"],
            "version": ["download_pending", "download_running", "download_succeeded", "dump_running", "unity_dumpable", "unity_unsupported", "failed", "cleaned"],
            "pair": ["pending", "comparing", "uploading", "succeeded", "failed"],
        },
        "endpoints": {
            "tasks": data_endpoints,
            "admin": {
                "admin_page": {"method": "GET", "path": "/admin", "auth": "session"},
                "create_api_key": {"method": "POST", "path": "/admin/api-keys", "auth": "session"},
                "revoke_api_key": {"method": "POST", "path": "/admin/api-keys/{keyId}/revoke", "auth": "session"},
            },
            "auth": {
                "login": {"method": "GET", "path": "/auth/login", "auth": "public"},
                "callback": {"method": "GET", "path": "/auth/callback", "auth": "public"},
                "logout": {"method": "GET", "path": "/auth/logout", "auth": "public"},
            },
            "system": {
                "health": {"method": "GET", "path": "/health", "auth": "public"},
                "discover": {"method": "GET", "path": "/discover", "auth": "public"},
                "home": {"method": "GET", "path": "/", "auth": "public"},
            },
        },
        "errors": {"format": "{ error: <code>, message: <str>, details?: <object> }"},
        "config": {
            "variables": _discover_config_variables(settings)
        },
        "workflows": {
            "single_compare": "提交包名和两个版本，worker 下载、dump、对比、上传报告并清理本地工作目录。",
            "batch_compare": "提交包名和版本列表，服务按规则排序后创建相邻 pair，每个版本只 dump 一次。",
        },
    }


def _discover_config_variables(settings: Settings) -> dict[str, dict[str, str]]:
    return {
        "PORT": {"default": str(settings.port), "description": "API 服务监听端口"},
        "HOST_PORT": {"default": "18080", "description": "Docker Compose 暴露到宿主机的端口"},
        "PUBLIC_BASE_URL": {"default": settings.public_base_url, "description": "公网访问根地址，用于 OAuth callback"},
        "DATA_DIR": {"default": str(settings.data_dir), "description": "SQLite 和 local report 数据目录"},
        "WORK_DIR": {"default": str(settings.work_dir), "description": "下载、dump、报告生成临时工作目录"},
        "DB_PATH": {"default": str(settings.task_db_path), "description": "任务 SQLite 数据库路径"},
        "AUTH_ENABLED": {"default": str(settings.auth_enabled).lower(), "description": "开启 API Key 和管理后台登录鉴权"},
        "AUTH_API_KEY_ENABLED": {"default": str(settings.auth_api_key_enabled).lower(), "description": "开启数据 API Key 校验"},
        "API_KEYS": {"default": "", "description": "兼容静态 API Key，逗号分隔；不在 discover 暴露真实值"},
        "FEISHU_APP_ID": {"default": "", "description": "飞书 OAuth 应用 App ID"},
        "FEISHU_APP_SECRET": {"default": "", "description": "飞书 OAuth 应用 App Secret；不在 discover 暴露真实值"},
        "FEISHU_AUTH_BASE": {"default": settings.feishu_auth_base, "description": "飞书 OAuth 授权地址"},
        "FEISHU_API_BASE": {"default": settings.feishu_api_base, "description": "飞书 OpenAPI 地址"},
        "SESSION_TTL_HOURS": {"default": str(settings.session_ttl_hours), "description": "管理后台 session 有效期"},
        "HTTP_TIMEOUT_SECONDS": {"default": str(settings.http_timeout_seconds), "description": "飞书 OAuth/OpenAPI HTTP 超时"},
        "APS_BASE_URL": {"default": "", "description": "APS 服务地址；不在 discover 暴露真实值"},
        "APS_API_KEY": {"default": "", "description": "APS API Key；不在 discover 暴露真实值"},
        "APS_DOWNLOAD_TIMEOUT_SECONDS": {"default": str(settings.aps_download_timeout_seconds), "description": "APS 下载超时"},
        "APS_JOB_POLL_SECONDS": {"default": str(settings.aps_job_poll_seconds), "description": "APS 202 任务轮询间隔"},
        "TASK_CONCURRENCY": {"default": str(settings.task_concurrency), "description": "同时运行的顶层任务数"},
        "DOWNLOAD_CONCURRENCY": {"default": str(settings.download_concurrency), "description": "同时从 APS 下载包的数量"},
        "DUMP_CONCURRENCY": {"default": str(settings.dump_concurrency), "description": "同时执行 Unity 检查和 Il2Cpp dump 的数量"},
        "COMPARE_CONCURRENCY": {"default": str(settings.compare_concurrency), "description": "同时执行 pair 对比的数量"},
        "IL2CPP_DUMPER_PATH": {"default": str(settings.il2cpp_dumper_path or ""), "description": "Il2CppDumper 可执行文件路径"},
        "IL2CPP_DUMPER_TIMEOUT_SECONDS": {"default": str(settings.il2cpp_dumper_timeout_seconds), "description": "Il2CppDumper 执行超时"},
        "DLL_ANALYZER_PATH": {"default": str(settings.dll_analyzer_path or ""), "description": "DllAnalyzer 可执行文件路径"},
        "DLL_ANALYZER_TIMEOUT_SECONDS": {"default": str(settings.dll_analyzer_timeout_seconds), "description": "DllAnalyzer 执行超时"},
        "REPORT_STORAGE_BACKEND": {"default": settings.report_storage_backend, "description": "报告存储后端：local/gcs/s3"},
        "REPORT_SIGNED_URL_TTL_SECONDS": {"default": str(settings.report_signed_url_ttl_seconds), "description": "报告 signed URL 有效期"},
        "REPORT_STORAGE_PREFIX": {"default": settings.report_storage_prefix, "description": "报告对象 key 前缀"},
        "REPORT_GCS_BUCKET": {"default": "", "description": "GCS 报告桶名"},
        "REPORT_GCS_CREDENTIALS_JSON": {"default": "", "description": "GCS service account JSON 内容或文件路径；不在 discover 暴露真实值"},
        "REPORT_S3_BUCKET": {"default": "", "description": "S3 报告桶名"},
        "REPORT_S3_REGION": {"default": "", "description": "S3 区域"},
        "REPORT_S3_ENDPOINT_URL": {"default": "", "description": "S3 兼容端点，可留空使用 AWS"},
        "REPORT_S3_ACCESS_KEY_ID": {"default": "", "description": "S3 access key；不在 discover 暴露真实值"},
        "REPORT_S3_SECRET_ACCESS_KEY": {"default": "", "description": "S3 secret key；不在 discover 暴露真实值"},
        "OPENAI_API_KEY": {"default": "", "description": "配置后 HTML 报告会调用 OpenAI-compatible API 生成 AI 分析；不在 discover 暴露真实值"},
        "OPENAI_BASE_URL": {"default": "https://api.openai.com/v1", "description": "OpenAI-compatible API base URL"},
        "OPENAI_MODEL": {"default": "gpt-4.1", "description": "AI 分析使用的模型；不传 temperature 参数"},
        "KEEP_FAILED_WORK_DIR": {"default": str(settings.keep_failed_work_dir).lower(), "description": "失败任务是否保留本地工作目录"},
        "WORK_DIR_TTL_HOURS": {"default": str(settings.work_dir_ttl_hours), "description": "工作目录兜底清理 TTL"},
        "WORKER_POLL_SECONDS": {"default": str(settings.worker_poll_seconds), "description": "worker 轮询 queued 任务和完成任务的间隔"},
        "WEB_CONCURRENCY": {"default": "2", "description": "Gunicorn web worker 数量"},
        "GUNICORN_TIMEOUT_SECONDS": {"default": "21600", "description": "Gunicorn 请求超时"},
    }


@discover_router.get("/")
async def home(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return {
        "message": "请先读取 /discover 获取 API 契约。",
        "discover": f"{base_url}/discover",
    }
