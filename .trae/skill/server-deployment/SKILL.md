---
name: "服务器自动化部署"
description: "提供完整的 SSH 免密登录配置、项目打包、上传和 Docker 部署自动化流程。规范服务器目录结构、容器管理和进程管理。Invoke when 部署项目到远程服务器、管理 Docker 容器或需要自动化部署工作流时。"
---

# 服务器自动化部署技能（SSH 免密登录版）

## 🎯 技能说明

本技能提供了一套**以 SSH 免密登录为核心**的标准化部署流程，确保所有服务器连接都使用密钥认证，无需密码。

**核心功能：**
- ✅ **SSH 免密登录配置（必选第一步）**
- ✅ 项目自动打包
- ✅ 使用密钥自动上传到服务器
- ✅ Docker 容器部署与管理
- ✅ 一键部署流程
- ✅ **部署后自动验证** ⭐⭐⭐ (确保容器更新到最新版本)
- ✅ **多重验证机制** ⭐⭐⭐ (版本、状态、健康检查)
- ✅ **服务器目录结构规范** ⭐
- ✅ **容器和进程管理规范** ⭐
- ✅ **PostgreSQL 数据库统一部署** ⭐
- ✅ 故障排查

**适用场景：**
- 新项目首次部署到服务器
- 现有项目更新代码
- 快速重建 Docker 容器
- 批量部署多个服务器
- **需要 SSH 免密登录的所有场景**
- **服务器目录结构规范化** ⭐
- **容器和进程统一管理** ⭐
- **PostgreSQL 数据库部署和多应用共享** ⭐

---

## 📁 服务器部署规范 ⭐

### 核心原则

1. **单一目录原则**: 一个项目在 `/root` 下只能有一个子目录
   - ✅ `/root/trading_system/`
   - ❌ `/root/trading_system_v2/`
   - ❌ `/root/bianace_btcethbnb_trade/` (同一项目的不同命名)

2. **单一容器原则**: 一个项目在 Docker 中只能有一个运行中的容器实例
   - ✅ `trading_system-app` (只有一个实例)
   - ❌ `trading_system-app` + `trading_system-app-old` (同时运行)

3. **规范命名原则**: 目录和容器命名必须规范、清晰
   - ✅ `trading_system`
   - ❌ `trading`, `ts`, `project1`

4. **环境隔离原则**: 开发环境和生产环境严格隔离

### 目录结构示例

```
/root/
├── database/                 # ✅ 数据库目录 (统一管理)
│   └── postgres/             # PostgreSQL 数据库
│       ├── docker-compose.yml
│       ├── init-scripts/     # 初始化脚本
│       ├── scripts/          # 运维脚本
│       └── backups/          # 备份文件
│
├── trading_system/           # ✅ 项目名称 (单一子目录)
│   ├── src/                  # 源代码
│   ├── config/               # 配置文件
│   ├── logs/                 # 日志文件
│   ├── data/                 # 数据文件
│   ├── docker-compose.yml    # Docker 编排
│   ├── Dockerfile            # Docker 镜像
│   └── .env                  # 环境变量
│
├── other_project/            # ✅ 其他项目 (独立子目录)
│   └── ...
│
└── backup/                   # ✅ 备份目录
    └── trading_system_backup_20260320.tar.gz
```

### PostgreSQL 数据库部署规范 ⭐

**核心原则：**
1. **统一数据库**: 所有项目共享一个 PostgreSQL 实例，使用 Schema 隔离
2. **独立部署**: PostgreSQL 容器独立于应用容器，便于管理和备份
3. **Schema 隔离**: 每个项目使用独立的 Schema，权限分离
4. **自动备份**: 配置定时备份任务，确保数据安全

**部署流程：**
```bash
# 1. 创建 PostgreSQL 目录
mkdir -p /root/database/postgres/{init-scripts,scripts,backups}

# 2. 部署 PostgreSQL 容器
cd /root/database/postgres
docker-compose up -d

# 3. 初始化数据库（创建 Schema 和用户）
docker exec -i postgres-db psql -U trading_user -d trading_platform < init-scripts/01-create-schema.sql

# 4. 配置定时备份
cat >> /etc/crontab << 'EOF'
0 2 * * * root cd /root/database/postgres && ./scripts/backup-postgres.sh >> /var/log/postgres_backup.log 2>&1
EOF
```

**应用连接配置：**
```bash
# .env 文件配置示例
DATABASE_URL=postgresql://user:password@postgres:5432/trading_platform?schema=schema_name
```

**注意事项：**
- ✅ PostgreSQL 容器应加入统一网络（trading-network）
- ✅ 应用容器通过 Docker 网络访问 PostgreSQL（使用服务名 `postgres`）
- ✅ 每个项目使用独立的用户和 Schema
- ✅ 定期备份数据库（建议每日凌晨 2 点）
- ✅ 监控数据库资源使用（CPU、内存、磁盘）

### 容器管理示例

```bash
# ✅ 正确：只有一个容器在运行
docker ps
CONTAINER ID   NAMES
abc123         trading_system-app

# ❌ 错误：同一项目的多个容器在运行
docker ps
CONTAINER ID   NAMES
abc123         trading_system-app
def456         trading_system-app-old  # 不应该运行旧版本
```

---

## 📖 标准部署流程

### 第一步：项目目录准备

**确保服务器上的目录结构符合规范**:

```bash
# 1. 检查 /root 下的目录
ssh root@43.156.242.184 "ls -la /root/"

# 2. 确认只有一个项目目录
# ✅ 正确：/root/trading_system/
# ❌ 错误：/root/trading_system_v2/, /root/bianace_btcethbnb_trade/

# 3. 如果有多个目录，清理旧目录
ssh root@43.156.242.184 "rm -rf /root/bianace_btcethbnb_trade"
ssh root@43.156.242.184 "rm -rf /root/trading_system_v2"

# 4. 创建备份目录
ssh root@43.156.242.184 "mkdir -p /root/backup"
```

### 第二步：配置 SSH 免密登录（必须，5 分钟）

**这是部署的前提条件，必须先完成！**

#### 方式一：使用云平台创建的 SSH 密钥（推荐）⭐⭐⭐

**适用场景：** 在腾讯云、阿里云等云平台创建的 SSH 密钥

**步骤：**

1. **下载密钥文件**
   - 在云平台控制台创建 SSH 密钥对
   - 下载私钥文件（通常是 `.pem` 格式）
   - 将密钥文件保存到安全位置，例如：`/Users/yl/vscode/inspection_automation/docs/only.pem`

2. **设置密钥文件权限（必须）**
   ```bash
   # 设置密钥文件权限为 600（只有所有者可读写）
   chmod 600 /Users/yl/vscode/inspection_automation/docs/only.pem
   
   # 验证权限
   ls -la /Users/yl/vscode/inspection_automation/docs/only.pem
   # 应该显示：-rw------- 1 yl staff ... only.pem
   ```

3. **测试密钥登录**
   ```bash
   # 测试登录（不需要输入密码）
   ssh -i /Users/yl/vscode/inspection_automation/docs/only.pem \
       -o StrictHostKeyChecking=no \
       -o UserKnownHostsFile=/dev/null \
       root@43.156.242.184 "echo 'SSH 密钥登录成功！'"
   
   # 如果成功，会直接返回"SSH 密钥登录成功！"，不需要输入密码
   ```

4. **配置 SSH 别名（推荐，简化后续操作）**
   
   编辑 `~/.ssh/config` 文件：
   
   ```bash
   cat >> ~/.ssh/config << 'EOF'
   
   # 生产服务器 - 使用云平台密钥
   Host production
       HostName 43.156.242.184
       User root
       IdentityFile /Users/yl/vscode/inspection_automation/docs/only.pem
       IdentitiesOnly yes
       StrictHostKeyChecking no
       UserKnownHostsFile=/dev/null
       AddKeysToAgent yes
       ServerAliveInterval 60
       ServerAliveCountMax 3
   
   # 简短别名
   Host prod
       HostName 43.156.242.184
       User root
       IdentityFile /Users/yl/vscode/inspection_automation/docs/only.pem
       IdentitiesOnly yes
       StrictHostKeyChecking no
       UserKnownHostsFile=/dev/null
   EOF
   ```
   
   **使用别名登录：**
   ```bash
   ssh prod          # 使用简短别名
   ssh production    # 使用完整别名
   ```

5. **验证配置成功**
   ```bash
   # 检查是否已配置 SSH 密钥
   ssh -o StrictHostKeyChecking=no root@43.156.242.184 "echo 成功"
   
   # 如果不需要输入密码就返回"成功"，说明已配置成功
   ```

**重要提示：**
- ⚠️ 云平台密钥文件路径较长，建议配置 SSH 别名简化操作
- ⚠️ 密钥文件权限必须是 600，否则 SSH 会拒绝使用
- ⚠️ 不要将密钥文件提交到 Git 仓库
- ⚠️ 定期更换密钥以提高安全性

#### ~~方式二：本地生成 SSH 密钥~~（已废弃，不再使用）

**⚠️ 注意：此方式已废弃，请使用"方式一：云平台密钥"**

**适用场景：** 没有云平台密钥，需要本地生成（不推荐）

#### ~~1.1 生成 SSH 密钥~~

```bash
# ❌ 已废弃 - 请使用云平台密钥
# 生成 ED25519 密钥（推荐，更安全）
# ssh-keygen -t ed25519 -C "your_email@example.com"

# 或者使用 RSA 密钥（兼容性更好）
# ssh-keygen -t rsa -b 4096 -C "your_email@example.com"
```

**重要提示：**
- ~~直接回车，**不需要设置密码短语（passphrase）**~~
- ~~生成的私钥：`/Users/yl/vscode/inspection_automation/docs/only.pem`（保密，不要给别人）~~
- ~~生成的公钥：`/Users/yl/vscode/inspection_automation/docs/only.pem.pub`（复制到服务器）~~

#### ~~1.2 复制公钥到服务器~~

```bash
# ❌ 已废弃 - 请使用云平台密钥
# 方法 1：使用 ssh-copy-id（推荐）
# ssh-copy-id -i /Users/yl/vscode/inspection_automation/docs/only.pem.pub -o StrictHostKeyChecking=no root@43.156.242.184

# 方法 2：手动复制（如果 ssh-copy-id 不可用）
# cat /Users/yl/vscode/inspection_automation/docs/only.pem.pub | ssh -o StrictHostKeyChecking=no root@43.156.242.184 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

#### ~~1.3 测试免密登录~~

```bash
# ❌ 已废弃 - 请使用云平台密钥
# 测试登录（不需要输入密码）
# ssh -o StrictHostKeyChecking=no root@43.156.242.184 "echo 成功"

# 如果成功，会直接返回"成功"，不需要输入密码
```

#### ~~1.4 配置 SSH 别名（推荐，简化后续操作）~~

```bash
# ❌ 已废弃 - 请使用云平台密钥
# 编辑 `~/.ssh/config` 文件：

# cat >> ~/.ssh/config << 'EOF'
# 
# # 生产服务器 - 免密登录
# Host production
#     HostName 43.156.242.184
#     User root
#     IdentityFile /Users/yl/vscode/inspection_automation/docs/only.pem
#     IdentitiesOnly yes
#     AddKeysToAgent yes
#     ServerAliveInterval 60
#     ServerAliveCountMax 3
# 
# # 简短别名
# Host prod
#     HostName 43.156.242.184
#     User root
#     IdentityFile /Users/yl/vscode/inspection_automation/docs/only.pem
#     IdentitiesOnly yes
# EOF
```

**~~使用别名登录：~~**
```bash
# ❌ 已废弃
# ssh prod          # 使用简短别名
# ssh production    # 使用完整别名
```

#### ~~1.5 验证配置成功~~

```bash
# ❌ 已废弃 - 请使用云平台密钥
# 检查是否已配置 SSH 密钥
# ssh -o StrictHostKeyChecking=no root@43.156.242.184 "echo 成功"

# 如果不需要输入密码就返回"成功"，说明已配置成功
```

**~~如果失败，查看故障排查章节。~~**

---

### 第三步：创建项目配置文件

在项目根目录创建 `.deploy_config` 文件：

```bash
cat > .deploy_config << 'EOF'
# 服务器配置
SERVER_IP="43.156.242.184"
SERVER_USER="root"
SERVER_PROJECT_PATH="/root/your-project-name"

# Docker 配置
DOCKER_CONTAINER_NAME="your-container-name"
DOCKER_IMAGE_NAME="your-image-name:latest"

# 项目配置
PROJECT_NAME="your-project-name"
DEPLOY_PACKAGE_NAME="deployment_package.tar.gz"
EOF
```

**必要文件检查：**

确保项目根目录包含以下文件：
```
your-project/
├── Dockerfile              # Docker 镜像构建文件
├── docker-compose.yml      # Docker Compose 配置
├── deploy.sh              # 部署脚本
├── requirements.txt       # Python 依赖（或其他语言的依赖文件）
├── .env                   # 环境变量文件
└── .deploy_config         # 部署配置
```

### PostgreSQL 数据库配置 ⭐

**如果项目使用 PostgreSQL 数据库，需要额外配置：**

1. **更新 .env 文件**：
```bash
# PostgreSQL 连接字符串
DATABASE_URL=postgresql://user:password@postgres:5432/trading_platform?schema=schema_name
```

2. **更新 docker-compose.yml**：
```yaml
services:
  app:
    # ...
    depends_on:
      - postgres
    networks:
      - trading-network
    environment:
      - DATABASE_URL=postgresql://user:password@postgres:5432/trading_platform?schema=schema_name

networks:
  trading-network:
    external: true  # 使用外部网络（PostgreSQL 已创建）
```

3. **安装数据库驱动**：
```bash
# Python 项目
pip install psycopg2-binary

# 或者异步驱动
pip install asyncpg
```

4. **数据库初始化**：
- 如果是新项目，PostgreSQL 会自动创建表结构
- 如果是迁移项目，需要从旧数据库导出数据并导入 PostgreSQL

---

### 第四步：创建自动打包脚本（增强版 - 防止文件遗漏）

在项目根目录创建 `auto_package.sh`：

```bash
#!/bin/bash

# ============================================
# 自动化打包脚本（增强版 - 防止文件遗漏）
# ============================================

set -e  # 遇到错误立即退出

# 加载配置
if [ -f ".deploy_config" ]; then
    source .deploy_config
    echo "✅ 已加载部署配置"
else
    echo "❌ 错误：.deploy_config 文件不存在"
    exit 1
fi

# 设置默认值
DEPLOY_PACKAGE_NAME=${DEPLOY_PACKAGE_NAME:-"deployment_package.tar.gz"}
PROJECT_NAME=${PROJECT_NAME:-$(basename "$(pwd)")}

echo "============================================="
echo "开始打包项目：$PROJECT_NAME"
echo "============================================="

# 创建临时目录
TEMP_DIR="/tmp/${PROJECT_NAME}_deploy_$$"
echo "📁 创建临时目录：$TEMP_DIR"
mkdir -p "$TEMP_DIR"

# 复制项目文件（排除不需要的文件）
echo "📋 复制项目文件..."
rsync -av --delete \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='*.pyo' \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='logs/*' \
    --exclude='data/*' \
    --exclude='reports/*' \
    --exclude='*.tar.gz' \
    --exclude='.DS_Store' \
    --exclude='._*' \
    --exclude='node_modules/*' \
    --exclude='.env.local' \
    --exclude='.trae/*' \
    --exclude='*.log' \
    --exclude='tmp/*' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='.coverage' \
    --exclude='htmlcov/*' \
    ./ "$TEMP_DIR/"

# 创建压缩包
echo "📦 创建压缩包..."
cd "$TEMP_DIR"
tar -czf "$OLDPWD/$DEPLOY_PACKAGE_NAME" .

# 清理临时目录
cd "$OLDPWD"
rm -rf "$TEMP_DIR"

# 显示结果
PACKAGE_SIZE=$(ls -lh "$DEPLOY_PACKAGE_NAME" | awk '{print $5}')
echo "============================================="
echo "✅ 打包完成！"
echo "📦 压缩包：$DEPLOY_PACKAGE_NAME"
echo "📊 大小：$PACKAGE_SIZE"
echo "============================================="

# 新增：文件完整性检查 ⭐⭐⭐
echo ""
echo "🔍 执行文件完整性检查..."

# 统计本地文件数量（排除相同规则）
LOCAL_FILE_COUNT=$(find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | wc -l)

# 解压压缩包并统计文件数量
VERIFY_DIR="/tmp/${PROJECT_NAME}_verify_$$"
mkdir -p "$VERIFY_DIR"
tar -xzf "$DEPLOY_PACKAGE_NAME" -C "$VERIFY_DIR"

PACKAGE_FILE_COUNT=$(find "$VERIFY_DIR" -type f | wc -l)
rm -rf "$VERIFY_DIR"

echo "📊 本地文件数量：$LOCAL_FILE_COUNT"
echo "📦 压缩包文件数量：$PACKAGE_FILE_COUNT"

# 计算差异（允许一定误差，因为 find 和 rsync 的统计方式可能略有不同）
DIFF=$((LOCAL_FILE_COUNT - PACKAGE_FILE_COUNT))
if [ $DIFF -lt 0 ]; then
    DIFF=$((-DIFF))
fi

# 如果差异超过 5 个文件，发出警告
if [ $DIFF -gt 5 ]; then
    echo "⚠️  警告：文件数量差异较大（差异：$DIFF 个文件）"
    echo "   可能遗漏了文件，请检查排除规则！"
    echo ""
    echo "   本地文件列表（前 20 个）："
    find . -type f \
        ! -path './.git/*' \
        ! -path './logs/*' \
        ! -path './data/*' \
        ! -path './reports/*' \
        ! -path './node_modules/*' \
        ! -path './.pytest_cache/*' \
        ! -path './.mypy_cache/*' \
        ! -path './htmlcov/*' \
        ! -path './tmp/*' \
        ! -name '*.pyc' \
        ! -name '*.pyo' \
        ! -name '*.tar.gz' \
        ! -name '*.log' \
        ! -name '.DS_Store' \
        ! -name '._*' \
        ! -name '.env.local' \
        ! -path './.trae/*' \
        | head -20
    echo ""
    echo "   请确认是否有重要文件被排除规则过滤！"
    read -p "是否继续？(y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "❌ 打包已取消"
        exit 1
    fi
else
    echo "✅ 文件数量检查通过（差异：$DIFF 个文件，在允许范围内）"
fi

echo ""
echo "============================================="
echo "🎉 打包完成并通过完整性检查！"
echo "============================================="
```
```

**执行打包：**
```bash
chmod +x auto_package.sh
./auto_package.sh
```

---

### 第五步：创建上传脚本（使用 SSH 密钥）

创建 `upload_to_server.sh`：

```bash
#!/bin/bash

# ============================================
# 上传脚本 - 使用 SSH 密钥认证
# ============================================

set -e

# 加载配置
source .deploy_config

echo "============================================="
echo "上传到服务器：$SERVER_IP"
echo "============================================="

# 检查压缩包是否存在
if [ ! -f "$DEPLOY_PACKAGE_NAME" ]; then
    echo "❌ 错误：压缩包不存在，请先执行打包"
    exit 1
fi

# 使用 SSH 密钥认证（推荐）
echo "🔑 使用 SSH 密钥认证..."

# 测试 SSH 密钥是否可用
if ssh -o StrictHostKeyChecking=no -o BatchMode=yes "$SERVER_USER@$SERVER_IP" "echo 密钥可用" 2>/dev/null; then
    echo "✅ SSH 密钥可用，开始上传..."
    
    scp -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        "$DEPLOY_PACKAGE_NAME" \
        "$SERVER_USER@$SERVER_IP:/root/"
    
    echo "✅ 上传成功（SSH 密钥）"
else
    echo "❌ 错误：SSH 密钥不可用，请先配置免密登录"
    echo ""
    echo "请执行以下步骤："
    echo "1. 确保云平台密钥文件存在：/Users/yl/vscode/inspection_automation/docs/only.pem"
    echo "2. 设置密钥权限：chmod 600 /Users/yl/vscode/inspection_automation/docs/only.pem"
    echo "3. 测试密钥登录：ssh -i /Users/yl/vscode/inspection_automation/docs/only.pem root@$SERVER_IP 'echo 成功'"
    exit 1
fi

echo "============================================="
```

**执行上传：**
```bash
chmod +x upload_to_server.sh
./upload_to_server.sh
```

---

### 第六步：创建一键部署脚本（增强版 - 确保容器更新）

创建 `one_click_deploy.sh`：

```bash
#!/bin/bash

# ============================================
# 一键部署脚本（增强版 - 确保容器更新到最新版本）
# ============================================

set -e

# 加载配置
source .deploy_config

echo "============================================="
echo "一键部署 - $PROJECT_NAME"
echo "目标服务器：$SERVER_IP"
echo "============================================="

# 步骤 1：打包
echo "📦 步骤 1/5: 打包项目..."
./auto_package.sh

# 步骤 2：上传
echo "📤 步骤 2/5: 上传到服务器..."
./upload_to_server.sh

# 步骤 3：远程部署
echo "🚀 步骤 3/5: 远程部署..."

# 使用 SSH 执行远程部署命令（使用密钥认证）
ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$SERVER_USER@$SERVER_IP" << 'ENDSSH'
    
# 在服务器上执行的命令
set -e

echo "============================================="
echo "远程部署开始"
echo "============================================="

# 1. 停止并删除旧容器
echo "🛑 停止旧容器..."
if docker ps -q -f name=$DOCKER_CONTAINER_NAME | grep -q .; then
    docker stop $DOCKER_CONTAINER_NAME
    echo "✅ 容器已停止"
else
    echo "⚠️  容器未运行，跳过停止"
fi

echo "🗑️  删除旧容器..."
if docker ps -aq -f name=$DOCKER_CONTAINER_NAME | grep -q .; then
    docker rm $DOCKER_CONTAINER_NAME
    echo "✅ 容器已删除"
else
    echo "⚠️  容器不存在，跳过删除"
fi

# 2. 删除旧镜像（关键步骤，防止使用缓存）⭐⭐⭐
echo "🗑️  删除旧镜像（防止使用缓存）..."
if docker images -q $DOCKER_IMAGE_NAME | grep -q .; then
    docker rmi $DOCKER_IMAGE_NAME --force 2>/dev/null || true
    echo "✅ 旧镜像已删除"
else
    echo "⚠️  旧镜像不存在，跳过删除"
fi

# 3. 解压新包
echo "📦 解压新代码包..."
cd /root
tar -xzf $DEPLOY_PACKAGE_NAME -C $PROJECT_NAME
echo "✅ 代码包已解压"

# 4. 设置权限
cd $PROJECT_NAME
chmod +x deploy.sh 2>/dev/null || true
chmod 600 .env 2>/dev/null || true
echo "✅ 权限已设置"

# 5. 清理 Docker 缓存（可选，如果磁盘空间紧张）
echo "🧹 清理 Docker 悬空镜像..."
docker image prune -f --filter "until=24h" 2>/dev/null || true

# 6. 构建并启动（不使用缓存）
echo "🏗️  构建 Docker 镜像（不使用缓存）..."
docker-compose build --no-cache
if [ $? -ne 0 ]; then
    echo "❌ Docker 构建失败！"
    exit 1
fi
echo "✅ Docker 镜像构建成功"

echo "🚀 启动 Docker 容器..."
docker-compose up -d
if [ $? -ne 0 ]; then
    echo "❌ Docker 容器启动失败！"
    exit 1
fi
echo "✅ Docker 容器启动成功"

# 7. 等待容器启动
echo "⏳ 等待容器启动..."
sleep 5

# 8. 显示状态
echo "============================================="
echo "容器状态:"
docker ps -f name=$DOCKER_CONTAINER_NAME
echo "============================================="
echo "最近日志:"
docker logs --tail 30 $DOCKER_CONTAINER_NAME
echo "============================================="
ENDSSH

# 检查远程部署是否成功
if [ $? -ne 0 ]; then
    echo "❌ 远程部署失败！"
    exit 1
fi

# 步骤 4：验证部署（关键步骤，确保容器更新到最新版本）⭐⭐⭐
echo "✅ 步骤 4/5: 验证部署（关键步骤，确保容器更新到最新版本）..."

# 创建验证脚本并上传
cat > /tmp/verify_deployment.sh << 'VERIFYEOF'
#!/bin/bash

DOCKER_CONTAINER_NAME="$1"
PROJECT_NAME="$2"

echo "============================================="
echo "🔍 部署验证 - 确保容器更新到最新版本"
echo "============================================="

# 1. 验证容器是否在运行
echo "1️⃣  验证容器运行状态..."
CONTAINER_STATUS=$(docker ps -f name=$DOCKER_CONTAINER_NAME --format '{{.Status}}')
if [ -z "$CONTAINER_STATUS" ]; then
    echo "❌ 容器未运行！部署失败！"
    exit 1
fi
echo "✅ 容器运行状态：$CONTAINER_STATUS"

# 2. 验证容器镜像版本（关键）⭐⭐⭐
echo ""
echo "2️⃣  验证容器镜像版本（确保是最新版本）..."
CONTAINER_IMAGE=$(docker inspect -f '{{.Config.Image}}' $DOCKER_CONTAINER_NAME 2>/dev/null)
IMAGE_CREATED=$(docker inspect -f '{{.Created}}' $DOCKER_CONTAINER_NAME 2>/dev/null)
echo "   容器镜像：$CONTAINER_IMAGE"
echo "   镜像创建时间：$IMAGE_CREATED"

# 获取本地最新镜像
LOCAL_IMAGE=$(docker images --format "{{.Repository}}:{{.Tag}}" | grep $PROJECT_NAME | head -1)
LOCAL_IMAGE_CREATED=$(docker inspect -f '{{.Created}}' $LOCAL_IMAGE 2>/dev/null)
echo "   本地最新镜像：$LOCAL_IMAGE"
echo "   本地镜像创建时间：$LOCAL_IMAGE_CREATED"

# 比较镜像创建时间
if [ "$IMAGE_CREATED" = "$LOCAL_IMAGE_CREATED" ]; then
    echo "✅ 容器使用的是最新镜像版本"
else
    # 检查时间差（300 秒 = 5 分钟）
    IMAGE_TIMESTAMP=$(date -d "$IMAGE_CREATED" +%s 2>/dev/null || echo "0")
    LOCAL_TIMESTAMP=$(date -d "$LOCAL_IMAGE_CREATED" +%s 2>/dev/null || echo "0")
    TIME_DIFF=$((LOCAL_TIMESTAMP - IMAGE_TIMESTAMP))
    
    if [ $TIME_DIFF -lt 0 ]; then
        TIME_DIFF=$((-TIME_DIFF))
    fi
    
    if [ $TIME_DIFF -le 300 ]; then
        echo "✅ 容器使用的是最新镜像版本（时间差：${TIME_DIFF}秒）"
    else
        echo "⚠️  警告：容器可能未使用最新镜像！"
        echo "   容器镜像创建时间：$IMAGE_CREATED"
        echo "   最新镜像创建时间：$LOCAL_IMAGE_CREATED"
        echo "   时间差：${TIME_DIFF}秒"
        exit 1
    fi
fi

# 3. 验证容器健康状态
echo ""
echo "3️⃣  验证容器健康状态..."
HEALTH_STATUS=$(docker inspect -f '{{.State.Health.Status}}' $DOCKER_CONTAINER_NAME 2>/dev/null || echo "无健康检查")
if [ "$HEALTH_STATUS" = "healthy" ] || [ "$HEALTH_STATUS" = "无健康检查" ]; then
    echo "✅ 容器健康状态：$HEALTH_STATUS"
else
    echo "⚠️  容器健康状态：$HEALTH_STATUS"
fi

# 4. 验证容器端口映射
echo ""
echo "4️⃣  验证容器端口映射..."
PORT_MAPPINGS=$(docker port $DOCKER_CONTAINER_NAME 2>/dev/null)
if [ -n "$PORT_MAPPINGS" ]; then
    echo "✅ 端口映射配置："
    echo "$PORT_MAPPINGS" | sed 's/^/   /'
else
    echo "⚠️  无端口映射或容器未运行"
fi

# 5. 验证容器日志（检查是否有启动错误）
echo ""
echo "5️⃣  验证容器日志（检查启动错误）..."
ERROR_COUNT=$(docker logs --tail 100 $DOCKER_CONTAINER_NAME 2>&1 | grep -i "error\|exception\|fatal" | wc -l)
if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "⚠️  发现 $ERROR_COUNT 个错误日志，请检查："
    docker logs --tail 20 $DOCKER_CONTAINER_NAME
    exit 1
else
    echo "✅ 未发现明显错误日志"
fi

# 6. 验证容器资源使用
echo ""
echo "6️⃣  验证容器资源使用..."
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" $DOCKER_CONTAINER_NAME

# 7. 最终验证总结
echo ""
echo "============================================="
echo "📋 验证总结"
echo "============================================="
echo "容器名称：$DOCKER_CONTAINER_NAME"
echo "运行状态：$CONTAINER_STATUS"
echo "镜像版本：$CONTAINER_IMAGE"
echo "健康状态：$HEALTH_STATUS"
echo "错误日志：$ERROR_COUNT 个"
echo "============================================="

if [ "$ERROR_COUNT" -eq 0 ] && [ -n "$CONTAINER_STATUS" ]; then
    echo "✅ 验证通过！容器已成功更新到最新版本！"
    exit 0
else
    echo "❌ 验证失败！请检查上述错误！"
    exit 1
fi
VERIFYEOF

chmod +x /tmp/verify_deployment.sh

# 上传验证脚本到服务器
scp -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    /tmp/verify_deployment.sh \
    "$SERVER_USER@$SERVER_IP:/tmp/verify_deployment.sh"

# 在服务器上执行验证
ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$SERVER_USER@$SERVER_IP" \
    "bash /tmp/verify_deployment.sh '$DOCKER_CONTAINER_NAME' '$PROJECT_NAME'"

# 检查验证结果
if [ $? -eq 0 ]; then
    echo "============================================="
    echo "🎉 一键部署完成！验证通过！"
    echo "============================================="
else
    echo "============================================="
    echo "⚠️  部署完成但验证失败！请检查上述错误！"
    echo "============================================="
    echo ""
    echo "🔧 建议执行以下命令重新部署："
    echo ""
    echo "ssh root@$SERVER_IP << 'EOF'"
    echo "cd /root/$PROJECT_NAME"
    echo "docker-compose down"
    echo "docker rmi $DOCKER_IMAGE_NAME --force"
    echo "docker-compose build --no-cache"
    echo "docker-compose up -d"
    echo "EOF"
    echo ""
    exit 1
fi

# 步骤 5：清理临时文件
echo ""
echo "📤 步骤 5/5: 清理临时文件..."
rm -f /tmp/verify_deployment.sh
ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$SERVER_USER@$SERVER_IP" \
    "rm -f /tmp/verify_deployment.sh /root/$DEPLOY_PACKAGE_NAME"
echo "✅ 临时文件已清理"

echo ""
echo "============================================="
echo "🎉 部署全部完成！"
echo "============================================="
```

**执行一键部署：**
```bash
chmod +x one_click_deploy.sh
./one_click_deploy.sh
```

---

## 🔍 部署后验证（关键步骤）⭐⭐⭐

**重要提示：** 部署完成后必须进行验证，确保容器已更新到最新版本！

### 验证脚本

创建 `verify_deployment.sh`：

```bash
#!/bin/bash

# ============================================
# 部署验证脚本 - 确保容器更新到最新版本
# ============================================

source .deploy_config

echo "============================================="
echo "🔍 部署验证 - 确保容器更新到最新版本"
echo "============================================="

# 在服务器上执行验证命令
ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" << ENDSSH

DOCKER_CONTAINER_NAME="$DOCKER_CONTAINER_NAME"
PROJECT_NAME="$PROJECT_NAME"

echo "============================================="
echo "验证步骤："
echo "1. 容器运行状态"
echo "2. 镜像版本验证（关键）"
echo "3. 健康状态检查"
echo "4. 端口映射验证"
echo "5. 日志错误检查"
echo "6. 资源使用检查"
echo "============================================="

# 1. 验证容器是否在运行
echo ""
echo "1️⃣  验证容器运行状态..."
CONTAINER_STATUS=$(docker ps -f name=\$DOCKER_CONTAINER_NAME --format '{{.Status}}')
if [ -z "\$CONTAINER_STATUS" ]; then
    echo "❌ 容器未运行！部署失败！"
    exit 1
fi
echo "✅ 容器运行状态：\$CONTAINER_STATUS"

# 2. 验证容器镜像版本（关键）⭐⭐⭐
echo ""
echo "2️⃣  验证容器镜像版本（确保是最新版本）..."
CONTAINER_IMAGE=\$(docker inspect -f '{{.Config.Image}}' \$DOCKER_CONTAINER_NAME 2>/dev/null)
IMAGE_CREATED=\$(docker inspect -f '{{.Created}}' \$DOCKER_CONTAINER_NAME 2>/dev/null)
echo "   容器镜像：\$CONTAINER_IMAGE"
echo "   镜像创建时间：\$IMAGE_CREATED"

# 获取服务器上最新镜像
LATEST_IMAGE=\$(docker images --format "{{.Repository}}:{{.Tag}}" | grep \$PROJECT_NAME | head -1)
LATEST_IMAGE_CREATED=\$(docker inspect -f '{{.Created}}' \$LATEST_IMAGE 2>/dev/null)
echo "   服务器最新镜像：\$LATEST_IMAGE"
echo "   最新镜像创建时间：\$LATEST_IMAGE_CREATED"

# 比较镜像创建时间
if [ "\$IMAGE_CREATED" = "\$LATEST_IMAGE_CREATED" ]; then
    echo "✅ 容器使用的是最新镜像版本"
else
    echo "⚠️  警告：容器可能未使用最新镜像！"
    echo "   容器镜像创建时间：\$IMAGE_CREATED"
    echo "   最新镜像创建时间：\$LATEST_IMAGE_CREATED"
    echo ""
    echo "   建议重新部署："
    echo "   cd /root/\$PROJECT_NAME && docker-compose down && docker-compose build --no-cache && docker-compose up -d"
fi

# 3. 验证容器健康状态
echo ""
echo "3️⃣  验证容器健康状态..."
HEALTH_STATUS=\$(docker inspect -f '{{.State.Health.Status}}' \$DOCKER_CONTAINER_NAME 2>/dev/null || echo "无健康检查")
if [ "\$HEALTH_STATUS" = "healthy" ] || [ "\$HEALTH_STATUS" = "无健康检查" ]; then
    echo "✅ 容器健康状态：\$HEALTH_STATUS"
else
    echo "⚠️  容器健康状态：\$HEALTH_STATUS"
fi

# 4. 验证容器端口映射
echo ""
echo "4️⃣  验证容器端口映射..."
PORT_MAPPINGS=\$(docker port \$DOCKER_CONTAINER_NAME 2>/dev/null)
if [ -n "\$PORT_MAPPINGS" ]; then
    echo "✅ 端口映射配置："
    echo "\$PORT_MAPPINGS" | sed 's/^/   /'
else
    echo "⚠️  无端口映射或容器未运行"
fi

# 5. 验证容器日志（检查是否有启动错误）
echo ""
echo "5️⃣  验证容器日志（检查启动错误）..."
ERROR_COUNT=\$(docker logs --tail 100 \$DOCKER_CONTAINER_NAME 2>&1 | grep -i "error\|exception\|fatal" | wc -l)
if [ "\$ERROR_COUNT" -gt 0 ]; then
    echo "⚠️  发现 \$ERROR_COUNT 个错误日志，请检查："
    docker logs --tail 20 \$DOCKER_CONTAINER_NAME
else
    echo "✅ 未发现明显错误日志"
fi

# 6. 验证容器资源使用
echo ""
echo "6️⃣  验证容器资源使用..."
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" \$DOCKER_CONTAINER_NAME

# 7. 最终验证总结
echo ""
echo "============================================="
echo "📋 验证总结"
echo "============================================="
echo "容器名称：\$DOCKER_CONTAINER_NAME"
echo "运行状态：\$CONTAINER_STATUS"
echo "镜像版本：\$CONTAINER_IMAGE"
echo "健康状态：\$HEALTH_STATUS"
echo "错误日志：\$ERROR_COUNT 个"
echo "============================================="

if [ "\$ERROR_COUNT" -eq 0 ] && [ -n "\$CONTAINER_STATUS" ]; then
    echo "✅ 验证通过！容器已成功更新到最新版本！"
    exit 0
else
    echo "❌ 验证失败！请检查上述错误！"
    exit 1
fi
ENDSSH

# 检查验证结果
if [ $? -eq 0 ]; then
    echo "============================================="
    echo "🎉 验证完成！部署成功！"
    echo "============================================="
else
    echo "============================================="
    echo "❌ 验证失败！请检查上述错误！"
    echo "============================================="
    exit 1
fi
```

**执行验证：**
```bash
chmod +x verify_deployment.sh
./verify_deployment.sh
```

### 快速验证命令

```bash
# 1. 快速检查容器状态
ssh root@SERVER_IP "docker ps -f name=CONTAINER_NAME"

# 2. 检查镜像版本
ssh root@SERVER_IP "docker inspect -f '{{.Config.Image}}' CONTAINER_NAME"

# 3. 检查镜像创建时间
ssh root@SERVER_IP "docker inspect -f '{{.Created}}' CONTAINER_NAME"

# 4. 查看最新日志
ssh root@SERVER_IP "docker logs --tail 30 CONTAINER_NAME"

# 5. 检查容器健康状态
ssh root@SERVER_IP "docker inspect -f '{{.State.Health.Status}}' CONTAINER_NAME"
```

### 验证清单 ⭐⭐⭐

部署后必须检查以下项目：

- [ ] **容器运行状态**：容器是否在运行
- [ ] **镜像版本**：容器使用的是最新镜像（检查 Created 时间）
- [ ] **健康状态**：容器健康检查是否通过
- [ ] **端口映射**：端口是否正确映射
- [ ] **日志检查**：无启动错误或异常
- [ ] **资源使用**：CPU、内存使用正常
- [ ] **功能测试**：应用功能正常

---

## 🔧 Docker 容器管理

创建 `docker_manage.sh`：

```bash
#!/bin/bash

# ============================================
# Docker 容器管理脚本
# ============================================

source .deploy_config

show_menu() {
    echo "============================================="
    echo "Docker 容器管理 - $DOCKER_CONTAINER_NAME"
    echo "============================================="
    echo "1. 查看容器状态"
    echo "2. 查看实时日志"
    echo "3. 重启容器"
    echo "4. 停止容器"
    echo "5. 启动容器"
    echo "6. 删除容器"
    echo "7. 重新构建并部署"
    echo "8. 进入容器终端"
    echo "9. 查看资源使用"
    echo "0. 退出"
    echo "============================================="
}

check_status() {
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker ps -a -f name=$DOCKER_CONTAINER_NAME"
}

view_logs() {
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker logs -f --tail 100 $DOCKER_CONTAINER_NAME"
}

restart_container() {
    echo "🔄 重启容器..."
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker restart $DOCKER_CONTAINER_NAME"
    echo "✅ 重启完成"
}

stop_container() {
    echo "🛑 停止容器..."
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker stop $DOCKER_CONTAINER_NAME"
    echo "✅ 停止完成"
}

start_container() {
    echo "🚀 启动容器..."
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker start $DOCKER_CONTAINER_NAME"
    echo "✅ 启动完成"
}

delete_container() {
    echo "⚠️  警告：此操作将删除容器！"
    read -p "确定要继续吗？(y/N): " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
            "docker stop $DOCKER_CONTAINER_NAME && docker rm $DOCKER_CONTAINER_NAME"
        echo "✅ 删除完成"
    else
        echo "❌ 操作已取消"
    fi
}

rebuild_deploy() {
    echo "🏗️  重新构建并部署..."
    ./one_click_deploy.sh
}

enter_container() {
    echo "🔌 进入容器终端..."
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker exec -it $DOCKER_CONTAINER_NAME /bin/bash"
}

view_resources() {
    echo "📊 资源使用情况:"
    ssh -o StrictHostKeyChecking=no "$SERVER_USER@$SERVER_IP" \
        "docker stats --no-stream $DOCKER_CONTAINER_NAME"
}

# 主循环
while true; do
    show_menu
    read -p "请选择操作 [0-9]: " choice
    
    case $choice in
        1) check_status ;;
        2) view_logs ;;
        3) restart_container ;;
        4) stop_container ;;
        5) start_container ;;
        6) delete_container ;;
        7) rebuild_deploy ;;
        8) enter_container ;;
        9) view_resources ;;
        0) echo "👋 退出"; exit 0 ;;
        *) echo "❌ 无效选择" ;;
    esac
    
    echo ""
    read -p "按回车键继续..."
done
```

---

## 🔍 故障排查

### 问题 1：SSH 密钥配置后仍然需要密码 ⭐ 重要

**症状：** 配置了 SSH 密钥，但登录时仍然提示输入密码，即使公钥已正确添加到 `~/.ssh/authorized_keys`

**常见原因：**
1. `/root` 目录的所有权不正确（**最常见**）
2. `~/.ssh` 或 `~/.ssh/authorized_keys` 权限不正确
3. 公钥内容不正确或格式有问题
4. SSH 配置限制了公钥认证

**解决方案：**

#### 1. 检查并修复 `/root` 目录所有权（**最重要**）

```bash
# 检查 /root 目录的所有权和权限
ssh root@SERVER_IP "ls -ld /root"

# ❌ 错误示例：drwxr-xr-x 27 501 games 8192 Mar 23 17:02 /root
# ✅ 正确示例：drwx------ 27 root root 8192 Mar 23 17:02 /root

# 如果所有者不是 root:root，修复它
ssh root@SERVER_IP "chown root:root /root && chmod 700 /root"

# 验证修复
ssh root@SERVER_IP "ls -ld /root"
# 应该显示：drwx------ 27 root root 8192 ... /root
```

**为什么这很重要：** SSH 对主目录的所有权和权限有严格要求。如果 `/root` 目录不是 `root:root` 所有或权限不是 `700`，SSH 会拒绝使用公钥认证，并在日志中记录：
```
Authentication refused: bad ownership or modes for directory /root
```

#### 2. 检查 SSH 密钥和 authorized_keys 权限

```bash
# 检查 .ssh 目录权限
ssh root@SERVER_IP "ls -la ~/.ssh/"

# 修复权限（必须严格）
ssh root@SERVER_IP "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"

# 检查 authorized_keys 内容
ssh root@SERVER_IP "cat ~/.ssh/authorized_keys"
```

#### 3. 验证公钥指纹匹配

```bash
# 检查云平台密钥公钥指纹
ssh-keygen -lf /Users/yl/vscode/inspection_automation/docs/only.pem

# 检查服务器上的公钥指纹
ssh root@SERVER_IP "ssh-keygen -lf ~/.ssh/authorized_keys"

# 两个指纹必须完全相同！
```

#### 4. 使用详细模式调试

```bash
# 使用 -vvv 查看详细调试信息
ssh -vvv -o StrictHostKeyChecking=no -o IdentitiesOnly=yes -i /Users/yl/vscode/inspection_automation/docs/only.pem root@SERVER_IP "echo test"

# 查看关键信息：
# - "Offering public key" - 确认正在提供正确的密钥
# - "type 51" - 服务器拒绝密钥
# - "Authenticated" - 认证成功
```

#### 5. 检查服务器 SSH 日志

```bash
# 查看最近的 SSH 认证日志
ssh root@SERVER_IP "sudo journalctl -u sshd --since '5 minutes ago' --no-pager | grep -i 'publickey\|authorized\|refused'"

# 查找关键错误：
# - "Authentication refused: bad ownership or modes"
# - "Failed publickey"
# - "Accepted publickey"
```

#### 6. 重新配置 SSH 密钥（如果以上都失败）

**注意：云平台密钥需要在云平台控制台重新绑定到服务器，不能使用 ssh-copy-id**

```bash
# 1. 在云平台控制台确认密钥已绑定到服务器
# 2. 检查密钥文件权限
chmod 600 /Users/yl/vscode/inspection_automation/docs/only.pem

# 3. 测试密钥登录
ssh -i /Users/yl/vscode/inspection_automation/docs/only.pem root@SERVER_IP "echo SSH 密钥登录成功！"
```

---

### 问题 2：文件打包遗漏 ⭐⭐⭐ 重要

**症状：** 部署后发现某些文件未上传到服务器

**常见原因：**
1. 文件被排除规则过滤
2. 文件权限问题导致无法读取
3. 符号链接未正确处理
4. 隐藏文件被忽略
5. 文件名包含特殊字符

**解决方案：**

#### 1. 检查排除规则

```bash
# 查看 auto_package.sh 中的排除规则
cat auto_package.sh | grep exclude

# 常见被误排除的文件：
# - .env (应该保留)
# - .gitignore (应该排除)
# - *.env (可能被误排除)
```

**建议：** 在打包前，先运行以下命令查看会被排除的文件：

```bash
# 模拟 rsync 的排除规则
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | sort > /tmp/local_files.txt

# 查看文件列表
cat /tmp/local_files.txt

# 检查是否有重要文件被排除
grep -E '\.(py|yml|yaml|json|env|sh|md)$' /tmp/local_files.txt
```

#### 2. 验证压缩包完整性

```bash
# 解压压缩包到临时目录
mkdir -p /tmp/verify_package
tar -xzf deployment_package.tar.gz -C /tmp/verify_package

# 统计文件数量
echo "本地文件数量："
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | wc -l

echo "压缩包文件数量："
find /tmp/verify_package -type f | wc -l

# 对比差异
diff -r <(find . -type f | grep -v '.git' | sort) \
         <(find /tmp/verify_package -type f | sed 's|/tmp/verify_package/||' | sort) \
    || true

# 清理
rm -rf /tmp/verify_package
```

#### 3. 检查符号链接

```bash
# 查找项目中的符号链接
find . -type l

# 如果有符号链接，需要在 rsync 中添加 -L 参数
# 或者将符号链接指向的文件包含到压缩包中
```

#### 4. 检查文件权限

```bash
# 查找无法读取的文件
find . -type f ! -readable

# 修复文件权限
chmod -R u+r .

# 查找特殊权限的文件
find . -type f -perm /7000
```

#### 5. 手动添加被遗漏的文件

如果确认某些文件被遗漏，可以：

```bash
# 方法 1：修改排除规则
# 编辑 auto_package.sh，移除对应该文件的排除规则

# 方法 2：手动上传遗漏的文件
scp path/to/missing/file root@SERVER_IP:/root/project/path/to/missing/file

# 方法 3：创建补充包
tar -czf missing_files.tar.gz path/to/missing/file
scp missing_files.tar.gz root@SERVER_IP:/root/
ssh root@SERVER_IP "cd /root/project && tar -xzf ../missing_files.tar.gz"
rm missing_files.tar.gz
```

---

### 问题 3：容器未使用最新代码 ⭐⭐⭐ 重要

**症状：** 部署完成后，容器内运行的还是旧代码

**常见原因：**
1. 旧镜像未删除，Docker 使用了缓存
2. docker-compose build 失败但未被检测
3. 容器启动失败，旧容器仍在运行
4. 多个容器同时运行
5. 镜像标签未更新

**解决方案：**

#### 1. 检查镜像版本

```bash
# 查看容器使用的镜像
ssh root@SERVER_IP "docker inspect -f '{{.Config.Image}}' CONTAINER_NAME"

# 查看服务器上的镜像列表
ssh root@SERVER_IP "docker images | grep PROJECT_NAME"

# 查看镜像创建时间
ssh root@SERVER_IP "docker inspect -f '{{.Created}}' CONTAINER_NAME"
ssh root@SERVER_IP "docker inspect -f '{{.Created}}' IMAGE_ID"

# 如果时间不一致，说明容器未使用最新镜像
```

#### 2. 强制重新部署

```bash
# 完全清理并重新部署
ssh root@SERVER_IP << 'EOF'
cd /root/PROJECT_NAME

# 停止并删除容器
docker-compose down

# 删除所有相关镜像
docker images | grep PROJECT_NAME | awk '{print $3}' | xargs docker rmi --force

# 清理构建缓存
docker builder prune -f

# 重新构建（不使用缓存）
docker-compose build --no-cache

# 启动新容器
docker-compose up -d

# 验证
docker ps -f name=CONTAINER_NAME
EOF
```

#### 3. 检查构建日志

```bash
# 查看构建过程
ssh root@SERVER_IP "cd /root/PROJECT_NAME && docker-compose build --no-cache"

# 检查是否有构建错误
ssh root@SERVER_IP "docker images | grep PROJECT_NAME"

# 如果镜像不存在，说明构建失败
```

#### 4. 检查容器状态

```bash
# 查看所有容器（包括已停止的）
ssh root@SERVER_IP "docker ps -a -f name=CONTAINER_NAME"

# 如果有多个容器，删除旧的
ssh root@SERVER_IP "docker ps -aq -f name=CONTAINER_NAME | tail -n +2 | xargs docker rm -f"

# 确保只有一个容器在运行
ssh root@SERVER_IP "docker ps -f name=CONTAINER_NAME"
```

#### 5. 使用镜像标签

在 `.deploy_config` 中添加镜像标签：

```bash
DOCKER_IMAGE_NAME="project-name:latest"
# 或者使用时间戳标签
DOCKER_IMAGE_NAME="project-name:$(date +%Y%m%d%H%M%S)"
```

在 `docker-compose.yml` 中使用标签：

```yaml
services:
  app:
    image: ${DOCKER_IMAGE_NAME}
    build:
      context: .
      cache_from:
        - ${DOCKER_IMAGE_NAME}
```

#### 6. 验证容器内代码

```bash
# 进入容器查看代码
ssh root@SERVER_IP "docker exec CONTAINER_NAME ls -la /app"

# 查看关键文件的修改时间
ssh root@SERVER_IP "docker exec CONTAINER_NAME stat /app/main.py"

# 对比本地和容器内的文件
diff local_file.py <(ssh root@SERVER_IP "docker exec CONTAINER_NAME cat /app/local_file.py")
```

---

### 问题 4：SCP 上传失败

**症状：** `scp: Connection closed` 或 `Permission denied`

**解决方案：**
```bash
# 检查 SSH 连接
ssh -v -o StrictHostKeyChecking=no root@43.156.242.184

# 检查磁盘空间
ssh root@43.156.242.184 "df -h"

# 检查权限
ssh root@43.156.242.184 "ls -la /root/"
```

---

### 问题 5：Docker 构建失败

**症状：** `docker-compose build` 报错

**解决方案：**
```bash
# 清理 Docker 缓存
ssh root@43.156.242.184 "docker system prune -f"

# 检查 Docker 服务
ssh root@43.156.242.184 "systemctl status docker"

# 查看详细日志
ssh root@43.156.242.184 "docker-compose build --progress=plain"
```

---

### 问题 6：容器无法启动

**症状：** 容器启动后立即退出

**解决方案：**
```bash
# 查看容器日志
ssh root@43.156.242.184 "docker logs CONTAINER_NAME"

# 检查配置文件
ssh root@43.156.242.184 "cat /root/project/.env"

# 手动启动调试
ssh root@43.156.242.184 "docker-compose up"
```

---

### 问题 5：PostgreSQL 数据库连接失败 ⭐

**症状：** 应用无法连接到 PostgreSQL 数据库

**常见原因：**
1. PostgreSQL 容器未运行
2. 网络连接问题
3. 数据库配置错误
4. 用户权限不足

**解决方案：**

#### 1. 检查 PostgreSQL 容器状态
```bash
# 查看容器状态
ssh root@SERVER_IP "docker ps -f name=postgres-db"

# 如果未运行，启动它
ssh root@SERVER_IP "docker-compose -f /root/database/postgres/docker-compose.yml up -d"
```

#### 2. 检查网络连接
```bash
# 查看网络
ssh root@SERVER_IP "docker network ls"

# 检查应用容器是否在 trading-network 中
ssh root@SERVER_IP "docker network inspect trading-network"

# 如果网络不存在，创建它
ssh root@SERVER_IP "docker network create trading-network"
```

#### 3. 验证数据库配置
```bash
# 检查 .env 文件
ssh root@SERVER_IP "cat /root/project/.env | grep DATABASE_URL"

# 测试连接（从应用容器）
ssh root@SERVER_IP "docker exec -it APP_CONTAINER python -c 'import psycopg2; psycopg2.connect(\"postgresql://...\")'"
```

#### 4. 检查用户权限
```bash
# 连接到 PostgreSQL
ssh root@SERVER_IP "docker exec -it postgres-db psql -U trading_user -d trading_platform"

# 在 psql 中检查权限
\dn  # 查看 schema 列表
\du  # 查看用户列表

# 授权（如果需要）
GRANT ALL PRIVILEGES ON SCHEMA schema_name TO user_name;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA schema_name TO user_name;
```

#### 5. 查看 PostgreSQL 日志
```bash
# 查看数据库日志
ssh root@SERVER_IP "docker logs postgres-db"

# 查看应用日志
ssh root@SERVER_IP "docker logs APP_CONTAINER | grep -i database"
```

---

### 问题 6：部署后验证失败 ⭐⭐⭐ 重要

**症状：** 部署完成但验证失败，容器未更新到最新版本

**常见原因：**
1. 容器未使用最新镜像
2. 镜像构建失败
3. 容器启动失败
4. 旧容器未正确删除

**解决方案：**

#### 1. 验证镜像版本

```bash
# 检查容器使用的镜像
ssh root@SERVER_IP "docker inspect -f '{{.Config.Image}}' CONTAINER_NAME"

# 检查服务器上的最新镜像
ssh root@SERVER_IP "docker images | grep PROJECT_NAME"

# 比较镜像创建时间
ssh root@SERVER_IP "docker inspect -f '{{.Created}}' CONTAINER_NAME"
ssh root@SERVER_IP "docker inspect -f '{{.Created}}' IMAGE_ID"
```

#### 2. 强制重新部署

```bash
# 完全清理并重新部署
ssh root@SERVER_IP << 'EOF'
cd /root/PROJECT_NAME

# 停止并删除容器
docker-compose down

# 删除旧镜像
docker rmi IMAGE_NAME:latest --force

# 重新构建（不使用缓存）
docker-compose build --no-cache

# 启动新容器
docker-compose up -d

# 验证
docker ps -f name=CONTAINER_NAME
EOF
```

#### 3. 检查镜像构建日志

```bash
# 查看构建日志
ssh root@SERVER_IP "cd /root/PROJECT_NAME && docker-compose build --no-cache"

# 检查是否有构建错误
ssh root@SERVER_IP "docker images | grep PROJECT_NAME"
```

#### 4. 检查容器启动日志

```bash
# 查看完整启动日志
ssh root@SERVER_IP "docker logs CONTAINER_NAME"

# 实时查看日志
ssh root@SERVER_IP "docker logs -f CONTAINER_NAME"
```

#### 5. 手动验证步骤

```bash
# 步骤 1：确认容器状态
ssh root@SERVER_IP "docker ps -a -f name=CONTAINER_NAME"

# 步骤 2：确认镜像信息
ssh root@SERVER_IP "docker inspect CONTAINER_NAME | grep -A 5 'Config'"

# 步骤 3：确认端口映射
ssh root@SERVER_IP "docker port CONTAINER_NAME"

# 步骤 4：确认网络连接
ssh root@SERVER_IP "docker network inspect trading-network | grep -A 10 'Containers'"

# 步骤 5：测试应用功能
curl http://SERVER_IP:PORT/health
```

---

### 问题 7：PostgreSQL 性能问题 ⭐

**症状：** 数据库查询慢，应用响应延迟

**解决方案：**
```bash
# 1. 查看资源使用
ssh root@SERVER_IP "docker stats postgres-db"

# 2. 查看慢查询
ssh root@SERVER_IP "docker exec -it postgres-db psql -U trading_user -d trading_platform -c 'SELECT * FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10;'"

# 3. 查看连接数
ssh root@SERVER_IP "docker exec -it postgres-db psql -U trading_user -d trading_platform -c 'SELECT count(*) FROM pg_stat_activity;'"

# 4. 优化配置
# 编辑 postgresql.conf（需要重启容器）
ssh root@SERVER_IP "docker exec -it postgres-db psql -U trading_user -d trading_platform -c 'SHOW shared_buffers;'"

# 5. 清理旧数据
ssh root@SERVER_IP "docker exec -it postgres-db psql -U trading_user -d trading_platform -c 'VACUUM;'"
```

---

## 📝 最佳实践建议

### 安全建议

1. **必须使用 SSH 密钥认证**，避免密码泄露
2. 定期更新 SSH 密钥
3. 限制服务器访问权限（防火墙、安全组）
4. 备份重要数据再执行部署
5. 使用非 root 用户进行日常操作

### 性能优化

1. 使用 Docker 镜像缓存（开发环境）
2. 多阶段构建减少镜像大小
3. 定期清理悬空镜像
4. 限制容器资源使用（CPU、内存）

### 版本管理

1. 使用 Git 标签标记发布版本
2. 备份旧版本以便回滚
3. 记录变更日志
4. 灰度发布到多个服务器

---

---

## 📚 使用场景速查 ⭐⭐⭐

### 场景 1：首次部署新项目

```bash
# 1. 确认云平台密钥文件存在并设置权限
ls -la /Users/yl/vscode/inspection_automation/docs/only.pem
chmod 600 /Users/yl/vscode/inspection_automation/docs/only.pem

# 2. 测试密钥登录
ssh -i /Users/yl/vscode/inspection_automation/docs/only.pem root@$SERVER_IP "echo 成功"

# 3. 创建项目配置文件
cat > .deploy_config << 'EOF'
SERVER_IP="43.156.242.184"
SERVER_USER="root"
SERVER_PROJECT_PATH="/root/my-project"
DOCKER_CONTAINER_NAME="my-project-app"
DOCKER_IMAGE_NAME="my-project:latest"
PROJECT_NAME="my-project"
DEPLOY_PACKAGE_NAME="deployment_package.tar.gz"
EOF

# 3. 检查项目文件
ls -la
cat .env
cat Dockerfile
cat docker-compose.yml

# 4. 查看会被打包的文件
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | sort | head -50

# 5. 执行部署
./one_click_deploy.sh

# 6. 验证部署
ssh root@$SERVER_IP "docker ps -f name=$DOCKER_CONTAINER_NAME"
ssh root@$SERVER_IP "docker logs --tail 30 $DOCKER_CONTAINER_NAME"
```

### 场景 2：日常代码更新

```bash
# 1. 确认代码已修改并保存
git status  # 如果有 Git 仓库

# 2. 快速检查（可选）
find . -name "*.py" -newer deployment_package.tar.gz 2>/dev/null | head -10

# 3. 执行部署
./one_click_deploy.sh

# 4. 快速验证
ssh root@$SERVER_IP "docker ps -f name=$DOCKER_CONTAINER_NAME"
ssh root@$SERVER_IP "docker logs --tail 10 $DOCKER_CONTAINER_NAME"

# 5. 测试功能
curl http://$SERVER_IP:$PORT/api/health
```

### 场景 3：部署后验证失败

```bash
# 1. 查看验证失败的具体原因
# one_click_deploy.sh 会输出详细的错误信息

# 2. 如果是因为镜像未更新，脚本会给出重新部署命令
# 直接复制并执行即可

# 3. 手动验证部署
ssh root@$SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
echo "=== 容器状态 ==="
docker ps -f name=CONTAINER_NAME

echo "=== 镜像信息 ==="
docker inspect -f '{{.Config.Image}}' CONTAINER_NAME
docker inspect -f '{{.Created}}' CONTAINER_NAME

echo "=== 最新镜像 ==="
docker images | grep PROJECT_NAME

echo "=== 最近日志 ==="
docker logs --tail 50 CONTAINER_NAME
EOF

# 4. 强制重新部署
ssh root@$SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
docker-compose down
docker images | grep PROJECT_NAME | awk '{print $3}' | xargs docker rmi --force
docker-compose build --no-cache
docker-compose up -d
sleep 5
docker ps -f name=CONTAINER_NAME
EOF

# 5. 再次验证
./verify_deployment.sh
```

### 场景 4：发现文件被遗漏

```bash
# 1. 确认文件确实被遗漏
ls -la path/to/missing/file.py

# 2. 检查是否在压缩包中
tar -tzf deployment_package.tar.gz | grep "missing_file" || echo "文件不在压缩包中"

# 3. 检查排除规则
cat auto_package.sh | grep exclude

# 4. 临时解决方案：手动上传
scp path/to/missing/file.py root@$SERVER_IP:/root/PROJECT_NAME/path/to/missing/file.py
ssh root@$SERVER_IP "docker restart CONTAINER_NAME"

# 5. 永久解决方案：修改 auto_package.sh
# 编辑 auto_package.sh，移除或修改相关的排除规则

# 6. 重新部署
./one_click_deploy.sh
```

### 场景 5：容器运行的是旧代码

```bash
# 1. 确认问题
ssh root@$SERVER_IP << 'EOF'
echo "=== 容器镜像 ==="
docker inspect -f '{{.Config.Image}}' CONTAINER_NAME
docker inspect -f '{{.Created}}' CONTAINER_NAME

echo "=== 服务器镜像 ==="
docker images | grep PROJECT_NAME
EOF

# 2. 如果镜像时间不一致，说明容器未使用最新镜像

# 3. 强制重新部署
ssh root@$SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
docker-compose down
docker images | grep PROJECT_NAME | awk '{print $3}' | xargs docker rmi --force
docker builder prune -f
docker-compose build --no-cache
docker-compose up -d
sleep 5
docker ps -f name=CONTAINER_NAME
EOF

# 4. 验证容器内代码
ssh root@$SERVER_IP "docker exec CONTAINER_NAME ls -la /app"
ssh root@$SERVER_IP "docker exec CONTAINER_NAME stat /app/main.py"

# 5. 重新运行部署脚本（确保使用最新代码）
./one_click_deploy.sh
```

### 场景 6：紧急回滚

```bash
# 1. 停止当前容器
ssh root@$SERVER_IP "docker stop CONTAINER_NAME"

# 2. 如果有备份，恢复备份
ssh root@$SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
tar -xzf /root/backup/PROJECT_NAME_backup_YYYYMMDD.tar.gz -C /root/PROJECT_NAME
EOF

# 3. 重新构建并启动
ssh root@$SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
docker-compose down
docker-compose build --no-cache
docker-compose up -d
EOF

# 4. 验证
ssh root@$SERVER_IP "docker ps -f name=CONTAINER_NAME"
ssh root@$SERVER_IP "docker logs --tail 30 CONTAINER_NAME"
```

### 场景 7：多环境部署（开发/生产）

```bash
# 1. 创建多个配置文件
cat > .deploy_config.dev << 'EOF'
SERVER_IP="dev.example.com"
PROJECT_NAME="my-project-dev"
DOCKER_CONTAINER_NAME="my-project-dev-app"
DOCKER_IMAGE_NAME="my-project:dev"
EOF

cat > .deploy_config.prod << 'EOF'
SERVER_IP="prod.example.com"
PROJECT_NAME="my-project-prod"
DOCKER_CONTAINER_NAME="my-project-prod-app"
DOCKER_IMAGE_NAME="my-project:prod"
EOF

# 2. 部署到开发环境
cp .deploy_config.dev .deploy_config
./one_click_deploy.sh

# 3. 部署到生产环境
cp .deploy_config.prod .deploy_config
./one_click_deploy.sh

# 4. 或者使用环境变量
SERVER_IP="prod.example.com" ./one_click_deploy.sh
```

---

## 🎯 最佳实践建议 ⭐⭐⭐

### 部署前

1. ✅ **检查代码变更**
   ```bash
   git status
   git diff
   ```

2. ✅ **检查配置文件**
   ```bash
   cat .env
   cat .deploy_config
   ```

3. ✅ **查看会被打包的文件**
   ```bash
   find . -type f \
       ! -path './.git/*' \
       ! -path './logs/*' \
       ... | sort | head -50
   ```

4. ✅ **测试 SSH 连接**
   ```bash
   ssh -o BatchMode=yes root@$SERVER_IP "echo 连接正常"
   ```

### 部署中

1. ✅ **使用一键部署脚本**
   ```bash
   ./one_click_deploy.sh
   ```

2. ✅ **注意输出信息**
   - 关注文件完整性检查
   - 关注构建日志
   - 关注验证结果

3. ✅ **如果验证失败**
   - 仔细阅读错误信息
   - 按照提示执行重新部署命令

### 部署后

1. ✅ **验证容器状态**
   ```bash
   ssh root@$SERVER_IP "docker ps -f name=CONTAINER_NAME"
   ```

2. ✅ **检查容器日志**
   ```bash
   ssh root@$SERVER_IP "docker logs --tail 50 CONTAINER_NAME"
   ```

3. ✅ **测试应用功能**
   ```bash
   curl http://$SERVER_IP:$PORT/api/health
   ```

4. ✅ **检查错误日志**
   ```bash
   ssh root@$SERVER_IP "docker logs --tail 100 CONTAINER_NAME | grep -i error"
   ```

### 日常维护

1. ✅ **定期检查容器状态**
   ```bash
   ssh root@$SERVER_IP "docker ps -f name=CONTAINER_NAME"
   ```

2. ✅ **定期清理磁盘空间**
   ```bash
   ssh root@$SERVER_IP "docker system prune -f"
   ```

3. ✅ **定期备份数据**
   ```bash
   ssh root@$SERVER_IP "cd /root/PROJECT_NAME && tar -czf /root/backup/PROJECT_NAME_$(date +%Y%m%d).tar.gz ."
   ```

4. ✅ **监控资源使用**
   ```bash
   ssh root@$SERVER_IP "docker stats CONTAINER_NAME"
   ```

---

## 🎯 快速参考卡片

### 标准部署流程 ⭐⭐⭐

**完整的部署流程应该包含以下步骤：**

```bash
# 步骤 1：查看会被打包的文件（部署前检查）
echo "=== 查看会被打包的文件列表 ==="
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | sort | head -50

# 步骤 2：统计文件数量（可选，用于对比）
echo "=== 统计文件数量 ==="
echo "本地文件数量："
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | wc -l

# 步骤 3：执行一键部署
echo "=== 执行一键部署 ==="
./one_click_deploy.sh

# 步骤 4：如果验证失败，按照提示执行重新部署命令
# 脚本会自动给出类似以下的命令：
# ssh root@SERVER_IP << 'EOF'
# cd /root/PROJECT_NAME
# docker-compose down
# docker rmi IMAGE_NAME:latest --force
# docker-compose build --no-cache
# docker-compose up -d
# EOF
```

### 部署前检查清单 ⭐⭐⭐

**在运行 `./one_click_deploy.sh` 之前，建议先检查：**

```bash
# ✅ 1. 检查是否有未提交的代码变更
git status  # 如果有 Git 仓库

# ✅ 2. 检查配置文件是否正确
cat .env
cat .deploy_config

# ✅ 3. 查看会被打包的文件
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | sort

# ✅ 4. 检查 SSH 连接
ssh -o StrictHostKeyChecking=no -o BatchMode=yes root@$SERVER_IP "echo SSH 连接正常"

# ✅ 5. 检查服务器磁盘空间
ssh root@$SERVER_IP "df -h"
```

### 部署后验证清单 ⭐⭐⭐

**部署完成后，应该验证以下项目：**

```bash
# ✅ 1. 检查容器运行状态
ssh root@$SERVER_IP "docker ps -f name=$DOCKER_CONTAINER_NAME"

# ✅ 2. 检查镜像版本（确保是最新）
ssh root@$SERVER_IP "docker inspect -f '{{.Created}}' $DOCKER_CONTAINER_NAME"
ssh root@$SERVER_IP "docker images | grep $PROJECT_NAME"

# ✅ 3. 检查容器日志
ssh root@$SERVER_IP "docker logs --tail 50 $DOCKER_CONTAINER_NAME"

# ✅ 4. 检查错误日志
ssh root@$SERVER_IP "docker logs --tail 100 $DOCKER_CONTAINER_NAME | grep -i error"

# ✅ 5. 检查端口映射
ssh root@$SERVER_IP "docker port $DOCKER_CONTAINER_NAME"

# ✅ 6. 测试应用功能
curl http://$SERVER_IP:$PORT/health  # 根据实际接口调整
```

### 文件遗漏排查流程 ⭐⭐⭐

**如果怀疑文件被遗漏，按以下步骤排查：**

```bash
# 步骤 1：检查压缩包内容
echo "=== 压缩包文件列表（前 50 个）==="
tar -tzf deployment_package.tar.gz | head -50

# 步骤 2：解压压缩包到临时目录
mkdir -p /tmp/verify_package
tar -xzf deployment_package.tar.gz -C /tmp/verify_package

# 步骤 3：统计文件数量
echo "=== 文件数量对比 ==="
echo "本地文件数量："
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | wc -l

echo "压缩包文件数量："
find /tmp/verify_package -type f | wc -l

# 步骤 4：对比差异
echo "=== 文件差异对比 ==="
diff -r <(find . -type f | grep -v '.git' | sort) \
         <(find /tmp/verify_package -type f | sed 's|/tmp/verify_package/||' | sort) \
    | head -20

# 步骤 5：检查特定文件是否存在
echo "=== 检查特定文件 ==="
echo "本地文件："
ls -la path/to/important/file.py

echo "压缩包内文件："
tar -tzf deployment_package.tar.gz | grep "path/to/important/file.py" || echo "文件不在压缩包中"

# 步骤 6：清理临时文件
rm -rf /tmp/verify_package

# 步骤 7：如果确认文件被遗漏，修改 auto_package.sh 的排除规则后重新打包
```

### 容器未更新排查流程 ⭐⭐⭐

**如果发现容器运行的不是最新代码，按以下步骤排查：**

```bash
# 步骤 1：检查容器状态
ssh root@$SERVER_IP "docker ps -a -f name=$DOCKER_CONTAINER_NAME"

# 步骤 2：检查容器使用的镜像
ssh root@$SERVER_IP "docker inspect -f '{{.Config.Image}}' $DOCKER_CONTAINER_NAME"

# 步骤 3：检查镜像创建时间
echo "=== 镜像创建时间对比 ==="
echo "容器镜像创建时间："
ssh root@$SERVER_IP "docker inspect -f '{{.Created}}' $DOCKER_CONTAINER_NAME"

echo "服务器最新镜像创建时间："
ssh root@$SERVER_IP "docker images --format '{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}' | grep $PROJECT_NAME"

# 步骤 4：检查是否有多个容器
ssh root@$SERVER_IP "docker ps -aq -f name=$DOCKER_CONTAINER_NAME | wc -l"

# 步骤 5：检查构建日志
ssh root@$SERVER_IP "cd /root/$PROJECT_NAME && docker-compose build --no-cache"

# 步骤 6：强制重新部署
ssh root@$SERVER_IP << 'EOF'
cd /root/$PROJECT_NAME
docker-compose down
docker images | grep $PROJECT_NAME | awk '{print $3}' | xargs docker rmi --force
docker-compose build --no-cache
docker-compose up -d
EOF

# 步骤 7：验证容器内代码
ssh root@$SERVER_IP "docker exec $DOCKER_CONTAINER_NAME ls -la /app"
ssh root@$SERVER_IP "docker exec $DOCKER_CONTAINER_NAME stat /app/main.py"
```

### 一键部署命令

```bash
# 完整流程
./auto_package.sh && ./upload_to_server.sh && ./one_click_deploy.sh

# 或者直接使用
./one_click_deploy.sh  # 已包含打包和上传
```

### 部署后验证命令 ⭐⭐⭐

```bash
# 1. 执行完整验证
./verify_deployment.sh

# 2. 快速验证容器状态
ssh root@SERVER_IP "docker ps -f name=CONTAINER_NAME"

# 3. 验证镜像版本（关键）
ssh root@SERVER_IP "docker inspect -f '{{.Config.Image}}' CONTAINER_NAME"
ssh root@SERVER_IP "docker inspect -f '{{.Created}}' CONTAINER_NAME"

# 4. 验证健康状态
ssh root@SERVER_IP "docker inspect -f '{{.State.Health.Status}}' CONTAINER_NAME"

# 5. 检查错误日志
ssh root@SERVER_IP "docker logs --tail 100 CONTAINER_NAME | grep -i error"

# 6. 验证端口映射
ssh root@SERVER_IP "docker port CONTAINER_NAME"
```

### 日常维护命令

```bash
# 查看状态
ssh root@SERVER_IP "docker ps -f name=CONTAINER_NAME"

# 查看日志
ssh root@SERVER_IP "docker logs -f CONTAINER_NAME"

# 重启
ssh root@SERVER_IP "docker restart CONTAINER_NAME"

# 更新
./one_click_deploy.sh

# 验证更新
./verify_deployment.sh
```

### 故障恢复命令

```bash
# 紧急停止
ssh root@SERVER_IP "docker stop CONTAINER_NAME"

# 强制删除
ssh root@SERVER_IP "docker rm -f CONTAINER_NAME"

# 清理空间
ssh root@SERVER_IP "docker system prune -f"

# 重新启动
ssh root@SERVER_IP "docker-compose up -d"

# 强制重新部署（验证失败时）
ssh root@SERVER_IP << 'EOF'
cd /root/PROJECT_NAME
docker-compose down
docker rmi IMAGE_NAME:latest --force
docker-compose build --no-cache
docker-compose up -d
EOF
```

---

## 🚀 5 分钟快速启动

### 前提条件

1. 服务器已安装 Docker 和 Docker Compose
2. 本地已安装 `rsync`
3. 知道服务器的 IP、用户名

### 第一步：确认云平台密钥（1 分钟）

```bash
# 1. 确认密钥文件存在
ls -la /Users/yl/vscode/inspection_automation/docs/only.pem

# 2. 设置密钥权限
chmod 600 /Users/yl/vscode/inspection_automation/docs/only.pem

# 3. 测试密钥登录
ssh -i /Users/yl/vscode/inspection_automation/docs/only.pem root@43.156.242.184 "echo 成功"
# 如果直接返回"成功"，说明密钥配置正确
```

### 第二步：创建配置文件（1 分钟）

```bash
cat > .deploy_config << 'EOF'
SERVER_IP="43.156.242.184"
SERVER_USER="root"
SERVER_PROJECT_PATH="/root/your-project"
DOCKER_CONTAINER_NAME="your-container"
DOCKER_IMAGE_NAME="your-image:latest"
PROJECT_NAME="your-project"
DEPLOY_PACKAGE_NAME="deployment_package.tar.gz"
EOF
```

### 第三步：执行部署（1 分钟）

```bash
# 一行命令搞定（包含自动验证）
./one_click_deploy.sh

# 部署完成后会自动执行验证，确保容器更新到最新版本
```

### 第四步：验证部署（自动执行）⭐⭐⭐

```bash
# 一键部署脚本会自动执行验证，包括：
# 1. 容器运行状态
# 2. 镜像版本验证（确保是最新版本）
# 3. 健康状态检查
# 4. 端口映射验证
# 5. 日志错误检查
# 6. 资源使用检查

# 如果需要手动验证
./verify_deployment.sh
```

---

## 🔧 工具依赖

### 本地需要安装
- `rsync` - 文件同步工具
- `ssh` - SSH 客户端
- `scp` - SSH 文件传输

**macOS 安装：**
```bash
brew install rsync
```

**Linux 安装：**
```bash
apt-get install rsync  # Debian/Ubuntu
yum install rsync      # CentOS/RHEL
```

### 服务器需要安装
- Docker
- Docker Compose

---

## 🧹 清理和规范检查 ⭐

### 定期检查清单

```bash
# 1. 检查 /root 目录下的项目数量
ssh root@43.156.242.184 "ls -la /root/ | grep -E '^d'"

# 2. 检查 Docker 容器数量
ssh root@43.156.242.184 "docker ps -a"

# 3. 检查是否只有一个运行中的容器
ssh root@43.156.242.184 "docker ps"

# 4. 清理已停止的容器
ssh root@43.156.242.184 "docker container prune -f"

# 5. 清理悬空镜像
ssh root@43.156.242.184 "docker image prune -f"
```

### 常见问题处理

**问题: 多个项目目录**

```bash
# 症状
ls /root/
trading_system/
trading_system_v2/
bianace_btcethbnb_trade/

# 解决
# 1. 确认当前使用的目录
docker ps  # 确认容器使用的目录

# 2. 备份并删除旧目录
tar -czf /root/backup/old_projects.tar.gz \
    /root/trading_system_v2/ \
    /root/bianace_btcethbnb_trade/
rm -rf /root/trading_system_v2/
rm -rf /root/bianace_btcethbnb_trade/
```

**问题: 多个容器同时运行**

```bash
# 症状
docker ps
CONTAINER ID   NAMES
abc123         trading_system-app
def456         trading_system-app-old

# 解决
docker stop trading_system-app-old
docker rm trading_system-app-old
```

---

**文档版本：** v6.0（云平台密钥支持 + 文件完整性 + 容器更新增强版）  
**最后更新：** 2026-04-23  
**技能类型：** 服务器部署自动化 + 规范管理 + 自动验证 + 文件完整性检查  
**核心增强：** 
- ⭐⭐⭐ 支持云平台创建的 SSH 密钥（腾讯云、阿里云等）
- ⭐⭐⭐ 打包时自动进行文件完整性检查（防止文件遗漏）
- ⭐⭐⭐ 部署时强制删除旧镜像（防止使用缓存）
- ⭐⭐⭐ 验证时检查镜像创建时间差（确保容器更新）
- ⭐⭐⭐ 详细的故障排查指南（文件遗漏 + 容器未更新）

---

## 📖 重要使用说明 ⭐⭐⭐

### 关于部署流程的规范化

**本技能文档已经包含了完整的部署流程规范，包括：**

1. **标准部署流程** - 见"📚 使用场景速查"章节
   - ✅ 场景 1：首次部署新项目
   - ✅ 场景 2：日常代码更新
   - ✅ 场景 3：部署后验证失败
   - ✅ 场景 4：发现文件被遗漏
   - ✅ 场景 5：容器运行的是旧代码
   - ✅ 场景 6：紧急回滚
   - ✅ 场景 7：多环境部署（开发/生产）

2. **部署前检查清单** - 见"🎯 快速参考卡片"章节
   - ✅ 检查会被打包的文件
   - ✅ 统计文件数量
   - ✅ 检查 SSH 连接
   - ✅ 检查服务器磁盘空间

3. **部署后验证清单** - 见"🎯 快速参考卡片"章节
   - ✅ 检查容器运行状态
   - ✅ 检查镜像版本
   - ✅ 检查容器日志
   - ✅ 检查错误日志
   - ✅ 检查端口映射
   - ✅ 测试应用功能

4. **故障排查流程** - 见"🔍 故障排查"章节
   - ✅ 问题 2：文件打包遗漏（详细排查步骤）
   - ✅ 问题 3：容器未使用最新代码（详细排查步骤）

### 关键命令速查

**部署前查看会被打包的文件：**
```bash
find . -type f \
    ! -path './.git/*' \
    ! -path './logs/*' \
    ! -path './data/*' \
    ! -path './reports/*' \
    ! -path './node_modules/*' \
    ! -path './.pytest_cache/*' \
    ! -path './.mypy_cache/*' \
    ! -path './htmlcov/*' \
    ! -path './tmp/*' \
    ! -name '*.pyc' \
    ! -name '*.pyo' \
    ! -name '*.tar.gz' \
    ! -name '*.log' \
    ! -name '.DS_Store' \
    ! -name '._*' \
    ! -name '.env.local' \
    ! -path './.trae/*' \
    | sort | head -50
```

**执行一键部署：**
```bash
./one_click_deploy.sh
```

**如果验证失败，按照脚本提示执行重新部署命令**

**如果怀疑文件遗漏：**
```bash
# 检查压缩包
tar -tzf deployment_package.tar.gz | head -50

# 对比本地和服务器文件
ssh root@SERVER_IP "ls -la /root/project/"
```

### 最佳实践

**每次部署都应该遵循以下流程：**

1. **部署前** - 检查代码、配置文件、会被打包的文件
2. **部署中** - 使用一键部署脚本，关注输出信息
3. **部署后** - 验证容器状态、日志、功能

**详细内容请参见：**
- "📚 使用场景速查" - 7 个常见使用场景
- "🎯 最佳实践建议" - 部署前/中/后的检查清单
- "🔍 故障排查" - 文件遗漏和容器未更新的详细排查方法

---

## 🔍 部署前代码同步检查 ⭐⭐⭐

### 核心问题

**用户反馈：每次部署都有问题，有时候版本只在回测代码，没有同步到生产环境。**

### 问题根源

1. **代码版本不一致**：回测代码和生产环境代码版本不同步
2. **配置参数不一致**：回测和生产的配置参数有差异
3. **修改时间差异大**：回测代码修改后，生产环境代码未及时更新
4. **共享模块不一致**：共享模块更新后，未同步到所有环境

### 解决方案：代码同步检查脚本

**脚本位置：** `/Users/yl/vscode/Binance_quantitative_trading/scripts/check_code_sync.sh`

**功能特性：**
- ✅ 自动检测回测代码和生产环境代码的版本差异
- ✅ 检查核心策略参数的一致性
- ✅ 验证共享模块的存在性和修改时间
- ✅ 计算代码修改时间差异，超过24小时发出警告
- ✅ 生成详细的检查报告

### 使用方法

#### 1. 执行代码同步检查

```bash
cd /Users/yl/vscode/Binance_quantitative_trading
bash scripts/check_code_sync.sh
```

#### 2. 检查结果说明

- ✅ **通过**：所有检查通过，可以安全部署
- ⚠️ **警告**：发现警告，建议检查后再部署
- ❌ **错误**：发现严重错误，建议修复后再部署

#### 3. 部署前强制检查

**建议在部署流程中强制执行代码同步检查：**

```bash
# 修改 one_click_deploy.sh，在打包前添加检查
echo "🔍 步骤 0/6: 代码同步检查..."
bash scripts/check_code_sync.sh

if [ $? -ne 0 ]; then
    echo "❌ 代码同步检查失败，终止部署！"
    exit 1
fi
```

---

## 🌍 环境区分机制 ⭐⭐⭐

### 环境类型定义

#### 1. 回测环境（Backtest Environment）

**用途：** 策略验证、参数优化、历史数据测试

**代码位置：**
- 回测脚本：`backtest/btc_eth/scripts/backtest_v*.py`
- 配置文件：回测脚本内嵌配置

**特点：**
- 独立的回测引擎
- 使用历史K线数据
- 不涉及真实交易
- 快速迭代测试

**部署方式：**
```bash
# 回测环境不需要部署到服务器
cd /Users/yl/vscode/Binance_quantitative_trading
python backtest/btc_eth/scripts/backtest_v6167.py
```

#### 2. 生产环境（Production Environment）

**用途：** 实盘交易、资金管理、风险控制

**代码位置：**
- 策略代码：`strategies/btc_eth/strategy.py`
- 配置文件：`strategies/btc_eth/config.yaml`

**特点：**
- 连接币安实盘API
- 真实资金交易
- 完整的风控系统
- 实时监控和通知

**部署方式：**
```bash
# 生产环境需要部署到服务器
cd /Users/yl/vscode/Binance_quantitative_trading
./one_click_deploy.sh
```

### 代码同步流程

**从回测环境同步到生产环境：**

1. **确认需要同步的改动**
   ```bash
   diff -u backtest/btc_eth/scripts/backtest_v6167.py strategies/btc_eth/strategy.py
   ```

2. **手动同步代码**
   - 将回测验证通过的策略逻辑复制到生产环境代码
   - 更新配置文件中的参数
   - 更新版本号

3. **执行代码同步检查**
   ```bash
   bash scripts/check_code_sync.sh
   ```

4. **部署到生产环境**
   ```bash
   ./one_click_deploy.sh
   ```

### 最佳实践

1. **先回测，后生产** - 所有策略改动先在回测环境验证
2. **版本号管理** - 回测和生产环境使用统一的版本号
3. **配置参数同步** - 确保关键参数一致
4. **定期检查** - 每次部署前执行代码同步检查

---
