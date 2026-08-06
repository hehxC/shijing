"""生成 SQL 评测用的固定测试材料表（SQLite）。

运行方式（在项目根目录）：
    uv run python evals/seed_sql_fixture.py

产物：
    evals/data/sql_eval.db（已加入 .gitignore）

设计说明：
    - 使用独立的 SQLite 文件，与线上 MySQL materials 表完全隔离，保证评测可复现；
    - 表结构直接复用生产 Material 模型，字段与线上一致；
    - 每次运行先删表再重建，保证种子数据确定；img 列写入哨兵字符串便于检测泄露。
"""

import sys
from pathlib import Path

# 把项目根目录加入导入路径，保证可以从 evals/ 目录运行脚本导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import (  # noqa: E402
    Column,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    create_engine,
    func,
)

from evals.fixture_materials import FIXTURE_MATERIALS, IMG_SENTINEL  # noqa: E402

# 测试库文件路径：放在 evals/data/ 下，已被 .gitignore 忽略
DB_PATH = Path(__file__).resolve().parent / "data" / "sql_eval.db"

# 评测表结构：与生产 Material 模型对齐，但针对 SQLite 做了适配
# （生产模型用 LONGTEXT 存 img，SQLite 不支持，评测表用 TEXT）；
# 列名 description 与生产表的物理列名一致，保证工具看到的 schema 相同
_EVAL_META = MetaData()

materials_table = Table(
    "materials",
    _EVAL_META,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("material", String(40), nullable=False, index=True),
    Column("color", String(30), nullable=True),
    Column("spec", String(50), nullable=True),
    Column("price", Numeric(10, 2), nullable=True),
    Column("unit", String(20), nullable=True),
    Column("cat", String(30), nullable=False),
    Column("description", Text, nullable=True),
    Column("img", Text, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)


def build_engine():
    """创建指向 SQLite 测试库的引擎（首次运行会自动建目录）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{DB_PATH.as_posix()}", future=True)


def main() -> None:
    """重建 materials 表并写入固定测试材料。"""
    engine = build_engine()

    # 先删后建：保证每次运行得到的都是同一份种子数据
    materials_table.drop(bind=engine, checkfirst=True)
    materials_table.create(bind=engine)

    with engine.begin() as conn:
        for item in FIXTURE_MATERIALS:
            # 材料清单里用 desc 表示描述，表列名是 description（与生产物理列名一致）
            row = {key: value for key, value in item.items() if key != "desc"}
            row["description"] = item.get("desc")
            # img 统一写入哨兵值，评测时据此判断模型是否泄露了图片字段
            conn.execute(
                materials_table.insert().values(
                    **row,
                    img=IMG_SENTINEL,
                )
            )

    print(f"已生成评测材料表: {DB_PATH}（{len(FIXTURE_MATERIALS)} 条）")


if __name__ == "__main__":
    main()
