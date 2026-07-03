# 网格交易策略 Dockerfile
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制共享模块
COPY shared/ /app/shared/

# 复制策略代码
COPY strategies/grid/ /app/strategies/grid/

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 设置环境变量
ENV STRATEGY_NAME=grid
ENV PYTHONUNBUFFERED=1

# 创建日志和数据目录
RUN mkdir -p /app/logs/grid /app/data/grid

# 运行策略
CMD ["python", "-m", "strategies.grid.main"]
