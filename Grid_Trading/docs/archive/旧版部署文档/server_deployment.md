# 服务器部署指南

## 📋 部署前准备

### 1. 服务器要求
- **操作系统**: Linux (Ubuntu 20.04+ 推荐)
- **内存**: 最低 512MB，推荐 1GB+
- **存储**: 最低 100MB
- **网络**: 可访问币安 API

### 2. 服务器软件
- **Docker**: 20.10+
- **Docker Compose**: 2.0+

### 3. 本地软件
- **SSH 客户端**
- **sshpass** (可选，用于自动输入密码)
- **rsync** (用于打包)

## 🔧 部署步骤

### 步骤 1: 配置服务器信息

编辑 `.deploy_config` 文件：

```bash
# 修改为你的服务器信息
SERVER_IP="你的服务器 IP"
SERVER_USER="root"
SERVER_PASSWORD="你的服务器密码"
SERVER_PROJECT_PATH="/root/adaptive_grid_trading"

DOCKER_CONTAINER_NAME="grid-trading"
DOCKER_IMAGE_NAME="grid-trading:latest"

PROJECT_NAME="adaptive_grid_trading"
DEPLOY_PACKAGE_NAME="deployment_package.tar.gz"
```

### 步骤 2: 安装 sshpass（可选但推荐）

**macOS:**
```bash
brew install sshpass
```

**Linux:**
```bash
apt-get install sshpass  # Debian/Ubuntu
yum install sshpass      # CentOS/RHEL
```

### 步骤 3: 执行一键部署

```bash
cd adaptive_grid_trading

# 添加执行权限
chmod +x auto_package.sh upload_to_server.sh one_click_deploy.sh

# 执行一键部署
./one_click_deploy.sh
```

## 📊 部署过程

一键部署脚本会自动完成：

1. **打包项目** - 创建压缩包（排除不必要的文件）
2. **上传到服务器** - 使用 SCP 上传
3. **远程部署** - 在服务器上：
   - 停止旧容器
   - 删除旧镜像
   - 解压新代码
   - 构建 Docker 镜像
   - 启动新容器
   - 检查容器状态

## 🔍 部署后验证

### 1. 检查容器状态

```bash
# SSH 登录服务器
ssh root@YOUR_SERVER_IP

# 查看容器状态
docker ps -f name=grid-trading

# 查看容器日志
docker logs grid-trading
```

### 2. 测试功能

```bash
# 查看实时日志
docker logs -f grid-trading

# 检查飞书报警
# 应该收到系统启动的报警消息
```

### 3. 监控运行

```bash
# 查看资源使用
docker stats grid-trading

# 查看详细信息
docker inspect grid-trading
```

## 🛠️ 容器管理命令

### 日常操作

```bash
# 重启容器
docker restart grid-trading

# 停止容器
docker stop grid-trading

# 启动容器
docker start grid-trading

# 查看日志
docker logs grid-trading

# 实时日志
docker logs -f grid-trading
```

### 更新部署

```bash
# 在本地重新执行一键部署
./one_click_deploy.sh
```

### 故障排查

```bash
# 进入容器
docker exec -it grid-trading /bin/bash

# 查看容器详细信息
docker inspect grid-trading

# 查看容器进程
docker top grid-trading

# 强制删除容器（谨慎使用）
docker rm -f grid-trading
```

## 📁 服务器文件结构

部署后服务器上的文件结构：

```
/root/adaptive_grid_trading/
├── Dockerfile              # Docker 配置
├── docker-compose.yml      # Docker Compose 配置
├── requirements.txt        # Python 依赖
├── src/                    # 源代码
├── config/
│   ├── config.yaml        # 策略配置
│   └── .env               # 环境变量（密钥）
├── logs/                   # 日志目录（挂载卷）
└── data/                   # 数据目录（挂载卷）
```

## 🔐 安全建议

### 1. 环境变量安全
- `.env` 文件包含敏感信息
- 不要提交到 Git
- 设置正确的文件权限：`chmod 600 config/.env`

### 2. SSH 安全
- 使用 SSH 密钥代替密码
- 限制 SSH 访问 IP
- 定期更新密码

### 3. Docker 安全
- 使用非 root 用户运行容器（如可能）
- 限制容器资源
- 定期更新基础镜像

## ⚠️ 常见问题

### Q1: 上传失败
**解决**: 
- 检查服务器密码是否正确
- 检查网络连接
- 安装 sshpass: `brew install sshpass`

### Q2: Docker 构建失败
**解决**:
```bash
# SSH 到服务器
ssh root@YOUR_SERVER_IP

# 清理 Docker 缓存
docker system prune -f

# 重新构建
cd /root/adaptive_grid_trading
docker-compose build --no-cache
```

### Q3: 容器无法启动
**解决**:
```bash
# 查看详细错误
docker logs grid-trading

# 手动启动调试
docker-compose up
```

### Q4: 无法访问币安 API
**解决**:
```bash
# 在服务器上测试
curl -I https://fapi.binance.com

# 如果无法访问，需要配置代理
# 或使用可以访问币安的服务器
```

## 📊 监控和日志

### 日志位置

**容器日志:**
```bash
docker logs grid-trading
```

**应用日志文件:**
```bash
# 在服务器上
docker exec grid-trading cat /app/logs/adaptive_grid.log
```

**本地查看:**
```bash
# 日志会保存在本地 logs/ 目录
cat logs/adaptive_grid.log
```

### 性能监控

```bash
# CPU 和内存使用
docker stats grid-trading

# 磁盘使用
docker system df

# 容器详细信息
docker inspect grid-trading
```

## 🔄 回滚操作

如果需要回滚到旧版本：

```bash
# SSH 到服务器
ssh root@YOUR_SERVER_IP

# 停止当前容器
docker stop grid-trading
docker rm grid-trading

# 使用旧镜像启动
docker run -d \
  --name grid-trading \
  --restart unless-stopped \
  grid-trading:previous_version
```

## 📞 获取帮助

遇到问题时：

1. 查看日志：`docker logs grid-trading`
2. 检查容器状态：`docker ps -a`
3. 查看系统资源：`docker stats`
4. 检查网络连接：`curl -I https://fapi.binance.com`

---

**部署脚本版本**: v1.0  
**最后更新**: 2026-03-19  
**项目名称**: 自适应趋势网格策略系统
