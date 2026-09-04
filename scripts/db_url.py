"""从 ``DATABASE_URL`` 解析出 mysqldump/mysql 需要的连接参数。"""

from urllib.parse import unquote

from sqlalchemy.engine.url import make_url


def parse_database_url(raw_url: str) -> dict:
    """解析 mysql 连接串，返回 host/port/user/password/database。

    密码等凭据在 URL 里可能是百分号编码的，这里统一解码。
    """
    url = make_url(raw_url)
    if url.drivername not in ("mysql", "mysql+pymysql"):
        raise ValueError("DATABASE_URL 必须是 mysql 连接串")
    if not url.host or not url.database:
        raise ValueError("DATABASE_URL 缺少主机或数据库名")
    return {
        "host": url.host,
        "port": url.port or 3306,
        "user": unquote(url.username or ""),
        "password": unquote(url.password or ""),
        "database": url.database,
    }
