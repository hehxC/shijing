"""SQL 工具错误与安全拒绝场景测试。"""

import unittest

from langchain_core.tools import StructuredTool

from app.service.sql_tool_guard import guard_sql_tools


class SqlToolGuardTests(unittest.TestCase):
    """通过包装后的工具接口验证危险 SQL 不会到达数据库。"""

    def setUp(self):
        self.executed_queries = []

        def database_query(query: str) -> str:
            self.executed_queries.append(query)
            return "database result"

        tool = StructuredTool.from_function(
            name="sql_db_query",
            description="Execute a read-only SQL query.",
            func=database_query,
        )
        self.guarded_tool = guard_sql_tools([tool])[0]

    def test_image_column_select_star_and_writes_are_rejected_locally(self):
        dangerous_queries = (
            "SELECT img FROM materials",
            "SELECT * FROM materials",
            "DELETE FROM materials WHERE id = 1",
        )

        for query in dangerous_queries:
            with self.subTest(query=query):
                response = self.guarded_tool.invoke({"query": query})
                self.assertIn("查询被拒绝", response)

        self.assertEqual([], self.executed_queries)

    def test_safe_select_reaches_database_with_execution_timeout(self):
        response = self.guarded_tool.invoke(
            {"query": "SELECT material, price FROM materials"}
        )

        self.assertEqual("database result", response)
        self.assertEqual(1, len(self.executed_queries))
        self.assertIn("MAX_EXECUTION_TIME(15000)", self.executed_queries[0])


if __name__ == "__main__":
    unittest.main()
