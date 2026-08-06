"""SQL 工具防护：在工具层拦截会把图片数据或大字段带进上下文的查询。

防护不依赖模型的自觉，而是直接包装 SQL 查询类工具：
    1. 查询中出现 img 列（图片数据）→ 拒绝；
    2. SELECT *（会隐式带入 img 等大字段）→ 拒绝；
    3. INSERT/UPDATE/DELETE/DROP 等写操作 → 拒绝。
被拒绝时返回中文提示，让模型改用明确的文本字段继续回答。
"""

import re

from langchain_core.tools import StructuredTool

# 需要包装的 SQL 工具名（query_checker 会原样返回待执行 SQL，也要拦截）
_GUARDED_TOOL_NAMES = {"sql_db_query", "sql_db_query_checker"}

# img 列引用：单独成词，避免误伤 material 等字段名里的 "img" 子串
_IMG_COLUMN_PATTERN = re.compile(r"\bimg\b", re.IGNORECASE)
# SELECT *：会把所有列（含 img）带进上下文
_SELECT_STAR_PATTERN = re.compile(r"\bselect\s+\*", re.IGNORECASE)
# 写操作关键字：查询工具只允许读，不允许改
_WRITE_KEYWORD_PATTERN = re.compile(
    r"^\s*(?:insert|update|delete|drop|alter|create|replace|truncate|grant|revoke)\b",
    re.IGNORECASE,
)


def _block_reason(query: str) -> str | None:
    """检查一条 SQL 是否命中防护规则，命中则返回原因，否则返回 None。"""
    if _IMG_COLUMN_PATTERN.search(query):
        return "img 字段包含图片数据，禁止查询"
    if _SELECT_STAR_PATTERN.search(query):
        return "SELECT * 会把 img 等大字段带入上下文，请改用明确的字段"
    if _WRITE_KEYWORD_PATTERN.search(query):
        return "查询工具只允许读取，禁止写操作"
    return None


def _guarded_query_tool(tool: StructuredTool) -> StructuredTool:
    """把单个 SQL 查询工具包装成带防护的版本，接口与原工具一致。"""
    original_invoke = tool.invoke

    def guarded(query: str) -> str:
        # 命中防护规则时直接返回拒绝提示，不再执行原工具
        reason = _block_reason(query)
        if reason:
            return (
                f"查询被拒绝：{reason}。"
                "请只查询 material、color、spec、price、unit、cat、description 等文本字段。"
            )
        return original_invoke({"query": query})

    return StructuredTool.from_function(
        name=tool.name,
        description=tool.description,
        func=guarded,
        args_schema=tool.args_schema,
    )


def guard_sql_tools(tools: list) -> list:
    """包装工具列表里的 SQL 查询类工具，其余工具原样保留。"""
    guarded = []
    for tool in tools:
        if tool.name in _GUARDED_TOOL_NAMES:
            guarded.append(_guarded_query_tool(tool))
        else:
            guarded.append(tool)
    return guarded
