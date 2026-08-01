# OmniBase Backend Image
#
# 基础镜像：Python 3.11 slim（体积小，含必要编译工具）
# 包管理：uv（比 pip 快 10-100 倍）
# 运行：uvicorn + --reload（开发模式热重载）

FROM python:3.11-slim-bookworm

# 防止 Python 写 .pyc（容器内无用，节省 IO）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONPATH="/app/src" \
    PATH="/app/.venv/bin:$PATH"

# 安装系统依赖
# - curl/wget：healthcheck 与调试
# - build-essential：部分 Python 包（如 psycopg）需要编译
# - libpq-dev：psycopg 编译依赖
# - git：uv 安装某些包时需要
# 使用国内 Debian 镜像源（加速 apt）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        wget \
        build-essential \
        libpq-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（直接下载二进制，避开 install.sh 的路径问题）
# 用国内 jsDelivr CDN 镜像
RUN ARCH=$(uname -m) && \
    case "$ARCH" in \
        x86_64) UV_ARCH="x86_64-unknown-linux-gnu" ;; \
        aarch64) UV_ARCH="aarch64-unknown-linux-gnu" ;; \
        *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac && \
    wget -q "https://cdn.jsdelivr.net/gh/astral-sh/uv@0.4.27/scripts/uv-${UV_ARCH}.tar.gz" -O /tmp/uv.tar.gz \
    || wget -q "https://github.com/astral-sh/uv/releases/download/0.4.27/uv-${UV_ARCH}.tar.gz" -O /tmp/uv.tar.gz && \
    mkdir -p /tmp/uv-extract && \
    tar -xzf /tmp/uv.tar.gz -C /tmp/uv-extract && \
    cp /tmp/uv-extract/uv-${UV_ARCH}/uv /usr/local/bin/uv && \
    cp /tmp/uv-extract/uv-${UV_ARCH}/uvx /usr/local/bin/uvx && \
    chmod +x /usr/local/bin/uv /usr/local/bin/uvx && \
    rm -rf /tmp/uv.tar.gz /tmp/uv-extract && \
    uv --version

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存（依赖不变则不重装）
COPY pyproject.toml uv.lock* ./

# 配置 uv 使用国内 PyPI 镜像（清华 TUNA）
ENV UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_EXTRA_INDEX_URL=https://pypi.org/simple

# 安装依赖到虚拟环境
RUN uv sync --no-dev

# 复制源码（开发模式下此层被 volume mount 覆盖）
COPY . .

# 暴露 FastAPI 端口
EXPOSE 8000

# 开发模式：热重载
# 生产环境应改用：uvicorn omnibase.main:app --host 0.0.0.0 --port 8000 --workers 4
CMD ["uvicorn", "omnibase.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "src"]
