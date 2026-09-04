# Docker 容器运行说明

当前镜像用于单实例生产预演：Python 依赖按 `uv.lock` 冻结构建，运行阶段不包含
uv、源码缓存、测试数据或本地密钥，并以 UID/GID `10001` 的非 root 用户启动。

> 完整生产编排（MySQL + MinIO + Caddy 反代 + 自动 HTTPS）见 `docker-compose.yml`；
> 域名、HTTPS 与反向代理（SSE 不缓冲、转发头、健康检查接线）详见
> [`domain-https.md`](domain-https.md)；数据库备份与恢复见
> [`backup-restore.md`](backup-restore.md)。

## 构建镜像

在项目根目录执行：

```powershell
docker build --pull --tag shijing:local .
```

镜像默认使用 Python 3.12 和 uv 0.11.14。需要更换基础 Python 小版本时可传递：

```powershell
docker build --build-arg PYTHON_VERSION=3.12 --tag shijing:local .
```

## 准备运行配置

`.env` 只在运行时通过 `--env-file` 注入，不会被复制进镜像。容器内的
`127.0.0.1` 指向容器自身，因此 `DATABASE_URL` 必须使用容器可以访问的数据库地址：

- Docker Desktop 访问宿主机 MySQL：使用 `host.docker.internal`；
- Docker Compose 或集群：使用数据库服务名；
- 云数据库：使用内网域名。

例如：

```text
DATABASE_URL=mysql+pymysql://shijing:password@host.docker.internal:3306/shijing?charset=utf8mb4
```

## 启动单实例容器

```powershell
docker volume create shijing-data
docker run --detach --name shijing-app --env-file .env --publish 8000:8000 --volume shijing-data:/app/data shijing:local
```

访问：

- 工作台：<http://127.0.0.1:8000/>
- API 文档：<http://127.0.0.1:8000/docs>

查看启动日志：

```powershell
docker logs --follow shijing-app
```

生产环境应设置 `APP_ENV=production`（此时应用启动不再自动迁移），并在每次发布时
用同一镜像执行一次性迁移：

```powershell
docker run --rm --env-file .env shijing:local alembic upgrade head
```

迁移依赖 `alembic/` 目录与 `alembic.ini`（均已复制进镜像），数据库地址通过
`--env-file` 注入；这样迁移可审计、可重复，也避免多实例启动时并发迁移。
非容器化环境可用 `uv run python scripts/migrate.py`。

## 健康检查

镜像内置两个探针：

- `/health/live`：进程存活，始终返回 200；
- `/health/ready`：检查数据库连通性，数据库不可用时返回 503，供负载均衡器摘除实例。

## 数据持久化边界

`/app/data` 包含本地图片、Chroma 数据和路由决策日志。使用 `IMAGE_STORE=local` 时
图片写入 `/app/data/images`；设置 `IMAGE_STORE=s3`（AWS S3 / 阿里云 OSS / MinIO）
后图片存入对象存储，不再依赖该卷。Chroma 数据与路由决策日志仍写入 `/app/data`，
必须挂载持久卷，否则删除容器会丢失这些本地数据。

MySQL 数据不应写入应用容器；应由独立数据库服务负责持久化和备份。

## 当前扩容限制

镜像固定使用一个 Uvicorn worker，因为限流、生成并发锁和幂等响应缓存目前保存在
进程内。直接增加 worker 或同时运行多个容器会让这些状态彼此隔离。完成 Redis
适配前，只支持单应用实例运行。

## 真实构建冒烟测试

安装并启动 Docker 后执行：

```powershell
$env:RUN_DOCKER_TESTS="1"
.\.venv\Scripts\python.exe -m unittest tests.test_containerization -v
Remove-Item Env:RUN_DOCKER_TESTS
```

测试会构建 `shijing-container-test` 镜像，并在容器内导入 FastAPI 应用。它不会启动
服务器、连接数据库或调用模型。
