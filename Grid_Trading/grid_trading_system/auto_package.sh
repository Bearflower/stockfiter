#!/bin/bash

# ============================================
# 网格交易系统 - 自动化打包脚本
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
    --exclude='.env' \
    --exclude='deployment_report_*.md' \
    --exclude='auto_package.sh' \
    --exclude='upload_to_server.sh' \
    --exclude='one_click_deploy.sh' \
    --exclude='verify_deployment.sh' \
    --exclude='docker_manage.sh' \
    --exclude='.deploy_config' \
    --exclude='.trae-cn/*' \
    --exclude='memory-bank/*' \
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

# 文件完整性检查
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
    ! -name '.env' \
    ! -name 'deployment_report_*.md' \
    ! -name 'auto_package.sh' \
    ! -name 'upload_to_server.sh' \
    ! -name 'one_click_deploy.sh' \
    ! -name 'verify_deployment.sh' \
    ! -name 'docker_manage.sh' \
    ! -name '.deploy_config' \
    ! -path './.trae-cn/*' \
    ! -path './memory-bank/*' \
    | wc -l)

# 解压压缩包并统计文件数量
VERIFY_DIR="/tmp/${PROJECT_NAME}_verify_$$"
mkdir -p "$VERIFY_DIR"
tar -xzf "$DEPLOY_PACKAGE_NAME" -C "$VERIFY_DIR"

PACKAGE_FILE_COUNT=$(find "$VERIFY_DIR" -type f | wc -l)
rm -rf "$VERIFY_DIR"

echo "📊 本地文件数量：$LOCAL_FILE_COUNT"
echo "📦 压缩包文件数量：$PACKAGE_FILE_COUNT"

# 计算差异（允许一定误差）
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
