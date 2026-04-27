#!/bin/bash

# ============================================
# 网格交易系统 - 一键部署脚本
# ============================================

set -e

# 加载配置
source .deploy_config

echo "============================================="
echo "一键部署 - $PROJECT_NAME"
echo "目标服务器：$SERVER_IP"
echo "============================================="

# 检查 SSH 密钥是否存在
if [ ! -f "$SSH_KEY_PATH" ]; then
    echo "❌ 错误：SSH 密钥文件不存在: $SSH_KEY_PATH"
    exit 1
fi

# 步骤 1：打包
echo "📦 步骤 1/5: 打包项目..."
./auto_package.sh

# 步骤 2：上传
echo ""
echo "📤 步骤 2/5: 上传到服务器..."
scp -i "$SSH_KEY_PATH" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$DEPLOY_PACKAGE_NAME" \
    "$SERVER_USER@$SERVER_IP:/root/"

echo "✅ 上传成功"

# 步骤 3：远程部署
echo ""
echo "🚀 步骤 3/5: 远程部署..."

ssh -i "$SSH_KEY_PATH" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$SERVER_USER@$SERVER_IP" << ENDSSH

# 在服务器上执行的命令
set -e

echo "============================================="
echo "远程部署开始"
echo "============================================="

# 1. 创建项目目录
echo "📁 创建项目目录..."
mkdir -p /root/$PROJECT_NAME
cd /root/$PROJECT_NAME

# 2. 停止并删除旧容器
echo "🛑 停止旧容器..."
if docker ps -q -f name=$DOCKER_CONTAINER_NAME | grep -q .; then
    docker stop \$DOCKER_CONTAINER_NAME 2>/dev/null || true
    echo "✅ 容器已停止"
else
    echo "⚠️  容器未运行，跳过停止"
fi

echo "🗑️  删除旧容器..."
if docker ps -aq -f name=\$DOCKER_CONTAINER_NAME | grep -q .; then
    docker rm \$DOCKER_CONTAINER_NAME 2>/dev/null || true
    echo "✅ 容器已删除"
else
    echo "⚠️  容器不存在，跳过删除"
fi

# 3. 删除旧镜像
echo "🗑️  删除旧镜像（防止使用缓存）..."
if docker images -q \$DOCKER_IMAGE_NAME | grep -q .; then
    docker rmi \$DOCKER_IMAGE_NAME --force 2>/dev/null || true
    echo "✅ 旧镜像已删除"
else
    echo "⚠️  旧镜像不存在，跳过删除"
fi

# 4. 解压新包
echo "📦 解压新代码包..."
tar -xzf /root/$DEPLOY_PACKAGE_NAME -C /root/$PROJECT_NAME --strip-components=1
echo "✅ 代码包已解压"

# 5. 设置权限
chmod +x src/main.py 2>/dev/null || true
echo "✅ 权限已设置"

# 6. 构建 Docker 镜像
echo "🏗️  构建 Docker 镜像..."
docker build -t \$DOCKER_IMAGE_NAME .
if [ \$? -ne 0 ]; then
    echo "❌ Docker 构建失败！"
    exit 1
fi
echo "✅ Docker 镜像构建成功"

# 7. 启动容器
echo "🚀 启动 Docker 容器..."
docker run -d \
    --name \$DOCKER_CONTAINER_NAME \
    --restart unless-stopped \
    -v /root/$PROJECT_NAME/config:/app/config \
    -v /root/$PROJECT_NAME/logs:/app/logs \
    -v /root/$PROJECT_NAME/data:/app/data \
    -e TZ=Asia/Shanghai \
    \$DOCKER_IMAGE_NAME

if [ \$? -ne 0 ]; then
    echo "❌ Docker 容器启动失败！"
    exit 1
fi
echo "✅ Docker 容器启动成功"

# 8. 等待容器启动
echo "⏳ 等待容器启动..."
sleep 5

# 9. 显示状态
echo "============================================="
echo "容器状态:"
docker ps -f name=\$DOCKER_CONTAINER_NAME
echo "============================================="
echo "最近日志:"
docker logs --tail 30 \$DOCKER_CONTAINER_NAME
echo "============================================="

ENDSSH

# 检查远程部署是否成功
if [ $? -ne 0 ]; then
    echo "❌ 远程部署失败！"
    exit 1
fi

# 步骤 4：验证部署
echo ""
echo "✅ 步骤 4/5: 验证部署..."

ssh -i "$SSH_KEY_PATH" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$SERVER_USER@$SERVER_IP" << ENDSSH

echo "============================================="
echo "🔍 部署验证"
echo "============================================="

# 1. 验证容器运行状态
echo "1️⃣  验证容器运行状态..."
CONTAINER_STATUS=\$(docker ps -f name=\$DOCKER_CONTAINER_NAME --format '{{.Status}}')
if [ -z "\$CONTAINER_STATUS" ]; then
    echo "❌ 容器未运行！部署失败！"
    exit 1
fi
echo "✅ 容器运行状态：\$CONTAINER_STATUS"

# 2. 验证容器镜像版本
echo ""
echo "2️⃣  验证容器镜像版本..."
CONTAINER_IMAGE=\$(docker inspect -f '{{.Config.Image}}' \$DOCKER_CONTAINER_NAME 2>/dev/null)
IMAGE_CREATED=\$(docker inspect -f '{{.Created}}' \$DOCKER_CONTAINER_NAME 2>/dev/null)
echo "   容器镜像：\$CONTAINER_IMAGE"
echo "   镜像创建时间：\$IMAGE_CREATED"

# 3. 验证容器日志
echo ""
echo "3️⃣  验证容器日志（检查启动错误）..."
ERROR_COUNT=\$(docker logs --tail 100 \$DOCKER_CONTAINER_NAME 2>&1 | grep -i "error\|exception\|fatal" | wc -l)
if [ "\$ERROR_COUNT" -gt 0 ]; then
    echo "⚠️  发现 \$ERROR_COUNT 个错误日志，请检查："
    docker logs --tail 20 \$DOCKER_CONTAINER_NAME
else
    echo "✅ 未发现明显错误日志"
fi

# 4. 验证容器资源使用
echo ""
echo "4️⃣  验证容器资源使用..."
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" \$DOCKER_CONTAINER_NAME

# 5. 最终验证总结
echo ""
echo "============================================="
echo "📋 验证总结"
echo "============================================="
echo "容器名称：\$DOCKER_CONTAINER_NAME"
echo "运行状态：\$CONTAINER_STATUS"
echo "镜像版本：\$CONTAINER_IMAGE"
echo "错误日志：\$ERROR_COUNT 个"
echo "============================================="

if [ "\$ERROR_COUNT" -eq 0 ] && [ -n "\$CONTAINER_STATUS" ]; then
    echo "✅ 验证通过！容器已成功部署！"
    exit 0
else
    echo "❌ 验证失败！请检查上述错误！"
    exit 1
fi

ENDSSH

# 检查验证结果
if [ $? -eq 0 ]; then
    echo "============================================="
    echo "🎉 一键部署完成！验证通过！"
    echo "============================================="
else
    echo "============================================="
    echo "⚠️  部署完成但验证失败！请检查上述错误！"
    echo "============================================="
    exit 1
fi

# 步骤 5：清理临时文件
echo ""
echo "📤 步骤 5/5: 清理临时文件..."
rm -f "$DEPLOY_PACKAGE_NAME"
ssh -i "$SSH_KEY_PATH" \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    "$SERVER_USER@$SERVER_IP" \
    "rm -f /root/$DEPLOY_PACKAGE_NAME"
echo "✅ 临时文件已清理"

echo ""
echo "============================================="
echo "🎉 部署全部完成！"
echo "============================================="
echo ""
echo "📝 后续步骤："
echo "1. 在服务器上配置 config/config.yaml 和 config/.env"
echo "2. 启动系统：ssh root@$SERVER_IP 'docker exec -it $DOCKER_CONTAINER_NAME python -m src.main'"
echo "============================================="
