"""集中读取与部署相关的运行时配置，支持开发/测试/生产环境隔离。

所有值均可通过环境变量覆盖；生产环境通过 `APP_ENV=production` 显式声明，
从而关闭应用启动时的自动迁移等开发期便利行为。
"""

import os


def app_env() -> str:
    """当前运行环境：development / testing / production。"""
    return os.getenv("APP_ENV", "development").lower()


def is_production() -> bool:
    """是否运行在生产环境。"""
    return app_env() == "production"


def auto_migrate() -> bool:
    """是否在应用启动时自动执行数据库迁移。

    生产环境默认关闭，迁移由独立发布步骤（``scripts/migrate.py`` 或
    ``alembic upgrade head``）执行；开发环境默认开启以保留本地体验。
    可用 ``AUTO_MIGRATE`` 显式覆盖（值为 1/true/yes/on 时开启）。
    """
    raw = os.getenv("AUTO_MIGRATE")
    if raw is not None:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return not is_production()
