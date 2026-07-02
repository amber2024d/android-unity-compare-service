# AGENTS.md

## 工程地图入口

- 项目地图：[PROJECT_MAP.md](PROJECT_MAP.md)
- 当前方案文档：[docs/android_unity_compare_service_plan.md](docs/android_unity_compare_service_plan.md)
- Git 提交规范：[docs/git_commit_convention.md](docs/git_commit_convention.md)
- 主监控项目：`../../PycharmProjects/UnityAppVersionMonitor`
- Android Package Service：`../android-package-service`

## 文档约定

- `docs/` 是活文档：每次调整功能边界、接口契约、任务状态、存储约定、部署方式或权限策略，都要同步更新相关文档。
- `AGENTS.md` 只保留入口、约定和常用路径；详细设计放在 `docs/`。
- 新实现优先参考 APS 的云迁移分支：API Key、飞书 OAuth、自描述 `/discover`、对象存储和 Docker Compose 形态。
- Unity dump、对比和报告生成逻辑优先参考主监控项目现有实现，迁移时保持行为一致，再做必要拆分。

## 常用路径

- `docs/`：方案、接口、部署和迁移设计。
- `app/`：FastAPI 服务源码。
- `lib/product/`：后续放置 Unity dump/compare 所需二进制。
- `docker-compose.yml`：云 VM 部署入口。

## 工作要求

- 先读方案文档，再改代码。
- 代码变更要保持方案文档同步。
- 提交信息按 `docs/git_commit_convention.md`，中文说明 + Conventional Commits 格式。
- 默认先做单 VM、SQLite、Docker Compose 的最小可用版本；不要提前引入 Redis、Celery、Postgres、Kubernetes 或动态多机调度。
- 本地工作目录清理必须覆盖包文件、dump 结果、报告目录和临时文件，避免云 VM 磁盘堆积。
