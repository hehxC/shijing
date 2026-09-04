"""把 MySQL 数据库备份为 gzip 压缩的 SQL 文件。

依赖运行环境里的 ``mysqldump``；连接信息从 ``DATABASE_URL`` 解析，
密码通过 ``MYSQL_PWD`` 环境变量传入，不暴露在进程命令行里。

用法：
    python scripts/backup_db.py [输出目录]

默认输出到 ``data/backups/``，文件名形如 ``shijing_20260827_120000.sql.gz``。
"""

import gzip
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from scripts.db_url import parse_database_url  # noqa: E402

load_dotenv()


def main() -> int:
    conn = parse_database_url(os.getenv("DATABASE_URL", ""))
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else os.getenv("BACKUP_DIR", "data/backups"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"shijing_{stamp}.sql.gz"

    # --single-transaction：InnoDB 一致性快照，备份期间不阻塞写入
    # --no-tablespaces：避免 MySQL 8 下要求 PROCESS 权限
    cmd = [
        "mysqldump",
        "--single-transaction",
        "--routines",
        "--triggers",
        "--no-tablespaces",
        f"--host={conn['host']}",
        f"--port={conn['port']}",
        f"--user={conn['user']}",
        conn["database"],
    ]
    env = os.environ.copy()
    env["MYSQL_PWD"] = conn["password"]

    with gzip.open(out_path, "wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, env=env)

    if proc.returncode != 0:
        print(proc.stderr.decode("utf-8", "replace"), file=sys.stderr)
        out_path.unlink(missing_ok=True)
        return proc.returncode
    print(f"备份完成：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
