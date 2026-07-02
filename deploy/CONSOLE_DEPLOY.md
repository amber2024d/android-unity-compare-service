# AWS 网页控制台部署步骤（固定 On-Demand 实例 + EIP + S3）

不用命令行、纯 AWS 管理控制台点击完成部署。流程沿用 APS（android-package-service）的部署经验，但架构按本服务的特点调整：

| | APS | 本服务 |
| --- | --- | --- |
| 计费模式 | Spot 抢占式 + ASG 自动补机 | **On-Demand 固定实例**（对比任务长达数小时，中断重跑浪费大） |
| 公网 IP | 无固定 IP（ALB 入站） | **EIP 固定出入站 IP**（`OPENAI_BASE_URL` 接口按来源 IP 白名单放行） |
| 入站 HTTPS | ALB + ACM 证书 | **Caddy 容器自动 Let's Encrypt**（单固定机无负载均衡需求，省掉 ALB/ACM/目标组） |
| SQLite | Litestream 复制到 S3（跨回收不丢） | Litestream 复制到 S3（**灾备**：机器不会被回收，但防实例损坏/误删/换机） |
| 对象存储 | S3 存产物 | S3 存对比报告（`unity-compare-reports/` 前缀），与 Litestream（`litestream/` 前缀）同桶 |

架构：单台 EC2（On-Demand，EIP）上跑 Docker Compose 四容器——`caddy`（80/443 终止 HTTPS）→ `compare-api`（8080）+ `compare-worker` + `litestream`。报告上传 S3，客户端拿短期 signed URL 下载。

## 前提（先备好）

- **域名**：飞书 OAuth 必须真实 https 回调，不能用裸 IP。例：`unity-compare.example.com`。
- **部署镜像仓公开**：user-data 开机 `git clone` 走 HTTPS，公开仓库免鉴权。把本仓库 fork/镜像到你自己的**公开**仓库后用它部署；下文占位 `https://github.com/<你的组织>/android-unity-compare-service.git`，分支 `main`。
- **飞书自建应用**：拿到 App ID / App Secret，开启「网页应用 / OAuth 登录」。
- **APS 已就绪**：云上 APS 的地址（`APS_BASE_URL`）和在其 `/admin` 创建的 API Key。
- **AI 接口白名单**：确认 `OPENAI_BASE_URL` 接口方支持按来源 IP 加白名单（第 4 步拿到 EIP 后提交，生效常有延迟，越早提交越好）。
- **全程同一区域**（下例 `us-west-2`，与 APS 同区可少跨区流量）；右上角记下你的 **12 位账号 ID**。

> 约定占位：`REGION`=区域、`ACCOUNT_ID`=账号 ID、`BUCKET`=S3 桶名、`<域名>`=你的域名、`<EIP>`=第 4 步分配的弹性 IP。

---

## 1. S3 桶（报告 + Litestream 灾备）

**S3 → Create bucket**：
- Name：`unity-compare-prod`（=下文 `BUCKET`），Region：`us-west-2`。
- **Block all public access 保持勾选**（私有；调用方靠服务签发的 signed URL 取报告，不需要桶公开）。
- Create。

## 2. IAM 角色（实例用）

**IAM → Roles → Create role**：
- Trusted entity = **AWS service**，Use case = **EC2** → Next → 直接创建，名字 `unity-compare-service-instance`。
- 进该角色 **Add permissions → Attach policies** → 勾 **AmazonSSMManagedInstanceCore**（Session Manager 免密登录运维）。
- **Add permissions → Create inline policy → JSON**，粘下面（把 `BUCKET`/`REGION`/`ACCOUNT_ID` 换掉）：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "S3Reports", "Effect": "Allow", "Action": ["s3:GetObject","s3:PutObject"], "Resource": "arn:aws:s3:::BUCKET/unity-compare-reports/*" },
    { "Sid": "S3Litestream", "Effect": "Allow", "Action": ["s3:GetObject","s3:PutObject","s3:DeleteObject"], "Resource": "arn:aws:s3:::BUCKET/litestream/*" },
    { "Sid": "S3List", "Effect": "Allow", "Action": ["s3:ListBucket","s3:GetBucketLocation"], "Resource": "arn:aws:s3:::BUCKET" },
    { "Sid": "SsmRead", "Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": "arn:aws:ssm:REGION:ACCOUNT_ID:parameter/android-unity-compare-service/env" },
    { "Sid": "SsmKmsDecrypt", "Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": "arn:aws:kms:REGION:ACCOUNT_ID:key/*", "Condition": { "StringEquals": { "kms:ViaService": "ssm.REGION.amazonaws.com" } } }
  ]
}
```

> `S3Reports` 的资源前缀 = `.env.cloud` 的 `REPORT_STORAGE_PREFIX`（默认 `unity-compare-reports`）；改前缀要同步改策略。

## 3. SSM 参数（存 .env.cloud 全文）

**Systems Manager → Parameter Store → Create parameter**：
- Name：`/android-unity-compare-service/env`
- Type：**SecureString**（KMS key 用默认 `alias/aws/ssm`）
- Value：粘贴你的 `.env.cloud` **全文**（以仓库 [`.env.cloud.example`](../.env.cloud.example) 为模板填好域名、飞书、APS、S3、OPENAI 各项）。

> `REPORT_S3_ACCESS_KEY_ID/SECRET` **留空** → 走实例 IAM 角色（第 2 步）。`HOST_PORT=8080` 保持——compare-api 只发布给实例本机排障用，公网入口是 Caddy。

## 4. EIP（先拿 IP，白名单和 DNS 提前办）

**EC2 → Elastic IPs → Allocate Elastic IP address** → Allocate。记下 `<EIP>`，立刻去办两件生效慢的事：

1. **AI 接口白名单**：把 `<EIP>` 提供给 `OPENAI_BASE_URL` 接口方登记来源 IP 白名单。
2. **DNS**：你的 DNS 服务商加 **A 记录**：`<域名>` → `<EIP>`（Caddy 签发 Let's Encrypt 证书前必须已解析）。

> EIP 关联到运行中的实例后，实例 stop/start、重启都不换 IP；将来换机重建时把 EIP 重新关联到新实例即可，白名单和 DNS 都不用动。

## 5. 安全组

**EC2 → Security Groups → Create security group**（选你的 VPC）：
- 名字 `unity-compare-sg`。Inbound：`HTTP 80` 来源 `0.0.0.0/0`（ACME challenge + 跳转 https）、`HTTPS 443` 来源 `0.0.0.0/0`。**不开 22**（用 Session Manager）。**不开 8080**（仅实例内排障）。
- Outbound 默认全放（拉代码/镜像、S3、SSM、飞书、APS、AI 接口都要出网）。

## 6. EC2 实例（On-Demand 固定机）

**EC2 → Instances → Launch an instance**：
- AMI：**Amazon Linux 2023（x86_64）**。**必须 x86_64，不能选 ARM/Graviton**——`lib/product/` 的 Il2CppDumper/DllAnalyzer 二进制是 x86-64，Compose 也固定 `platform: linux/amd64`。
- Instance type：`m6i.large`（2 vCPU / 8 GB）起步，购买选项保持默认（**On-Demand**，不要勾 Spot）。容量权衡见下方[容量与机型](#容量与机型)。
- Key pair：可不选（用 Session Manager 登录）。
- Network settings：选**公网子网**，**Auto-assign public IP = Enable**（开机先用临时公网 IP 出网跑 user-data，第 7 步换成 EIP）；Security group 选 `unity-compare-sg`。
- Storage：**100 GiB gp3**（工作目录解包/dump 峰值占用大，见容量小节）。
- **Advanced details**：
  - **IAM instance profile** = 第 2 步角色。
  - **Termination protection = Enable**（固定机防误删）。
  - **Metadata version = V2 only (token required)**；**Metadata response hop limit = 2**（关键！容器内 boto3/Litestream 要靠 IMDS 拿实例角色凭据；填 1 会导致 S3 上传/Litestream 全挂）。
  - **User data**（把区域、仓库地址换成你的）：

```bash
#!/bin/bash
set -euxo pipefail
exec > >(tee -a /var/log/user-data.log) 2>&1
dnf install -y docker git awscli
systemctl enable --now docker
install -d /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)" -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
install -d /opt/app
git clone --branch main https://github.com/<你的组织>/android-unity-compare-service.git /opt/app
cd /opt/app
aws ssm get-parameter --region us-west-2 --name /android-unity-compare-service/env --with-decryption --query 'Parameter.Value' --output text > /opt/app/.env.cloud
chmod 600 /opt/app/.env.cloud
docker compose -f docker-compose.yml -f docker-compose.cloud.yml --env-file .env.cloud up -d --build
```

- Launch instance。

## 7. 关联 EIP + 验证出口 IP

- **EC2 → Elastic IPs → 选中第 4 步的 EIP → Actions → Associate**：Instance = 第 6 步实例 → Associate。
- **Systems Manager → Session Manager → Start session** 登进实例，验证出站源 IP 就是 EIP：

```sh
curl -s https://checkip.amazonaws.com   # 应输出 <EIP>
```

> 此后实例的出站（访问 AI 接口、APS、S3）与入站（域名解析）都固定走这个 IP。

## 8. 飞书回调

飞书开放平台你的应用 →「安全设置 → 重定向 URL」登记 `https://<域名>/auth/callback`（**逐字一致**，含协议/域名/路径，与 `.env.cloud` 的 `PUBLIC_BASE_URL` 对应）。

## 9. 验证

- 首启现构建镜像（含 .NET runtime 下载），**等约 5 分钟**。Session Manager 登实例看进度：

```sh
sudo cat /var/log/user-data.log                  # 开机脚本执行到哪
cd /opt/app && sudo docker compose ps            # 四容器：caddy / compare-api / compare-worker / litestream
```

- 浏览器 `https://<域名>/health` → `{"status":"ok"}`（证书由 Caddy 自动签发；DNS 刚生效时可能要再等一两分钟）。
- 开 `https://<域名>/` → 飞书 OAuth 登录，**第一个登录者即管理员** → 到 `/admin` 建一个 API Key。
- 提交冒烟任务（Unity 判定，不触发完整对比）：

```sh
curl -X POST https://<域名>/api/v1/unity-checks \
  -H 'Authorization: Bearer <key>' -H 'Content-Type: application/json' \
  -d '{"packageName":"<包名>","versionCode":"<版本号>"}'
# 返回 202 和 taskId，然后轮询：
curl -H 'Authorization: Bearer <key>' https://<域名>/api/v1/tasks/<taskId>
```

- **验证 AI 白名单生效**：提交一个真实对比任务（`POST /api/v1/comparisons`），任务成功后打开返回的 HTML 报告 signed URL，确认「AI 智能分析」段有内容而不是调用失败提示。失败多半是 EIP 还没在接口方白名单生效。

---

## 容量与机型

- **内存**：Il2CppDumper 对大包（il2cpp 二进制 + metadata 数百 MB）dump 时吃内存。默认 `TASK_CONCURRENCY=2`、`DUMP_CONCURRENCY=2`，8 GB 起步可用；若 worker 日志出现 OOM/被杀，优先把 `DUMP_CONCURRENCY` 降到 1，仍不够再升 `m6i.xlarge`（4 vCPU / 16 GB）。
- **磁盘**：每个任务的工作目录峰值 ≈ 参与版本数 × 包大小 ×（解压/提取放大 2~3 倍）；批量对比多版本叠加并发任务，100 GB 是合理起点。`WORK_DIR_TTL_HOURS=24` 会自动清理过期工作目录，报告本体在 S3 不占本地盘。磁盘吃紧时 **EC2 → 卷 → Modify volume** 在线扩容（扩后实例里 `sudo growpart /dev/nvme0n1 1 && sudo xfs_growfs /`）。
- **CPU**：dump/compare 是 CPU 密集，核数决定吞吐不决定成败；任务排队变慢再升配。
- **成本参考**（us-west-2 按需价）：`m6i.large` ≈ $70/月 + EBS gp3 100GB ≈ $8/月 + EIP ≈ $3.6/月 + S3/流量少量 ≈ **$85/月**。长期跑可上 1 年期 Savings Plan 省约三成；机器升降配只需 stop → change instance type → start（EIP/数据都保持）。

## 日常运维

### 更新部署

对比任务一旦中断就要整体重跑，**先确认没有 running 任务再更新**：

```sh
cd /opt/app
sudo docker compose exec compare-api python -c \
  "from app.db import TaskStore; from app.config import get_settings; print(TaskStore(get_settings().task_db_path).running_task_ids())"
# 输出 set() 表示空闲，再执行：
sudo git pull
sudo docker compose -f docker-compose.yml -f docker-compose.cloud.yml --env-file .env.cloud up -d --build
```

如果确实在有任务运行时重启了：worker 启动会把中断的任务自动标记为 `failed`（error 注明因重启中断），对这些任务调 `POST /api/v1/tasks/{taskId}/retry` 重新提交即可（retry 按原 payload 建新任务）。

改了 `.env.cloud`（SSM 参数）后同样流程：先更新 SSM 参数，再在实例上重拉 + 重启：

```sh
aws ssm get-parameter --region us-west-2 --name /android-unity-compare-service/env --with-decryption --query 'Parameter.Value' --output text | sudo tee /opt/app/.env.cloud > /dev/null
cd /opt/app && sudo docker compose -f docker-compose.yml -f docker-compose.cloud.yml --env-file .env.cloud up -d
```

### 灾备与换机重建

- 数据三份保障：报告在 S3；`tasks.sqlite`/`auth.sqlite` 由 Litestream 持续复制到 S3 `litestream/` 前缀；EBS 本身持久。可选再开定期 EBS 快照（Lifecycle Manager）兜底 work 目录以外的一切。
- 系统级故障（硬件/宿主问题）由 EC2 **simplified automatic recovery** 自动迁移恢复，实例 ID/EIP/EBS 都保持，无需干预。
- 彻底换机重建：用第 6 步同样配置起新实例（user-data 会自动 clone + 从 SSM 拉 env + 起容器，Litestream 先从 S3 恢复两个 SQLite 再放行应用）→ 把 EIP 重新关联到新实例 → 白名单/DNS 全都不用动。运行中的任务会丢，按上面 retry 恢复。

## 故障排查

Session Manager 登进实例：

```sh
sudo cat /var/log/user-data.log          # 开机脚本哪步失败
cd /opt/app && sudo docker compose ps    # 容器状态
sudo docker compose logs caddy           # 证书签发/反代
sudo docker compose logs litestream      # 恢复/S3 权限/桶名
sudo docker compose logs compare-worker  # 任务执行/dump/OOM
curl -s localhost:8080/health            # 绕过 Caddy 直测应用
```

| 症状 | 原因 / 处理 |
| --- | --- |
| clone 失败 | 部署镜像仓不是 public → 设为 public，或 user-data 加 GitHub token |
| S3 上传/Litestream 全报权限或超时 | Launch 时 **IMDS hop limit 不是 2** → 容器拿不到实例角色凭据（Instance → Actions → Instance settings → Modify instance metadata options 可改）；或 IAM 策略桶名/前缀/区域填错 |
| `https://<域名>` 打不开、Caddy 日志刷证书错误 | 域名 A 记录没指到 EIP / DNS 未生效 / 安全组没放 80；Caddy 会自动重试，修好 DNS 后稍等即可 |
| 飞书登录回调 400 | 重定向 URL 没在飞书**逐字**登记，或 `PUBLIC_BASE_URL` 与域名不一致 |
| 报告 AI 分析失败 | EIP 未在 `OPENAI_BASE_URL` 接口方白名单生效（`curl -s https://checkip.amazonaws.com` 核对当前出口 IP）；或 `OPENAI_API_KEY` 错 |
| 容器起不来报 `exec format error` | 选了 ARM/Graviton 机型 → 换 x86_64 机型重建 |
| 任务一直卡 `running` | 任务运行中重启过 worker 且用的旧代码；当前版本 worker 启动会自动标 `failed`，对该任务 retry |
| 下载阶段全失败 | `APS_BASE_URL`/`APS_API_KEY` 没配对，或 APS 侧 Key 被吊销；看 compare-worker 日志 |

## 与 APS 部署文档的关系

APS 的控制台部署见 `../../android-package-service/deploy/CONSOLE_DEPLOY.md`（Spot + ASG + ALB 形态）。两个服务独立部署、独立域名，本服务通过公网域名 + API Key 调 APS。
