# Android Unity 对比服务方案

## 参考项目

- 主监控项目：`/Users/chenshuai/PycharmProjects/UnityAppVersionMonitor`
- Android Package Service：`/Users/chenshuai/VSCodeProjects/android-package-service`

## 目标

新建一个独立的云端 Android Unity 包分析服务，用来承载耗时的下载、dump、对比和 AI 分析流程。

第一版只支持 Android 包，能力包括：

- 校验指定版本是否是可 dump 的 Unity 包。
- 提交单个对比任务：包名 + 两个版本。
- 提交多版本批量对比任务：包名 + 版本列表，相邻版本递增对比。
- 通过环境变量控制任务、下载、dump、对比的并发数量。
- 查询任务状态、批量对比进度、失败原因和报告下载地址。
- 通过 Docker Compose 部署到云 VM。

这个服务不替代 Android Package Service（APS）。APS 继续负责 Android 版本发现、包下载、包缓存、包对象存储、包下载签名 URL 和 APS 自身权限校验。

## 当前 APS 前提

基于 `android-package-service` 的 `feat/cloud-migration` 分支能力设计：

- APS 数据 API 已支持 API Key，调用方式为 `Authorization: Bearer <key>` 或 `X-API-Key`。
- APS 包产物已抽象为 `STORAGE_BACKEND=local|gcs|s3`。
- APS 下载接口可能返回包文件、`202` 异步下载任务，或 `302` 到对象存储 signed URL。
- APS 下载任务成功后返回 `fileUrl`，对比服务跟随跳转并把包下载到自己的本地工作目录。

对比服务只存报告，不重复实现 APS 的包缓存和包对象存储。

## 部署形态

```text
APS VM
  aps-api
  aps-download-worker
  aps-catalog-scheduler
  包对象存储

Compare VM
  compare-api
  compare-worker
  SQLite 任务库
  本地 SSD 工作目录
  报告对象存储
```

公网只需要暴露 `compare-api`。APS 优先通过 VPC/private IP 访问；如果必须公网访问，APS API Key 必须开启。

## 技术选型

- Python + FastAPI：提供任务提交、查询、管理接口。
- SQLite：单 VM 下保存任务状态和进度。
- 独立 worker loop：后台执行下载、dump、对比、上传和清理。
- Docker Compose：部署 `compare-api` 和 `compare-worker`。
- 复用当前仓库的 Unity dump/compare 代码和 `lib/product` 二进制。
- Docker 镜像安装 .NET 8 和 .NET 9 runtime（非 SDK）以及 `libicu`，用于运行 `lib/product/Il2CppDumper` 和 `lib/product/DllAnalyzer`。当前 Linux 二进制是 x86-64，Compose 固定 `platform: linux/amd64`。

第一版不引入 Redis、Celery、Postgres、Kubernetes、动态多机器调度。等单台 compare VM 被打满后再升级。

## 项目结构

目标结构：

```text
android-unity-compare-service/
  app/
    main.py
    config.py
    db.py
    models.py
    auth/
      deps.py
      routes.py
      service.py
      store.py
      feishu.py
    api/
      routes.py
      schemas.py
    aps/
      client.py
    worker/
      loop.py
      executor.py
      cleanup.py
    unity/
      dumper.py
      compare.py
      report.py
    storage/
      base.py
      gcs.py
      s3.py
      factory.py
  lib/product/
  docker-compose.yml
  Dockerfile
  pyproject.toml
```

当前最小实现已经落地：

```text
android-unity-compare-service/
  app/
    main.py              # FastAPI 应用入口
    config.py            # 环境变量配置
    db.py                # SQLite schema 和任务状态存取
    models.py            # 请求模型和状态枚举
    api/routes.py        # /discover、/health 外的任务 API
    auth/deps.py         # 静态 API_KEYS 门禁
    aps/client.py        # APS 下载 client，已接入 worker
    worker/loop.py       # worker 主循环
    worker/executor.py   # 下载包、判断 Unity 可 dump、汇总 pair 状态
    worker/cleanup.py    # WORK_DIR TTL 清理
    unity/dumper.py      # Unity 包判断、Il2CppDumper 输入提取和真实 dump 入口
    unity/compare.py     # DummyDll 目录对比，内容契约兼容主监控项目
    unity/report.py      # HTML 报告生成，字段读取方式兼容主监控项目
  tests/test_service.py
  PROJECT_MAP.md
  lib/product/Il2CppDumper/
  lib/product/DllAnalyzer/
  docker-compose.yml
  Dockerfile
  pyproject.toml
```

## 权限校验

因为服务要部署在公网，权限方案参考 APS 新实现，不重新设计一套。

- 数据 API 使用 API Key：`Authorization: Bearer <key>` 或 `X-API-Key`。
- 管理页面使用飞书 OAuth 单管理员登录。
- 管理员可以创建、查看、吊销 API Key。
- `/discover` 保持公开，方便 Agent 先读取契约再决定如何鉴权调用。
- `/health` 可以公开。
- 报告文件不直接公开，接口只返回短期 signed URL。

环境变量：

```env
AUTH_ENABLED=true
AUTH_API_KEY_ENABLED=true
API_KEYS=...  # 当前最小实现使用逗号分隔静态 key；管理后台阶段替换为 auth.sqlite 哈希存储
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_AUTH_BASE=https://accounts.feishu.cn
FEISHU_API_BASE=https://open.feishu.cn
SESSION_TTL_HOURS=24
```

实现方式复用 APS 的形态：独立 `auth.sqlite`，API Key 只存 hash，服务端 session，OAuth state 落库。

当前最小实现暂时没有管理后台和 `auth.sqlite`，只支持 `AUTH_ENABLED=true` + `API_KEYS=key1,key2` 的静态门禁；后续管理后台阶段替换为 APS 同款哈希存储。

## 环境变量

```env
PORT=8080
PUBLIC_BASE_URL=https://compare.example.com

APS_BASE_URL=http://aps-private-ip:11010
APS_API_KEY=...
APS_DOWNLOAD_TIMEOUT_SECONDS=21600
APS_JOB_POLL_SECONDS=10

TASK_CONCURRENCY=2
DOWNLOAD_CONCURRENCY=4
DUMP_CONCURRENCY=2
COMPARE_CONCURRENCY=2
IL2CPP_DUMPER_PATH=/app/lib/product/Il2CppDumper/linux/Il2CppDumper
IL2CPP_DUMPER_TIMEOUT_SECONDS=3600
DLL_ANALYZER_PATH=/app/lib/product/DllAnalyzer/linux/DllAnalyzer
DLL_ANALYZER_TIMEOUT_SECONDS=300

DATA_DIR=/app/data
WORK_DIR=/app/work
DB_PATH=/app/data/tasks.sqlite

REPORT_STORAGE_BACKEND=gcs
REPORT_SIGNED_URL_TTL_SECONDS=3600
REPORT_STORAGE_PREFIX=unity-compare-reports

REPORT_GCS_BUCKET=...
REPORT_GCS_CREDENTIALS_JSON=...

REPORT_S3_BUCKET=...
REPORT_S3_REGION=...
REPORT_S3_ENDPOINT_URL=
REPORT_S3_ACCESS_KEY_ID=...
REPORT_S3_SECRET_ACCESS_KEY=...

OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1

KEEP_FAILED_WORK_DIR=false
WORK_DIR_TTL_HOURS=24
```

## 自描述接口

参考 APS 的 `/discover`，对比服务也需要提供公开自描述接口：

```http
GET /discover
```

用途：

- 给 Agent 或自动化系统读取完整 API 契约。
- 告知调用方哪些接口公开、哪些接口需要 API Key、哪些页面需要 OAuth 会话。
- 描述任务模型、状态流转、报告 signed URL、错误格式和关键环境变量。

`/discover` 不需要 API Key。原因是调用方需要先读契约，才能知道如何携带 API Key。它只暴露接口形状和能力边界，不返回任务数据、报告 URL、API Key 或云存储凭证。

建议返回结构：

```json
{
  "name": "Android Unity Compare Service",
  "version": "1.0",
  "description": "Android Unity 包校验、相邻版本对比、批量对比和报告生成服务。",
  "base_url": "https://compare.example.com",
  "auth": {
    "type": "api_key",
    "description": "数据 API 需要 Authorization: Bearer <key>，兼容 X-API-Key。管理页面走飞书 OAuth 单管理员登录。",
    "scheme": {
      "in": "header",
      "name": "Authorization",
      "format": "Bearer <key>",
      "alt_header": "X-API-Key"
    },
    "public_endpoints": ["/health", "/discover"],
    "api_key_endpoints": [
      "/api/v1/unity-checks",
      "/api/v1/comparisons",
      "/api/v1/batch-comparisons",
      "/api/v1/tasks/{taskId}"
    ],
    "session_endpoints": ["/", "/admin"]
  },
  "concepts": {
    "package_name": "Android 包名，是所有任务的应用标识。",
    "version_selection": "versionCode 优先，versionName 兜底。",
    "task": "顶层异步任务，包含一个 Unity 校验、一个 pair 对比，或一个批量相邻对比。",
    "version": "任务内的某个版本，包含下载和 dump 状态。",
    "pair": "相邻两个版本的对比段。",
    "report_artifact": "上传到报告对象存储的 HTML、JSON 或其他报告文件，查询任务时返回短期 signed URL。"
  },
  "endpoints": {
    "tasks": {},
    "system": {}
  },
  "errors": {
    "format": "{ error: <code>, message: <str>, details?: <object> }"
  },
  "config": {
    "variables": {
      "TASK_CONCURRENCY": {"default": "2", "description": "同时运行的顶层任务数"},
      "DOWNLOAD_CONCURRENCY": {"default": "4", "description": "同时从 APS 下载包的数量"},
      "DUMP_CONCURRENCY": {"default": "2", "description": "同时执行 Il2Cpp dump 的数量"},
      "COMPARE_CONCURRENCY": {"default": "2", "description": "同时执行 pair 对比的数量"}
    }
  },
  "workflows": {
    "single_compare": "提交包名和两个版本，服务从 APS 下载两个包，dump 后对比，上传报告并清理本地工作目录。",
    "batch_compare": "提交包名和版本列表，服务排序后按相邻版本建 pair，版本下载和 dump 可并发，任意 pair 两端 dump 完成后立即对比。"
  }
}
```

可以额外提供 OAuth 保护的 HTML 首页 `/`，展示一段“请先读取 `/discover`”的提示词，形态参考 APS 首页。

## API 设计

### Unity 可导校验

```http
POST /api/v1/unity-checks
Authorization: Bearer <api-key>
Content-Type: application/json

{
  "packageName": "com.example.game",
  "versionCode": "123",
  "versionName": "1.2.3"
}
```

`versionCode` 优先，`versionName` 作为兜底。

### 单个版本对比

```http
POST /api/v1/comparisons
Authorization: Bearer <api-key>
Content-Type: application/json

{
  "packageName": "com.example.game",
  "oldVersion": {"versionCode": "100", "versionName": "1.0.0"},
  "newVersion": {"versionCode": "101", "versionName": "1.0.1"}
}
```

### 多版本相邻对比

```http
POST /api/v1/batch-comparisons
Authorization: Bearer <api-key>
Content-Type: application/json

{
  "packageName": "com.example.game",
  "versions": [
    {"versionCode": "100", "versionName": "1.0.0"},
    {"versionCode": "101", "versionName": "1.0.1"},
    {"versionCode": "102", "versionName": "1.0.2"}
  ]
}
```

排序规则：

- 如果所有版本都有可转数字的 `versionCode`，按 `versionCode` 升序。
- 如果有版本缺少 `versionCode`，保留输入顺序。
- 第一版不做复杂 semver 猜测。

### 任务状态查询

```http
GET /api/v1/tasks/{taskId}
Authorization: Bearer <api-key>
```

返回示例：

```json
{
  "taskId": "abc",
  "type": "batch_compare",
  "status": "running",
  "packageName": "com.example.game",
  "progress": {
    "versionsTotal": 3,
    "versionsDownloaded": 2,
    "versionsDumped": 2,
    "comparisonsTotal": 2,
    "comparisonsCompleted": 1,
    "comparisonsFailed": 0
  },
  "comparisons": [
    {
      "oldVersion": "1.0.0",
      "newVersion": "1.0.1",
      "status": "succeeded",
      "artifacts": [
        {"name": "report.html", "url": "https://signed-url"},
        {"name": "report.json", "url": "https://signed-url"}
      ],
      "error": null
    }
  ],
  "error": null
}
```

报告 signed URL 在查询时实时生成，不把会过期的 URL 固化进 SQLite。

当前最小实现还没有报告存储后端，查询任务时 artifact 返回 `objectKey` 和 `contentType`，不返回 signed URL；阶段 7 接入 GCS/S3 后再改为实时签名 URL。

## 任务模型

任务状态：

```text
queued
running
succeeded
partial_failed
failed
cancelled
```

版本状态：

```text
download_pending
download_running
download_succeeded
dump_running
unity_dumpable
unity_unsupported
failed
cleaned
```

相邻对比段状态：

```text
pending
comparing
uploading
succeeded
failed
```

SQLite 至少保存四类记录：

- task：顶层任务。
- version：某个任务内的版本下载和 dump 状态。
- pair：相邻版本对比状态。
- artifact：报告对象 key、文件名、content type。

这样状态查询和重启恢复都足够直接。

## APS Client 行为

针对一个包名和版本：

1. 调 APS `/api/v1/android/apps/{package}/download`。
2. 带上 `Authorization: Bearer $APS_API_KEY`。
3. APS 返回包文件时，直接流式写入 `WORK_DIR/{task_id}/packages/`。
4. APS 返回 `302` 时，跟随 signed URL 并流式下载到本地。
5. APS 返回 `202` 时，轮询 `statusUrl`，直到 `succeeded` 或 `failed`。
6. 成功后访问 `fileUrl`，跟随 `302` 并下载到本地。

虽然 APS 已经校验过包，对比服务本地仍要确认文件非空且是 zip，避免无效文件进入 dump。

## 执行模型

使用环境变量控制并发：

- `TASK_CONCURRENCY`：同时运行的顶层任务数。
- `DOWNLOAD_CONCURRENCY`：同时从 APS 下载包的数量。
- `DUMP_CONCURRENCY`：同时执行 Il2Cpp dump 的数量。
- `COMPARE_CONCURRENCY`：同时执行 pair 对比的数量。

多版本任务流程：

1. 规范化版本列表并排序。
2. 创建相邻 pair，例如 `1 -> 2`、`2 -> 3`。
3. 并发下载所有版本。
4. 某个版本下载完成后，立即开始 dump。
5. 任意 pair 的两个版本都 dump 成功后，立即开始该 pair 对比。
6. pair 对比完成后上传报告目录。
7. 根据结果把任务标记为 `succeeded`、`partial_failed` 或 `failed`。
8. 清理整个任务工作目录。

如果某个版本不是可导 Unity，只失败包含该版本的相邻 pair，其他 pair 继续执行。

## 避免重复 dump

不要在批量模式的内部循环直接调用当前 `XapkComparator.compare_xapks()`，因为它会对每个 pair 重复 dump 旧包和新包。

需要拆成两个层次：

- `dump_package(package_path) -> dummy_dll_dir`
- `compare_dummy_dirs(old_dummy_dir, new_dummy_dir, output_dir, metadata) -> report`

每个版本只下载一次、dump 一次；相邻 pair 复用 dump 后的 `DummyDll` 目录。

## 本地清理

每个任务使用独立工作目录：

```text
WORK_DIR/{task_id}/
  packages/
  dumps/
  reports/
  tmp/
```

清理规则：

- 版本 dump 成功后，如果后续不再需要原始包，删除该包文件。
- pair 报告上传成功后，删除该 pair 的本地报告目录。
- 任务结束时删除整个 `WORK_DIR/{task_id}`，包括包、dump 结果、报告和临时文件。
- worker 启动时扫描 `WORK_DIR`，删除没有对应 running task 的任务目录。
- 失败任务默认也清理本地目录；只有 `KEEP_FAILED_WORK_DIR=true` 时保留现场。
- 增加兜底 TTL 清理，默认 `WORK_DIR_TTL_HOURS=24`。

这里必须包含 dump 输出目录。Il2Cpp dump 结果可能很大，如果不清理会长期占用磁盘。

## 报告存储

对比服务只抽象报告存储，不抽象包存储。

当前最小实现先把报告文件复制到 `DATA_DIR/reports/{REPORT_STORAGE_PREFIX}/{taskId}/{pairId}/`，并把该路径作为 artifact `objectKey` 写入 SQLite。报告内容兼容主监控项目 `UnityUpdateMonitor.generate_full_report()` 的 JSON 顶层字段、`summary`、`overall_statistics` 和 `dll_comparisons` 结构；HTML 报告沿用主监控项目的统计、变更详情、详细对比和 AI 智能分析区块。配置 `OPENAI_API_KEY` 后，HTML 报告会调用 OpenAI-compatible `/chat/completions` 生成 Markdown 分析；未配置或调用失败时只在 HTML 中显示提示，不改变 JSON 报告内容契约。GCS/S3 接入后只替换 artifact 上传和签名 URL，不改变报告内容契约。

接口：

```python
class ReportStorage:
    def upload_file(local_path, key, content_type): ...
    def upload_dir(local_dir, prefix): ...
    def signed_url(key, ttl_seconds, filename): ...
```

第一版支持：

- `REPORT_STORAGE_BACKEND=gcs`
- `REPORT_STORAGE_BACKEND=s3`

对象 key 布局：

```text
{REPORT_STORAGE_PREFIX}/{packageName}/{taskId}/{pairId}/report.html
{REPORT_STORAGE_PREFIX}/{packageName}/{taskId}/{pairId}/report.json
{REPORT_STORAGE_PREFIX}/{packageName}/{taskId}/{pairId}/...
```

## Docker Compose

```yaml
services:
  compare-api:
    build: .
    platform: linux/amd64
    ports:
      - "18080:8080"
    environment:
      PORT: 8080
      IL2CPP_DUMPER_PATH: /app/lib/product/Il2CppDumper/linux/Il2CppDumper
      DLL_ANALYZER_PATH: /app/lib/product/DllAnalyzer/linux/DllAnalyzer
    volumes:
      - ./data:/app/data
      - ./work:/app/work

  compare-worker:
    build: .
    platform: linux/amd64
    command: ["python", "-m", "app.worker.loop"]
    environment:
      PORT: 8080
      IL2CPP_DUMPER_PATH: /app/lib/product/Il2CppDumper/linux/Il2CppDumper
      DLL_ANALYZER_PATH: /app/lib/product/DllAnalyzer/linux/DllAnalyzer
    volumes:
      - ./data:/app/data
      - ./work:/app/work
```

云 VM 上 `/app/work` 应使用本地 SSD。不要把工作目录放到对象存储挂载上。

## 实施阶段

1. [done] 搭 FastAPI、配置、SQLite 任务表、Docker Compose。
2. [partial] 实现公开 `/discover` 和首页 `/`；OAuth 保护的首页随管理后台阶段接入。
3. [done] 实现 APS client：API Key、`202` 轮询、`302` 跟随下载，并接入 worker。
4. [done] 迁移 Unity dump、对比、报告生成代码和二进制：已迁移 Il2CppDumper、DllAnalyzer 单文件二进制、DummyDll compare、兼容内容报告和 HTML AI 分析调用。
5. [done] 实现 Unity 可导校验和单 pair 对比：worker 已下载包、判断 libil2cpp/global-metadata，执行真实 dump，并对 DummyDll 生成 JSON/HTML 报告。
6. [partial] 实现批量相邻对比：版本级任务建模、排序、下载复用和 pair 状态汇总已落地；当前执行器按 pair 顺序处理，后续再按 `COMPARE_CONCURRENCY` 做并发调度。
7. 实现报告 GCS/S3 存储和 signed URL。
8. 实现 API Key + 飞书 OAuth 管理后台，形态参考 APS。
9. [partial] 实现成功、失败、worker 启动和 TTL 四类清理：worker loop 和 TTL 清理已落地。
10. [partial] 增加 fake APS 或 mock APS 的 smoke test：当前覆盖 API 提交/查询和鉴权门禁。

## 当前实现状态

已落地最小可运行骨架：

- `app/main.py` 提供 FastAPI 服务、`/health`、`/discover` 和 `/`。
- `app/db.py` 使用 SQLite 保存 task/version/pair/artifact，支持提交和查询任务。
- `app/api/routes.py` 支持 `/api/v1/unity-checks`、`/api/v1/comparisons`、`/api/v1/batch-comparisons`、`/api/v1/tasks/{taskId}`。
- `app/worker/loop.py` 可领取 queued task，执行 APS 下载、Unity 包判断、pair 成败汇总和清理。
- `app/aps/client.py` 已具备下载接口、APS `202` 轮询和重定向跟随能力，并已接入执行器。
- `app/unity/dumper.py` 支持扫描 APK/XAPK 内嵌 APK、提取 `libil2cpp.so`/`global-metadata.dat`，并在 `IL2CPP_DUMPER_PATH` 或仓库 `lib/product` 可用时运行 Il2CppDumper。
- `app/unity/compare.py` 迁移主监控项目 DummyDll 对比逻辑，调用 `DllAnalyzer <dll> <output_json>` 分析 DLL，并按原项目字段结构生成 compare report。
- `app/unity/report.py` 生成 HTML 报告，内容区块和字段读取方式兼容主监控项目；配置 `OPENAI_API_KEY` 时会调用 OpenAI-compatible API 生成 AI 智能分析，未配置或失败时保留提示。
- `lib/product/Il2CppDumper/` 已从主监控项目迁入；Docker 默认使用 Linux 二进制，本地 macOS 会自动使用 osx 二进制。
- `lib/product/DllAnalyzer/` 已从主监控项目重新发布为单文件二进制：Linux `linux-x64`、macOS `osx-arm64`。Docker 默认使用 Linux 版本。
- `PROJECT_MAP.md` 记录当前代码入口和模块边界。

刻意暂缓：

- 飞书 OAuth 管理后台和 API Key 管理页面；当前先用 `AUTH_ENABLED=true` + `API_KEYS=key1,key2` 做数据 API 门禁。
- GCS/S3 报告 signed URL；当前查询返回 artifact objectKey，本地报告保存在 `DATA_DIR/reports/`。

当前降级策略：

- 未配置 `IL2CPP_DUMPER_PATH` 且仓库未放置 `lib/product/Il2CppDumper/{linux|osx}/Il2CppDumper` 时，worker 只做基础 Unity 结构检查并继续流转，避免开发环境没有大二进制时无法跑通。
- 一旦显式配置 `IL2CPP_DUMPER_PATH`，dump 失败会标记 version failed，不再静默降级。

## 待定事项

- 报告存储和 auth 代码是从 APS 复制小模块，还是抽成共享内部包。第一版建议复制，减少跨仓库耦合。
- 旧 `UnityAppVersionMonitor` 是直接同步调用新对比服务，还是先保留本地路径，等新服务稳定后切换。
- 第一版是否需要 cancel/retry 接口。默认先不做 cancel；retry 可以通过重新提交任务解决。
