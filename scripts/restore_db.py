"""从 gzip 压缩的 SQL 备份文件恢复 MySQL 数据库。

连接信息从 ``DATABASE_URL`` 解析；恢复会覆盖目标库现有数据，请先确认。

用法：
    python scripts/restore_db.py <备份文件.sql[.gz]>
"""

import gzip
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
from scripts.db_url import parse_database_url  # noqa: E402

load_dotenv()


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("用法：python scripts/restore_db.py <备份文件.sql[.gz]>")
    backup = Path(sys.argv[1])
    if not backup.is_file():
        raise SystemExit(f"备份文件不存在：{backup}")

    conn = parse_database_url(os.getenv("DATABASE_URL", ""))
    cmd = [
        "mysql",
        f"--host={conn['host']}",
        f"--port={conn['port']}",
        f"--user={conn['user']}",
        conn["database"],
    ]
    env = os.environ.copy()
    env["MYSQL_PWD"] = conn["password"]

    opener = gzip.open if backup.suffix == ".gz" else open
    with opener(backup, "rb") as fh:
        proc = subprocess.run(cmd, stdin=fh, stderr=subprocess.PIPE, env=env)

    if proc.returncode != 0:
        print(proc.stderr.decode("utf-8", "replace"), file=sys.stderr)
        return proc.returncode
    print(f"恢复完成：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
