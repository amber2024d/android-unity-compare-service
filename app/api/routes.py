from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.deps import require_api_key
from app.config import Settings, get_settings
from app.db import TaskStore
from app.models import BatchCompareRequest, PairCompareRequest, UnityCheckRequest

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
async def get_task(task_id: str, store: TaskStore = Depends(get_store)):
    task = store.get_task(task_id)
    if task is None:
        return JSONResponse({"error": "NOT_FOUND", "message": "Task not found."}, status_code=404)
    return JSONResponse(task)


@discover_router.get("/discover")
async def discover(request: Request, settings: Settings = Depends(get_settings)):
    base_url = str(request.base_url).rstrip("/")
    return {
        "name": "Android Unity Compare Service",
        "version": "0.1",
        "description": "Android Unity 包校验、相邻版本对比、批量对比和报告生成服务。",
        "base_url": base_url,
        "auth": {
            "type": "api_key",
            "description": "数据 API 使用 Authorization: Bearer <key>，兼容 X-API-Key。AUTH_ENABLED=false 时放行。",
            "scheme": {"in": "header", "name": "Authorization", "format": "Bearer <key>", "alt_header": "X-API-Key"},
            "public_endpoints": ["/health", "/discover"],
            "api_key_endpoints": [
                "/api/v1/unity-checks",
                "/api/v1/comparisons",
                "/api/v1/batch-comparisons",
                "/api/v1/tasks/{taskId}",
            ],
            "session_endpoints": ["/"],
        },
        "concepts": {
            "package_name": "Android 包名，是所有任务的应用标识。",
            "version_selection": "versionCode 优先，versionName 兜底。",
            "task": "顶层异步任务，包含一个 Unity 校验、一个 pair 对比，或一个批量相邻对比。",
            "version": "任务内的某个版本，包含下载和 dump 状态。",
            "pair": "相邻两个版本的对比段。",
            "report_artifact": "上传到报告对象存储的报告文件；第一版本地存 objectKey，signed URL 后续接存储后端。",
        },
        "endpoints": {
            "tasks": {
                "unity_check": {"method": "POST", "path": "/api/v1/unity-checks", "auth": "api_key"},
                "single_compare": {"method": "POST", "path": "/api/v1/comparisons", "auth": "api_key"},
                "batch_compare": {"method": "POST", "path": "/api/v1/batch-comparisons", "auth": "api_key"},
                "get_task": {"method": "GET", "path": "/api/v1/tasks/{taskId}", "auth": "api_key"},
            },
            "system": {
                "health": {"method": "GET", "path": "/health", "auth": "public"},
                "discover": {"method": "GET", "path": "/discover", "auth": "public"},
            },
        },
        "errors": {"format": "{ error: <code>, message: <str>, details?: <object> }"},
        "config": {
            "variables": {
                "TASK_CONCURRENCY": {"default": str(settings.task_concurrency), "description": "同时运行的顶层任务数"},
                "DOWNLOAD_CONCURRENCY": {"default": str(settings.download_concurrency), "description": "同时从 APS 下载包的数量"},
                "DUMP_CONCURRENCY": {"default": str(settings.dump_concurrency), "description": "同时执行 Il2Cpp dump 的数量"},
                "COMPARE_CONCURRENCY": {"default": str(settings.compare_concurrency), "description": "同时执行 pair 对比的数量"},
                "IL2CPP_DUMPER_PATH": {"default": str(settings.il2cpp_dumper_path or ""), "description": "Il2CppDumper 可执行文件路径"},
                "DLL_ANALYZER_PATH": {"default": str(settings.dll_analyzer_path or ""), "description": "DllAnalyzer 可执行文件路径"},
                "OPENAI_API_KEY": {"default": "", "description": "配置后 HTML 报告会调用 OpenAI-compatible API 生成 AI 分析"},
                "OPENAI_BASE_URL": {"default": "https://api.openai.com/v1", "description": "OpenAI-compatible API base URL"},
                "OPENAI_MODEL": {"default": "gpt-4.1", "description": "AI 分析使用的模型"},
            }
        },
        "workflows": {
            "single_compare": "提交包名和两个版本，worker 下载、dump、对比、上传报告并清理本地工作目录。",
            "batch_compare": "提交包名和版本列表，服务按规则排序后创建相邻 pair，每个版本只 dump 一次。",
        },
    }


@discover_router.get("/")
async def home(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return {
        "message": "请先读取 /discover 获取 API 契约。",
        "discover": f"{base_url}/discover",
    }
