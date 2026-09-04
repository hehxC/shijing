"""独立发布步骤：把数据库迁移到最新版本。

生产环境应用启动时不再自动迁移（``AUTO_MIGRATE=false``），改为在每次发布时
显式执行本脚本，使迁移可审计、可重复，并避免多实例同时启动时并发迁移。

用法：
    uv run python scripts/migrate.py
"""

from pathlib import Path
import sys

from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()


def main() -> int:
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
