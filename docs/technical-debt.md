# 技术债盘点（2026-08-07）

> 盘点方式：直连 MySQL 统计表体积与 base64 字段占用（`tmp/audit_storage.py`，可重复运行），
> 配合 `static/generated/` 目录统计与代码审查。行数为实际 COUNT，表体积为 information_schema 估算。

## 一、数字摘要

| 项目 | 数量 | 占用 |
| --- | --- | --- |
| `materials.img`（base64 图片） | 101 行 | **487.7 MB**（单张最大 12.2 MB） |
| `materials` 表物理体积 | — | **387.5 MB**（几乎全是 img） |
| `design_reference_images.data_url` | 34 行 | 11.1 MB（材料 23 / 空间 7 / space3d 4） |
| `chat_session_contexts.reference_image_data_url` | 7 行 | 2.4 MB（与上表空间图**重复存储**） |
| `static/generated/` 生成图文件 | 33 个 | 110.6 MB（本地磁盘，gitignored） |
| `chat_messages.generated_image_filename` | 22 条 | 引用上述文件 |
| `chat_user_messages` | 0 行 | 遗留空表（疑似旧设计） |

## 二、问题清单（按优先级）

### P0：材料图片以 base64 存在 MySQL LONGTEXT（487.7 MB）

- **现状**：`materials.img` 列（LONGTEXT）存 101 张 base64 图片，占全库体积的绝对大头；上传/读取都要做 base64 编解码，每次查询 `materials` 若不小心选到 `img` 会把几十 MB 文本拉进内存（防护工具已拦截 `img` 查询，但列本身是隐患）。
- **影响**：备份慢、表膨胀、查询/读写性能差、无法走 CDN。
- **状态**：✅ 已修复（2026-08-07）——新建 `app/service/image_store.py` 存储抽象（本地文件系统实现 + S3 兼容接口），`materials.img` 迁移到 `data/images/materials/`，DB 改存 `image_key`；旧列已删除（迁移 `ec176e80cea0`）。

### P1：空间图在同一会话里被双写冗余

- **现状**：`save_space_image` 同时写 `design_reference_images.data_url` 和 `chat_session_contexts.reference_image_data_url`，同一张 base64 存两份（实测 7 行共 2.4 MB 完全重复）。
- **影响**：空间占用翻倍、两处可能不一致。
- **状态**：✅ 已修复（2026-08-07）——上下文表不再存 base64，`get_session_context` 从 `design_reference_images` 解析；冗余列已删除。

### P1：生成效果图存本地 `static/generated/`（110.6 MB）

- **现状**：33 个生成图文件存在仓库 `static/generated/`（gitignored），通过受保护接口按文件名读取；无统一存储抽象。
- **影响**：部署时需要持久化该目录；多实例/容器重建会丢图；无法扩到对象存储。
- **状态**：✅ 已修复（2026-08-07）——生成图读写/删除并入 `image_store`（`data/images/generated/`），受保护接口改为从存储读字节返回。

### P2：`list_conversations` 存在 N+1 查询

- **现状**：`app/service/conversation_service.py` 的 `list_conversations` 先查全部会话，再**每条会话单独查一次**最新消息做预览，会话数为 N 时共 1+N 次查询。
- **影响**：会话一多延迟线性恶化。
- **状态**：✅ 已修复（2026-08-07）——改用窗口函数 `ROW_NUMBER()` 一次取回全部会话与预览，7 个会话时 SELECT 次数从 8 降为 1。

### P2：无 Alembic，schema 变更靠启动时手工 ALTER

- **现状**：`main.py` 启动时 `Base.metadata.create_all` + `ensure_chat_session_context_columns` 手工补 6 列（reference_image_data_url、reference_image_request、selected_style_id、context_revision、effect_revision、assets_expired_at）。
- **影响**：迁移不可审计、不可回滚；多环境容易漏跑；手工 SQL 与模型定义漂移。
- **状态**：✅ 已修复（2026-08-07）——引入 Alembic，生成基线迁移 `94f07976cdfc`（6 张表全量）；`main.py` 启动改为 `alembic upgrade head`，手工 ALTER 与 `create_all` 已删除。

### P3：生产聊天路径无 token/成本统计

- **现状**：路由决策有结构化日志（`data/router_decisions.jsonl`），但模型调用（ChatDeepSeek/视觉/文生图）在生产聊天路径没有统一的 token/成本记录。
- **影响**：无法核算单会话成本、无法定位"哪类请求最烧钱"。
- **建议**：给模型调用加 usage 统计，落一张 token 流水表或结构化日志。

### P3：无限流

- **现状**：`/chat` 等接口无任何限流。
- **影响**：被刷会直接烧 API 额度。
- **建议**：每用户/分钟限流，超限返回 429。

### 杂项

- `chat_user_messages` 空表 ✅ 已删除（迁移 `91afb379d785`，2026-08-07）；
- `chat_session_contexts` 遗留列（`material_analysis` + `model3d_*` 共 6 列）✅ 已删除（同一迁移），`alembic check` 已无差异；
- `design_reference_images.kind` 出现 `space3d`，领域文档未定义该类型，仍需确认是否遗留。

## 三、建议实施顺序

1. 低风险快赢：修 N+1、补索引（P2）；
2. 引入 Alembic，把手工 ALTER 收敛（P2）；
3. 图片存储迁移：存储抽象 → 读写切换 → 存量迁移 → 清理冗余与旧列（P0/P1）；
4. 可观测性与限流（P3）。
