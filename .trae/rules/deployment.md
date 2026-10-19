# 部署规则

> **本项目使用 GitHub Actions + GHCR 自动化部署。** push main 触发云端构建，SSH 服务器自动 pull + up。本节记录部署架构、CI/CD 细节、手动运维操作和防幻觉验证方法。

## 一、部署架构概览

### 1.1 三容器分离架构

| 容器名 | 镜像 | Dockerfile | 启动命令 | 说明 |
|--------|------|-----------|---------|------|
| stockfilter-kline | ghcr.io/bearflower/stockfilter-kline:latest | Dockerfile.kline | `python -u scripts/scheduler_kline.py` | K线数据采集服务 |
| stockfilter-obpc | ghcr.io/bearflower/stockfilter-obpc:latest | Dockerfile.obpc | `python -u scripts/scheduler_obpc.py` | OBPC 策略服务 |
| stockfilter-eadvisor | ghcr.io/bearflower/stockfilter-eadvisor:latest | Dockerfile.eadvisor | `python -u distill_changying/scripts/advisor/scheduler.py` | E大估值策略服务 |

### 1.2 服务器信息

| 项目 | 值 |
|------|---|
| 服务器 IP | 43.156.242.184 |
| SSH 用户 | root |
| 部署目录 | `/root/stockfilter_v3` |
| 部署日志 | `/root/stockfilter_v3/deploy_logs/YYYYMMDD.log` |
| Docker Compose | `/root/stockfilter_v3/docker-compose.yml` |

### 1.3 外部依赖

stockfilter 的容器不连接 Dockerfile 内置数据库，而是通过网络连接 common_service 项目的外部服务：

| 依赖 | 来源 | 说明 |
|------|------|------|
| common_service_common_network | common_service 项目创建的 Docker 外部网络 | stockfilter 3 个容器都要连这个网络才能访问 postgres 和 redis |

> **首次部署前必须确认网络存在**：`docker network ls | grep common_service_common_network`。不存在则手动创建：`docker network create common_service_common_network`。

### 1.4 配置文件注入

`config/config.yaml` 通过 docker-compose.yml 的 volume 挂载注入容器，不打包进镜像。**改配置只需要重启容器，不需要重新构建镜像**：

```bash
ssh root@43.156.242.184 "cd /root/stockfilter_v3 && docker compose restart"
```

---

## 二、CI/CD 流程

### 2.1 自动部署触发

push main 分支触发部署（**纯文档类变更除外**，见 2.3）。`.github/workflows/deploy.yml` 的触发条件：

```yaml
on:
  push:
    branches: [main]
    paths-ignore:
      - '.trae/**'
      - '.agents/**'
      - 'docs/**'
      - 'data/backtest/**'
      - '*.md'
      - '*.sqlite3'
  workflow_dispatch:          # 也可手动触发
```

### 2.2 完整流水线

```
push main（非纯文档变更，见 2.3）
  ↓
build-and-push job（矩阵构建 3 个镜像，fail-fast: false）
  ├─「判断是否需要构建」步骤：git diff HEAD~1 HEAD 列变更，
  │   若全部落在 .trae/ .agents/ docs/ data/backtest/ logs/ .env
  │   .gitignore ARCHIVE AGENTS.md README.md → skip=true，仅跳过下列构建步骤
  ├─ kline    → ghcr.io/bearflower/stockfilter-kline:latest
  ├─ obpc     → ghcr.io/bearflower/stockfilter-obpc:latest
  └─ eadvisor → ghcr.io/bearflower/stockfilter-eadvisor:latest
  ↓
deploy job（needs: build-and-push，skip 不影响本 job）
  ├─ 配置 SSH：SERVER_SSH_KEY 写入 ~/.ssh/id_rsa、ssh-keyscan 写 known_hosts、
  │   追加 ~/.ssh/config（User/IdentityFile/超时加固），仅做连通性测试（不用 scp）
  ├─ 远程脚本 base64 编码 → ssh "base64 -d | bash -s" 执行（规避转义问题）
  └─ 远程脚本依次执行：
       1) curl -fsSL 从 GitHub raw 拉 docker-compose.yml（不走 scp）
       2) GHCR 认证：GHCR_PULL_TOKEN 经 stdin 执行 docker login
       3) docker compose pull
       4) docker compose up -d --remove-orphans
       5) sleep 15 后打印容器状态
       6) 写部署日志 deploy_logs/YYYYMMDD.log ← DEPLOY_SUCCESS commit=<sha>
       7) docker image prune -f 清理悬空镜像（失败不阻断部署；只删悬空层，保留被 stopped 容器占用的旧版本镜像）
```

> 并发控制：`concurrency.group = deploy-${{ github.ref }}` 且 `cancel-in-progress: true`，同一分支上更新的 push 会取消正在进行的 Run。
> 注意「判断是否需要构建」只看 `HEAD~1..HEAD`（最后一次提交）：一次推送含多个提交时，仅按最后一个提交的变更决定是否跳过构建。

### 2.3 纯文档类变更不触发 workflow（paths-ignore 语义）

`paths-ignore` 是**整个 workflow 级别的过滤**：当一次 push 的变更文件**全部**落在下列范围时，GitHub Actions **完全跳过该 workflow**——`build-and-push` 与 `deploy` 都不会运行，服务器不会 pull/重启容器，也不写部署日志。

- `.trae/**`（规则文件）
- `.agents/**`（Codex skill）
- `docs/**`（文档）
- `data/backtest/**`（回测数据）
- `*.md`、`*.sqlite3`（文档/数据库）

> 注意：`logs/`、`.env`、`.gitignore` **不在** `paths-ignore` 列表中；它们只出现在 job 内「判断是否需要构建」步骤的跳过名单里。因此单独修改这些文件仍会触发 workflow，该步骤会置 `skip=true` 跳过镜像构建，但 `deploy` job 照常执行 pull + up。

只要变更中**还有任一文件**不在上面范围内 → workflow 正常触发：`build-and-push` 构建全部 3 个镜像（stockfilter 是单目录项目，任何源码变更都影响所有容器），`deploy` 随后 pull + up。

**需要为纯文档变更强制部署时**：到 GitHub Actions 页面用 `workflow_dispatch`（Run workflow）手动触发。

### 2.4 GitHub Secrets 配置

服务器端只需要 2 个 Secret（Settings → Secrets and variables → Actions）：

| Secret 名 | 值 | 用途 |
|-----------|---|------|
| `SERVER_SSH_KEY` | 服务器 root 用户的 SSH 私钥（only.pem 内容） | SSH 登录服务器执行部署 |
| `GHCR_PULL_TOKEN` | GitHub PAT，至少 `read:packages` 权限 | 服务器从 GHCR pull 镜像时认证 |

> push（构建镜像到 GHCR）用的是 `GITHUB_TOKEN`（内置，自动生效）。pull 不一样，服务器没有内置 token，需要手动配 `GHCR_PULL_TOKEN`。

---

## 三、手动运维操作

**以下操作属于运维，AI 只有在用户明确要求时才可执行。**

### 3.1 紧急回滚（新代码有 bug）

服务器保存了上一个镜像版本，直接回退：

```bash
ssh root@43.156.242.184 "cd /root/stockfilter_v3 && docker compose down && docker compose up -d"
```

docker compose up -d 会重新 pull latest，如果 latest 有问题需要重新部署：push main 恢复正常代码 → 等 Actions 部署。

### 3.2 Actions 挂了，手动补部署

GitHub Runner 偶发故障或网络问题，手动执行：

```bash
# 1. 确保 GHCR 认证有效
ssh root@43.156.242.184 "docker login ghcr.io -u bearflower"
# 输入 PAT（read:packages）

# 2. pull + up
ssh root@43.156.242.184 "cd /root/stockfilter_v3 && docker compose pull && docker compose up -d"

# 3. 验证
ssh root@43.156.242.184 "docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' | grep stockfilter"
```

### 3.3 服务器重启后容器没起来

```bash
ssh root@43.156.242.184 "cd /root/stockfilter_v3 && docker compose up -d"
```

### 3.4 外部网络缺失

common_service 项目重建 postgres 后网络可能丢失：

```bash
ssh root@43.156.242.184 "docker network create common_service_common_network && cd /root/stockfilter_v3 && docker compose up -d"
```

### 3.5 磁盘满了（Docker 构建缓存）

```bash
ssh root@43.156.242.184 "docker system prune -f"
```

### 3.6 只改了 config.yaml，重启容器

```bash
scp config/config.yaml root@43.156.242.184:/root/stockfilter_v3/config/config.yaml
ssh root@43.156.242.184 "cd /root/stockfilter_v3 && docker compose restart"
```

**不需要重新构建镜像。**

---

## 四、防幻觉验证

### 4.1 CI/CD 模式下的验证

push main 后等待 Actions Run 全绿，再检查：

| 检查项 | 命令 | 期望 |
|--------|------|------|
| Actions 全绿 | https://github.com/Bearflower/stockfiter/actions | build-and-push + deploy 两个 job 都 ✅ |
| 容器全部运行 | `ssh root@43.156.242.184 "docker ps --filter name=stockfilter --format 'table {{.Names}}\t{{.Status}}'"` | 3 个容器都是 Up |
| 日志无错误 | `ssh root@43.156.242.184 "docker logs --tail 50 stockfilter-obpc 2>&1 | grep -iE 'error|exception|fatal' || echo OK"` | 无错误输出 |
| 部署日志写入 | `ssh root@43.156.242.184 "cat /root/stockfilter_v3/deploy_logs/$(date +%Y%m%d).log"` | 有 DEPLOY_SUCCESS 记录 |

### 4.2 手动部署时的五层验证

**如果不走 CI/CD、手动构建部署，使用以下五层验证防幻觉：**

| 层级 | 验证内容 | 验证命令 |
|------|---------|---------|
| 第一层 | 容器状态 | `docker ps --filter name=stockfilter` 确认 3 个都在运行 |
| 第二层 | 镜像 ID | `docker inspect -f '{{.Image}}' stockfilter-obpc` == 最新构建镜像 |
| 第三层 | VERSION 文件 | 容器内 VERSION 文件 DEPLOY_ID == 本地 |
| **第四层** | **关键文件 MD5** | **容器内文件 MD5 == 本地文件 MD5** |
| 第五层 | 日志无错 | `docker logs stockfilter-obpc 2>&1 | grep -iE 'error\|exception\|fatal'` 无输出 |

**第四层 MD5 对比（手动部署时的核心验证）：**

```bash
# 本地（macOS 用 md5 -q）
md5 -q scripts/scheduler_obpc.py
md5 -q config/config.yaml

# 服务器容器内（Linux 用 md5sum）
ssh root@43.156.242.184 "docker exec stockfilter-obpc md5sum /app/scripts/scheduler_obpc.py"
ssh root@43.156.242.184 "docker exec stockfilter-obpc md5sum /app/config/config.yaml"
```

**本地与容器内 MD5 必须完全一致。**

---

## 五、部署状态查询

| 查什么 | 去哪查 |
|--------|--------|
| Actions 运行状态 | https://github.com/Bearflower/stockfiter/actions |
| GHCR 镜像列表 | https://github.com/orgs/bearflower/packages?repo_name=stockfiter |
| 服务器部署日志 | `ssh root@43.156.242.184 "cat /root/stockfilter_v3/deploy_logs/YYYYMMDD.log"` |
| 容器实时状态 | `ssh root@43.156.242.184 "docker ps --filter name=stockfilter"` |
| 容器日志 | `ssh root@43.156.242.184 "docker logs --tail 100 <容器名>"` |
| GHCR 认证状态 | `ssh root@43.156.242.184 "docker login ghcr.io -u bearflower"` |

---

## 六、常见问题排查

| 问题 | 症状 | 排查方向 |
|------|------|---------|
| deploy job 失败 | Actions exit code 100 | SERVER_SSH_KEY Secret 是否配了？服务器 SSH 端口是否被防火墙挡住？ |
| 拉取镜像失败 | SSH 手动 docker pull 报 unauthorized | GHCR_PULL_TOKEN 是否过期？PAT 是否有 `read:packages` 权限？ |
| 容器启动后立即退出 | docker ps 看不到容器 | 看日志：`docker logs stockfilter-obpc`，常见原因：数据库连不上、common_service_common_network 不存在 |
| 只有 2 个容器在跑 | stockfilter-eadvisor 没起来 | eadvisor 依赖 DEEPSEEK_API_KEY，检查 env 是否注入 |
| Actions 没触发 | push main 后 Actions tab 空的 | 若本次变更全为 `docs/**`、`*.md`、`.trae/**`、`.sqlite3` 等，属 `paths-ignore` 预期行为（见 2.3），非误判；否则检查 push 是否真在 main 分支 |
| 纯文档变更也想部署 | 只有 .md/docs 变更，Actions 完全没跑 | `paths-ignore` 命中导致整个 workflow 不触发，用 `workflow_dispatch`（Actions 页面 Run workflow）手动触发；或临时从 deploy.yml 的 `paths-ignore` 移除对应模式 |
| 改了 config 但容器没变 | 配置文件没生效 | volume 挂载需要重启容器：`docker compose restart`，不是 up -d |

---

**最后更新：** 2026-10-15（修正 2.3 paths-ignore 语义：纯文档类变更跳过整个 workflow，deploy job 不执行；修正 2.2 流水线事实：curl 拉 compose 而非 scp，deploy 实际步骤与并发控制）
