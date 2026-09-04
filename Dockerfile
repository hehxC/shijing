ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ARG UV_VERSION=0.11.14
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build

# uv 只存在于构建阶段；运行镜像仅复制冻结后的虚拟环境。
RUN python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 使用固定的非 root UID/GID，便于宿主机卷和编排平台设置文件权限。
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /app/data/images /app/data/chroma /app/static/generated \
    && chown -R app:app /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . /app

USER app

EXPOSE 8000
STOPSIGNAL SIGTERM

# 暂时固定单 worker：当前限流、并发锁和幂等缓存为进程内实现。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
