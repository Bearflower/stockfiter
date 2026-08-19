# 部署规则

## 部署触发条件

- 用户输入 `/deploy`，触发部署流程
- 用户明确要求部署到服务器，触发部署流程
- 用户输入 `/git`，完成提交后询问是否需要部署

---

## 一、"部署幻觉"根因分析（强制阅读）⭐⭐⭐

"部署幻觉"是指：**部署流程显示成功，但线上运行的仍是旧代码。**

| 根因 | 具体表现 | 概率 |
|------|---------|------|
| Docker 构建缓存 | `docker-compose build` 使用缓存层，旧代码被打包进镜像 | 高 |
| 旧镜像未删除 | 未执行 `docker rmi`，`up -d` 发现镜像已存在就不重建 | 高 |
| 构建失败未感知 | build 报错但脚本未用 `set -e`，继续执行 `up -d` 启动旧容器 | 中 |
| 文件上传不完整 | scp/rsync 中途中断，服务器上解压时文件损坏 | 中 |
| 容器启动失败 | 新容器启动后立即退出，docker-compose 未报错 | 中 |
| 多个容器混淆 | 服务器上有多个同名容器，更新的不是运行中的那个 | 低 |
| 卷挂载覆盖 | 代码通过 volume 挂载，但宿主机文件未更新 | 低 |
| 脚本提前退出 | 流水线中某一步失败但未阻断后续步骤 | 低 |

**核心问题：** 传统验证只检查"容器是否在运行"和"镜像创建时间"，但**不验证"容器内的代码是否与本地一致"**。

---

## 二、防幻觉机制总览

```
部署前快照 → 版本标记 → 上传验证 → 构建验证 → 容器验证 → 代码验证 → 部署确认报告
```

每个阶段都有明确的验证点，任一阶段失败必须阻断后续流程。

---

## 三、部署前检查（强制）⭐⭐⭐

### 3.1 记录版本快照

记录当前本地和线上版本，作为后续对比基线：

```bash
# 本地版本快照
echo "=== 本地版本快照 ===" > /tmp/deploy_verify_$(date +%Y%m%d_%H%M%S).txt
git log --oneline -1 >> /tmp/deploy_verify_*.txt
echo "关键文件 MD5:" >> /tmp/deploy_verify_*.txt
md5sum strategies/btc_eth/main.py >> /tmp/deploy_verify_*.txt
md5sum strategies/btc_eth/config.yaml >> /tmp/deploy_verify_*.txt
md5sum shared/*.py >> /tmp/deploy_verify_*.txt

# 线上版本快照（部署前）
ssh root@SERVER_IP "echo '=== 线上容器启动时间 ===' && docker inspect -f '{{.Created}}' CONTAINER_NAME"
ssh root@SERVER_IP "echo '=== 线上容器内文件时间 ===' && docker exec CONTAINER_NAME stat /app/main.py 2>/dev/null || echo '容器未运行'"
```

### 3.2 代码同步检查

```bash
cd /Users/yl/vscode/Binance_quantitative_trading
bash scripts/check_code_sync.sh
```

**检查项：** 回测代码 vs 生产代码版本、策略参数一致性、共享模块完整性、代码修改时间差异。

### 3.3 环境区分

| 环境 | 代码位置 | 部署方式 |
|------|---------|---------|
| 回测环境 | `backtest/btc_eth/scripts/` | 本地执行，不部署到服务器 |
| 生产环境 | `strategies/btc_eth/` | 部署到服务器 |

### 3.4 配置文件检查

- `.env` — 环境变量
- `strategies/btc_eth/config.yaml` — 策略配置
- `.deploy_config` — 部署配置

### 3.5 生成版本标记文件

打包前自动生成 VERSION 文件，后续用它验证容器内代码是否为当前版本：

```bash
cat > VERSION << EOF
DEPLOY_TIME=$(date '+%Y-%m-%d %H:%M:%S')
GIT_COMMIT=$(git log --oneline -1 2>/dev/null || echo "no-git")
DEPLOY_ID=$(uuidgen | cut -d- -f1)
FILE_MD5=$(md5sum strategies/btc_eth/main.py | cut -d' ' -f1)
EOF
```

---

## 四、部署中防幻觉机制（强制）⭐⭐⭐

### 4.1 上传完整性验证

```bash
LOCAL_MD5=$(md5sum deployment_package.tar.gz | cut -d' ' -f1)
SERVER_MD5=$(ssh root@SERVER_IP "md5sum /root/deployment_package.tar.gz | cut -d' ' -f1")

if [ "$LOCAL_MD5" != "$SERVER_MD5" ]; then
    echo "❌ 文件上传不完整！本地MD5=$LOCAL_MD5 服务器MD5=$SERVER_MD5"
    exit 1
fi
echo "✅ 文件上传完整性验证通过"
```

### 4.2 强制删除旧镜像和缓存

**这是防止"部署幻觉"最关键的一步：**

```bash
ssh root@SERVER_IP << 'EOF'
set -e  # 任何错误立即退出

cd /root/PROJECT_NAME

# 1. 停止并删除旧容器
docker-compose down --remove-orphans

# 2. 删除所有相关镜像（强制）
docker images | grep PROJECT_NAME | awk '{print $3}' | xargs -r docker rmi --force

# 3. 清理构建缓存
docker builder prune -f -a

# 4. 解压新代码包
tar -xzf /root/deployment_package.tar.gz -C /root/PROJECT_NAME

# 5. 验证 VERSION 文件已正确解压
if [ ! -f "VERSION" ]; then
    echo "❌ VERSION 文件不存在，部署包可能损坏"
    exit 1
fi
cat VERSION

# 6. 构建（不使用缓存）
docker-compose build --no-cache

# 7. 启动
docker-compose up -d

# 8. 等待容器就绪
sleep 5

# 9. 确认容器正在运行
if ! docker ps -q -f name=CONTAINER_NAME | grep -q .; then
    echo "❌ 容器启动失败"
    docker logs --tail 50 CONTAINER_NAME
    exit 1
fi
EOF
```

### 4.3 构建日志错误检测

```bash
BUILD_LOG=$(ssh root@SERVER_IP "cd /root/PROJECT_NAME && docker-compose build --no-cache --progress=plain 2>&1")

if echo "$BUILD_LOG" | grep -qi "error\|exception\|failed\|exit code"; then
    echo "❌ 构建过程中发现错误！"
    echo "$BUILD_LOG" | grep -i "error\|exception\|failed\|exit code"
    exit 1
fi
```

---

## 五、部署后代码级验证（强制）⭐⭐⭐

**这是验证新版本代码是否真正在线上运行的核心步骤。**

### 第一层：容器状态验证

```bash
echo "=== 1. 容器运行状态 ==="
ssh root@SERVER_IP "docker ps -f name=CONTAINER_NAME --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

### 第二层：镜像 ID 验证

```bash
echo "=== 2. 镜像版本验证 ==="
CONTAINER_IMAGE_ID=$(ssh root@SERVER_IP "docker inspect -f '{{.Image}}' CONTAINER_NAME")
LATEST_IMAGE_ID=$(ssh root@SERVER_IP "docker images --no-trunc --format '{{.ID}}' | head -1")

if [ "$CONTAINER_IMAGE_ID" != "$LATEST_IMAGE_ID" ]; then
    echo "❌ 部署幻觉检测失败：容器使用的镜像不是最新构建的镜像！"
    exit 1
fi
echo "✅ 镜像版本一致"
```

### 第三层：VERSION 文件验证（关键）⭐⭐⭐

```bash
echo "=== 3. VERSION 文件验证 ==="
LOCAL_DEPLOY_ID=$(grep DEPLOY_ID VERSION | cut -d= -f2)
CONTAINER_DEPLOY_ID=$(ssh root@SERVER_IP "docker exec CONTAINER_NAME cat /app/VERSION 2>/dev/null | grep DEPLOY_ID | cut -d= -f2" || echo "NOT_FOUND")

if [ "$CONTAINER_DEPLOY_ID" = "NOT_FOUND" ]; then
    echo "❌ 部署幻觉检测失败：容器内不存在 VERSION 文件！说明容器内运行的代码不是本次部署的代码"
    exit 1
fi

if [ "$LOCAL_DEPLOY_ID" != "$CONTAINER_DEPLOY_ID" ]; then
    echo "❌ 部署幻觉检测失败：VERSION 文件不匹配！"
    echo "   本地 DEPLOY_ID: $LOCAL_DEPLOY_ID"
    echo "   容器内 DEPLOY_ID: $CONTAINER_DEPLOY_ID"
    exit 1
fi
echo "✅ VERSION 文件匹配，确认容器内为本次部署代码"
```

### 第四层：代码 MD5 校验（终极验证）⭐⭐⭐

```bash
echo "=== 4. 代码 MD5 校验 ==="
KEY_FILES=(
    "strategies/btc_eth/main.py"
    "strategies/btc_eth/config.yaml"
    "shared/constants.py"
)

for FILE in "${KEY_FILES[@]}"; do
    LOCAL_MD5=$(md5sum "$FILE" | cut -d' ' -f1)
    CONTAINER_MD5=$(ssh root@SERVER_IP "docker exec CONTAINER_NAME md5sum /app/$FILE 2>/dev/null | cut -d' ' -f1" || echo "NOT_FOUND")
    
    if [ "$CONTAINER_MD5" = "NOT_FOUND" ]; then
        echo "⚠️  容器内 $FILE 不存在，检查路径是否正确"
        continue
    fi
    if [ "$LOCAL_MD5" != "$CONTAINER_MD5" ]; then
        echo "❌ 部署幻觉检测失败：$FILE MD5 不匹配！"
        echo "   本地: $LOCAL_MD5"
        echo "   容器: $CONTAINER_MD5"
        exit 1
    fi
    echo "✅ $FILE MD5 匹配"
done
```

### 第五层：功能验证

```bash
echo "=== 5. 功能验证 ==="
ERROR_COUNT=$(ssh root@SERVER_IP "docker logs --tail 200 CONTAINER_NAME 2>&1 | grep -cE "(ERROR|Exception|FATAL|CRITICAL|Traceback)"")
if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "⚠️  发现 $ERROR_COUNT 个错误日志，请检查："
    ssh root@SERVER_IP "docker logs --tail 50 CONTAINER_NAME 2>&1 | grep -E '(ERROR|Exception|FATAL|CRITICAL|Traceback)'"
fi
```

---

## 六、部署确认报告（强制）⭐⭐⭐

所有验证通过后，必须生成部署确认报告：

```bash
#!/bin/bash
REPORT_FILE="/tmp/deploy_report_$(date +%Y%m%d_%H%M%S).md"

cat > "$REPORT_FILE" << REPORT_HEADER
# 部署确认报告

## 基本信息
- 部署时间: $(date '+%Y-%m-%d %H:%M:%S')
- 部署人员: $(whoami)
- 目标服务器: $SERVER_IP
- 项目名称: $PROJECT_NAME
- 容器名称: $CONTAINER_NAME

## 版本信息
- Git Commit: $(git log --oneline -1 2>/dev/null || echo "N/A")
- 部署 ID: $(grep DEPLOY_ID VERSION | cut -d= -f2)
- 部署时间戳: $(grep DEPLOY_TIME VERSION | cut -d= -f2)

## 验证结果
REPORT_HEADER

# 容器状态
CONTAINER_STATUS=$(ssh root@SERVER_IP "docker ps -f name=$CONTAINER_NAME --format '{{.Status}}'")
echo "- 容器运行状态: ✅ $CONTAINER_STATUS" >> "$REPORT_FILE"

# 镜像一致性
CONTAINER_IMAGE_ID=$(ssh root@SERVER_IP "docker inspect -f '{{.Image}}' $CONTAINER_NAME")
LATEST_IMAGE_ID=$(ssh root@SERVER_IP "docker images --no-trunc --format '{{.ID}}' | head -1")
if [ "$CONTAINER_IMAGE_ID" = "$LATEST_IMAGE_ID" ]; then
    echo "- 镜像版本一致性: ✅ 一致" >> "$REPORT_FILE"
else
    echo "- 镜像版本一致性: ❌ 不一致" >> "$REPORT_FILE"
fi

# VERSION 文件
CONTAINER_VERSION=$(ssh root@SERVER_IP "docker exec $CONTAINER_NAME cat /app/VERSION 2>/dev/null | grep DEPLOY_ID | cut -d= -f2" || echo "NOT_FOUND")
LOCAL_VERSION=$(grep DEPLOY_ID VERSION | cut -d= -f2)
if [ "$CONTAINER_VERSION" = "$LOCAL_VERSION" ]; then
    echo "- 容器内代码版本: ✅ 匹配 (部署ID: $CONTAINER_VERSION)" >> "$REPORT_FILE"
else
    echo "- 容器内代码版本: ❌ 不匹配" >> "$REPORT_FILE"
fi

# 关键文件 MD5
MD5_PASS=true
for FILE in "strategies/btc_eth/main.py" "strategies/btc_eth/config.yaml"; do
    LOCAL_MD5=$(md5sum "$FILE" 2>/dev/null | cut -d' ' -f1)
    CONTAINER_MD5=$(ssh root@SERVER_IP "docker exec $CONTAINER_NAME md5sum /app/$FILE 2>/dev/null" | cut -d' ' -f1)
    if [ "$LOCAL_MD5" = "$CONTAINER_MD5" ]; then
        echo "- 文件 $FILE: ✅ MD5 匹配" >> "$REPORT_FILE"
    else
        echo "- 文件 $FILE: ❌ MD5 不匹配" >> "$REPORT_FILE"
        MD5_PASS=false
    fi
done

# 最终结论
echo "" >> "$REPORT_FILE"
echo "## 最终结论" >> "$REPORT_FILE"
if [ "$CONTAINER_IMAGE_ID" = "$LATEST_IMAGE_ID" ] && [ "$CONTAINER_VERSION" = "$LOCAL_VERSION" ] && [ "$MD5_PASS" = true ]; then
    echo "✅ **部署成功！新版本代码已确认在生产环境中运行。**" >> "$REPORT_FILE"
    echo "   - 镜像版本: 一致" >> "$REPORT_FILE"
    echo "   - 代码版本: 匹配" >> "$REPORT_FILE"
    echo "   - 文件校验: 通过" >> "$REPORT_FILE"
else
    echo "❌ **部署失败！存在部署幻觉风险。**" >> "$REPORT_FILE"
    echo "   请检查上述验证失败项并重新部署。" >> "$REPORT_FILE"
fi

cat "$REPORT_FILE"
echo ""
echo "报告已保存到: $REPORT_FILE"
```

---

## 七、反幻觉检查清单

部署完成后，必须逐项检查：

```
□ 1. 容器状态确认: docker ps 显示所有容器运行中（包括 kline-monitor）
□ 2. 镜像 ID 对比: 容器镜像 ID == 最新构建镜像 ID
□ 3. VERSION 文件: 容器内 DEPLOY_ID == 本地 DEPLOY_ID
□ 4. 关键文件 MD5: 容器内文件 MD5 == 本地文件 MD5
□ 5. 日志无错误: 容器日志中无 error/exception/fatal
□ 6. 部署确认报告: 已生成并保存
□ 7. 无遗漏服务: docker ps | grep 确认所有服务（含 kline-monitor）都在运行

**任一检查项失败，视为部署失败，必须修复后重新部署。**

---

## 八、代码同步规则

### 回测代码 vs 生产代码

- 修改了 `backtest/` 目录下的代码，必须检查 `strategies/` 是否需要同步
- 修改了共享逻辑，必须同步到 `shared/` 目录
- 部署前必须确认生产环境代码已更新

### 同步流程

1. 在回测环境验证策略改动
2. 将验证通过的逻辑同步到生产环境代码
3. 更新配置文件中的参数
4. 执行代码同步检查
5. 部署到生产环境

---

## 九、常见问题处理

### 问题1：VERSION 文件不匹配

**症状：** 容器内 VERSION 文件的 DEPLOY_ID 与本地不同

**解决方案：**
1. 确认本地代码是最新的：`git status`
2. 确认部署包是最新的：重新打包
3. 执行强制重新部署：

```bash
ssh root@SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
docker-compose down
docker images | grep PROJECT_NAME | awk '{print $3}' | xargs -r docker rmi --force
docker builder prune -f -a
EOF
./one_click_deploy.sh
```

### 问题2：容器内文件 MD5 不匹配

**可能原因：** 部署包中文件损坏、构建时使用了缓存层、Dockerfile 中 COPY 路径有误

**解决方案：**
1. 检查 Dockerfile 中的 COPY 指令是否正确
2. 检查 .dockerignore 是否过滤了必要文件
3. 重新执行部署（带 --no-cache）

### 问题3：容器未更新

**症状：** 部署后容器运行的还是旧代码

**解决方案：**
```bash
ssh root@SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
docker-compose down
docker rmi IMAGE_NAME:latest --force
docker-compose build --no-cache
docker-compose up -d
EOF
```

### 问题4：文件遗漏

**解决方案：**
1. 检查打包脚本的排除规则
2. 手动上传遗漏的文件
3. 重启容器

### 问题5：【Binance quantitative trading】PostgreSQL 容器命名冲突导致部署中断

**根因：** `.deploy_config` 中的 `POSTGRES_CONTAINER_NAME` 与 docker-compose.yml 中实际的容器名不一致（如 `postgres-db` vs `trading_system-postgres`），导致部署脚本每次都认为 postgres 未运行，尝试 `docker-compose up -d postgres` 时触发命名冲突。

**另一点：** docker-compose 项目名变更时，会产生带前缀的残留容器（如 `b62539d01e8b_trading_system-postgres`），与现有容器名冲突。

**影响：** 部署脚本使用 `set -e`，postgres 启动失败后整个 SSH 脚本退出，**后续所有服务（btc_eth、kline-monitor 等）都不会被启动**，但这些服务的状态不会被报告为"部署失败"。

**解决方案：**
1. 确保 `.deploy_config` 中的 `POSTGRES_CONTAINER_NAME` 与 docker-compose.yml 一致
2. 启动 postgres 前清理所有残留的旧 pg 容器：`docker rm -f $(docker ps -aq -f name=postgres)`
3. 使用 `||` 降级方案：`docker-compose up -d postgres || docker run -d ...` 直接创建

### 问题6：【Binance quantitative trading】部署后缺少容器（如 kline-monitor）

**症状：** 部署完成后，部分容器（如 `trading_system-kline-monitor`）未运行，甚至不存在。

**根因：** 部署脚本中 postgres 启动失败（见问题5），导致 `set -e` 退出 SSH 脚本，排在 postgres 后面的服务全部被跳过。

**注意：** 即使 postgres 启动成功，`docker-compose up -d <service>` 也可能因为 `depends_on` 条件不满足而跳过。例如 `kline_monitor` 依赖 `postgres`，如果 postgres 健康检查未通过，`kline_monitor` 不会被启动。

**解决方案：**
1. 部署完成后，必须执行 `docker ps | grep kline-monitor` 确认所有服务都在运行
2. 如果缺少某个服务，单独启动：`docker-compose up -d kline-monitor`
3. 长期方案：将部署脚本中的 `set -e` 改为对非关键服务不阻断，或使用 `|| true` 降级

---

## 十、部署命令速查

```bash
# 代码同步检查
bash scripts/check_code_sync.sh

# 生成版本标记
cat > VERSION << EOF
DEPLOY_TIME=$(date '+%Y-%m-%d %H:%M:%S')
GIT_COMMIT=$(git log --oneline -1 2>/dev/null || echo "no-git")
DEPLOY_ID=$(uuidgen | cut -d- -f1)
FILE_MD5=$(md5sum strategies/btc_eth/main.py | cut -d' ' -f1)
EOF

# 一键部署
./one_click_deploy.sh

# 验证部署（五层验证）
./verify_deployment.sh

# 生成部署确认报告
./generate_deploy_report.sh

# 查看所有容器状态（确认无遗漏）
ssh root@SERVER_IP "docker ps --format 'table {{.Names}}\t{{.Status}}'"

# 单独检查 kline-monitor 是否存在
ssh root@SERVER_IP "docker ps -f name=kline-monitor --format '{{.Names}} {{.Status}}' || echo '❌ kline-monitor 未运行'"

# 启动缺失的服务（kline-monitor 等）
ssh root@SERVER_IP "cd /root/trading_system && docker-compose up -d kline-monitor"

# 查看容器日志
ssh root@SERVER_IP "docker logs --tail 50 CONTAINER_NAME"

# 容器内代码校验
ssh root@SERVER_IP "docker exec CONTAINER_NAME cat /app/VERSION"
ssh root@SERVER_IP "docker exec CONTAINER_NAME md5sum /app/main.py"
```

---

## 相关技能

- **服务器自动化部署** — 详细的部署流程和脚本
- **通用模块调用指南** — K线服务、通知服务等通用模块的使用

---

**最后更新：** 2026-06-01