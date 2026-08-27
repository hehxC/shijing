# 任务四验收：请求 ID 与结构化日志

## 验收时间

- 日期：2026-08-26
- 环境：本地 Uvicorn + MySQL + 已配置的真实模型供应商
- 服务地址：`http://127.0.0.1:8000`

## HTTP 层结果

| 场景 | 状态码 | 结果 |
| --- | ---: | --- |
| 未传 `X-Request-ID` | 200 | 服务端生成合法的 32 位 ID |
| 传入合法 ID | 200 | `client-check-20260826` 原样返回 |
| 传入包含空格的非法 ID | 200 | 非法值被替换为新的合法 ID |
| 未登录访问受保护接口 | 401 | 响应仍包含原 request ID |
| 访问不存在的路由 | 404 | 响应仍包含原 request ID |

## 真实聊天全链路结果

- request ID：`fullchain-1787726502`
- HTTP 状态：200
- 响应头返回 ID：与客户端发送值一致
- 用户消息 ID：122
- 助手回复：完整返回，未发生流式响应中断
- HTTP 总耗时：9001 ms
- `design_runs` 状态：`succeeded`
- `design_runs.total_latency_ms`：8917 ms
- `design_runs.failure_stage`：空

本次任务记录了三次 AI 调用：

| operation | provider | model | 状态 | 输入 Token | 输出 Token | 耗时 |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `intent_routing` | deepseek | deepseek-chat | succeeded | 457 | 37 | 1155 ms |
| `rag_retrieval` | dashscope | text-embedding-v4 | succeeded | 不可得 | 不可得 | 3318 ms |
| `text_chat` | deepseek | deepseek-chat | succeeded | 2071 | 31 | 1423 ms |

响应头、`http_request_completed`、`design_run_started`、三条
`ai_call_completed`、`design_run_completed`、`design_runs` 和
`ai_call_records` 均使用同一个 request ID。

## 验证期间发现并修复的问题

### Alembic 覆盖应用日志配置

应用启动时执行迁移，Alembic 的 `fileConfig` 默认会禁用已经存在的 Uvicorn 和
`app.*` logger，导致请求成功但没有结构化日志。修复为保留已有 logger。

### 流式生成器跨 Context 重置失败

Starlette 在线程池中逐次推进同步流式生成器，不同迭代可能使用不同的复制上下文。
`ContextVar.reset(token)` 不能在另一上下文中使用，曾导致模型调用成功后客户端响应
提前断开。修复为保存旧观测值并在退出时直接恢复，同时增加确定性回归测试。

## 安全与配置结论

- 自动化测试确认 Authorization、API Key、Token、密码和完整图片 Data URL 会被脱敏；
- HTTP 日志不记录请求头、用户消息正文或图片正文；
- 本地没有配置 `MODEL_PRICING_JSON`，因此本次 Token 已记录，但估算成本为空；
- 任务四的 request ID 与结构化日志验收通过；成本估算需在部署配置中单独填写模型价格。
