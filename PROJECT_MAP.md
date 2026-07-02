# Android Unity Compare Service 项目地图

## 入口文档

- 方案和开发状态：`docs/android_unity_compare_service_plan.md`
- Git 提交规范：`docs/git_commit_convention.md`
- Agent 工作约定：`AGENTS.md`
- 参考主监控项目：`../../PycharmProjects/UnityAppVersionMonitor`
- 参考 APS 项目：`../android-package-service`

## 当前代码结构

```text
android-unity-compare-service/
  app/
    main.py              # FastAPI 应用入口，注册 /health、/discover、/api/v1/*
    config.py            # 环境变量配置和目录创建
    db.py                # SQLite schema、任务创建、状态更新、任务查询
    models.py            # 任务、版本、pair 状态和请求模型
    api/routes.py        # 提交/查询任务和公开 discover/home
    auth/deps.py         # API Key 和管理 session 依赖
    auth/service.py      # auth.sqlite、API Key hash、session、OAuth state
    auth/routes.py       # 飞书 OAuth 登录/回调/退出
    admin/routes.py      # 管理后台和 API Key 创建/吊销
    aps/client.py        # APS 下载 client，支持 202 轮询和重定向跟随
    storage.py           # 报告 local/GCS/S3 上传和 signed URL
    worker/loop.py       # worker 主循环，按 TASK_CONCURRENCY 并发运行任务，启动时清理孤儿工作目录
    worker/executor.py   # 按 DOWNLOAD/DUMP/COMPARE_CONCURRENCY 执行下载、dump 和 pair 对比
    worker/cleanup.py    # WORK_DIR 孤儿目录和 TTL 清理
    unity/dumper.py      # Unity 包判断、Il2CppDumper 输入提取和真实 dump 入口
    unity/compare.py     # DummyDll 对比，报告 JSON 内容兼容主监控项目
    unity/report.py      # HTML 报告生成，支持 OpenAI-compatible AI 分析
  tests/test_service.py  # API、鉴权、admin/OAuth、worker、APS、storage、AI、fake APS 端到端和报告内容契约 smoke tests
  lib/product/Il2CppDumper/
  lib/product/DllAnalyzer/
  .env.example           # 环境变量模板，不包含真实 APS 地址或密钥
  docker-compose.yml     # compare-api + compare-worker
  Dockerfile
  pyproject.toml
```

## 已落地能力

- `GET /health`
- `GET /discover`
- `GET /`
- `POST /api/v1/unity-checks`
- `POST /api/v1/comparisons`
- `POST /api/v1/batch-comparisons`
- `GET /api/v1/tasks/{taskId}`
- `POST /api/v1/tasks/{taskId}/cancel`
- `POST /api/v1/tasks/{taskId}/retry`
- SQLite 保存 `task`、`version`、`pair`、`artifact`
- task 支持 cancel/retry；retry 基于原 payload 创建新任务，running cancel 为阶段边界协作取消
- worker 可启动清理非 running 的孤儿工作目录，按 `TASK_CONCURRENCY` 并发运行 queued task，调用 APS 下载包，判断 Unity 可 dump，并在配置 Il2CppDumper 时执行真实 dump
- 批量相邻对比复用版本 dump 结果，并按 `DOWNLOAD_CONCURRENCY`、`DUMP_CONCURRENCY`、`COMPARE_CONCURRENCY` 分段并发
- 仓库内置 `lib/product/Il2CppDumper`，Docker 默认使用 Linux 版本
- 仓库内置 `lib/product/DllAnalyzer` 单文件二进制，Docker 默认使用 Linux 版本
- DummyDll compare 已迁入，产出 `report.json` 和 `report.html`，JSON 内容结构兼容主监控项目
- 配置 `OPENAI_API_KEY` 后，HTML 报告会调用 OpenAI-compatible API 生成 AI 智能分析；JSON 报告内容不写入 AI 结果
- `REPORT_STORAGE_BACKEND=local|gcs|s3` 支持报告上传；GCS/S3 查询任务时返回短期 signed URL
- 生产 APS 地址和 API Key 只通过 `APS_BASE_URL` / `APS_API_KEY` 环境变量注入
- fake APS 端到端 smoke 覆盖提交任务、APS API Key、`202` 轮询、`fileUrl` 下载、worker dump/compare 和报告 artifact 生成
- 默认 pytest 覆盖 partial_failed、cancel/retry 边界、AI payload、本地/S3 报告存储参数和环境变量/Compose 对齐；Docker build 与 compare-api health smoke 已通过
- `.env.example` 提供本地和部署配置模板，不包含真实 APS 地址或密钥
- Docker 镜像安装 .NET 8 和 .NET 9 runtime（非 SDK）以及 `libicu76`；Compose 固定 `linux/amd64`
- `AUTH_ENABLED=true` 时支持飞书 OAuth 单管理员后台，API Key 创建/吊销；静态 `API_KEYS` 仍保留兼容

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18080
.venv/bin/python -m app.worker.loop
```

## 文档维护规则

- 改接口、状态、存储、鉴权、部署或清理策略时，同步更新 `docs/android_unity_compare_service_plan.md`。
- 新增顶层模块或关键入口时，同步更新本文件。
- 写提交信息时遵守 `docs/git_commit_convention.md`。
- `AGENTS.md` 只放入口和工作约定；细节放方案文档或本地图。
