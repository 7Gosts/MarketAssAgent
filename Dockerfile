# MarketReActAgent Dockerfile
# 支持 Python + Node.js（研报搜索需要）

FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（Node.js + PostgreSQL 客户端库）
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    gnupg \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装 Python 包
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码
COPY . .

# 暴露端口
EXPOSE 8000

# 默认启动命令
CMD ["python", "cli/api_server.py"]
