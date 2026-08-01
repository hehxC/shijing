# 石境：AI 造园顾问

石境是一个面向庭院设计与园林材料咨询的 AI 应用。用户可以通过文字或参考图片描述改造需求，选择庭院风格，生成效果图，并继续追问材料、预算与施工建议。项目同时提供用户登录、会话上下文记忆和材料管理能力。

## 核心功能

- **AI 庭院咨询**：以流式响应提供风格、布局、植物配置和施工建议。
- **18 种庭院风格**：内置新中式、宋式文人园、枯山水、侘寂、现代东方极简、英式乡村等风格。
- **参考图分析**：识别庭院空间、硬景材料、植物配置和光照条件。
- **多图庭院效果图**：同时使用一张庭院空间图和最多六张石材参考图生成 16:9、2K 效果图；没有空间图时按风格自行设计布局。
- **效果图连续修改**：设计素材未变化时基于当前效果图继续调整；空间图、石材方案或风格变化后自动重新生成完整方案。
- **材料查询与估价**：通过 LangChain SQL 工具查询 MySQL 材料库，回答规格、颜色、价格及用量问题。
- **多意图路由**：区分普通对话、材料查询、图片分析、预算估算与效果图生成，并提供规则降级策略。
- **用户与会话管理**：支持注册、登录、匿名对话、用户消息记录以及跨模型会话上下文。
- **材料管理后台**：支持查看、新增和删除自定义园林材料。

## 技术栈

- Python 3.12+
- FastAPI、Uvicorn
- LangChain、LangGraph
- DeepSeek、通义千问视觉模型、Google Gemini 图像模型
- SQLAlchemy、PyMySQL、MySQL
- 原生 HTML、CSS、JavaScript
- uv 依赖管理

## 项目结构

```text
.
├── app/
│   ├── api/                    # 登录、聊天和材料接口
│   ├── middleware/             # 人工确认中间件
│   ├── models/                 # SQLAlchemy 与 Pydantic 模型
│   ├── prompts/                # AI 造园顾问系统提示词
│   ├── service/                # 对话、意图路由、图像生成与会话服务
│   ├── database.py             # 数据库连接
│   └── garden_styles.py        # 内置庭院风格目录
├── static/                     # 聊天页、设计页、管理后台及静态资源
├── tests/                      # 单元测试
├── main.py                     # FastAPI 应用入口
├── pyproject.toml              # 项目与依赖配置
└── uv.lock                     # 锁定的依赖版本
```

## 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/hehxC/shijing.git
cd shijing
```

### 2. 安装依赖

推荐使用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync
```

### 3. 准备 MySQL

创建一个使用 `utf8mb4` 字符集的数据库：

```sql
CREATE DATABASE shijing
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

应用启动时会自动创建所需数据表。

### 4. 配置环境变量

在项目根目录新建 `.env` 文件，并按所使用的模型服务填写配置：

```dotenv
# MySQL
DATABASE_URL=mysql+pymysql://用户名:密码@127.0.0.1:3306/shijing?charset=utf8mb4

# 登录令牌签名密钥；生产环境务必替换为足够长的随机字符串
AUTH_SECRET_KEY=请替换为随机密钥
AUTH_TOKEN_TTL_SECONDS=604800

# 默认文本模型：DeepSeek
TEXT_CHAT_MODEL=deepseek-chat
DEEPSEEK_API_KEY=你的_DeepSeek_API_Key

# 默认视觉模型：通义千问
IMAGE_CHAT_MODEL=qwen-vl-max-latest
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_API_KEY=你的_DashScope_API_Key

# 效果图生成：Gemini
GEMINI_IMAGE_MODEL=gemini-3.1-flash-image
GEMINI_KEY=你的_Gemini_API_Key
```

也可以把文本或视觉模型切换为项目已支持的其他模型。只需设置相应的模型名称和服务商密钥即可。

> 请勿将 `.env` 或任何真实 API Key 提交到 GitHub。

### 5. 启动服务

```bash
uv run uvicorn main:app --reload
```

启动后可访问：

- AI 对话首页：<http://127.0.0.1:8000/>
- 材料管理后台：<http://127.0.0.1:8000/static/admin.html>
- FastAPI 接口文档：<http://127.0.0.1:8000/docs>

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/chat` | 发送文字或图片消息，返回流式 AI 响应 |
| `GET` | `/api/styles` | 获取内置庭院风格列表 |
| `GET` | `/api/design/session` | 恢复当前设计会话的空间图、石材方案、风格和效果图 |
| `PUT` / `DELETE` | `/api/design/space-image` | 上传、替换或删除庭院空间图 |
| `POST` / `DELETE` | `/api/design/material-images` | 添加或清空石材参考图 |
| `PATCH` / `DELETE` | `/api/design/material-images/{image_id}` | 修改石材名称、用途或删除单张石材图 |
| `PATCH` | `/api/design/style` | 保存当前庭院风格 |
| `POST` | `/api/design/reset` | 清空当前设计会话并新建设计 |
| `POST` | `/api/auth/register` | 注册用户 |
| `POST` | `/api/auth/login` | 用户登录 |
| `GET` | `/api/auth/me` | 获取当前用户信息 |
| `GET` | `/api/materials` | 获取自定义材料列表 |
| `POST` | `/api/materials` | 新增材料 |
| `DELETE` | `/api/materials/{material_id}` | 删除材料 |

## 运行测试

```bash
uv run python -m unittest discover -s tests -v
```

## 使用提示

- 未登录用户也可以正常对话；登录后，会话将与用户身份进行隔离。
- 空间图和石材参考图使用独立入口上传；石材图可填写名称并多选用途。
- 设计资源接口通过 `X-Design-Session` 请求头接收浏览器生成的高强度随机会话标识。
- 生成效果图后，可以直接输入“增加瓦片围边”“换成新中式”等要求继续修改。
- 材料价格回答依赖数据库中的实际记录；没有匹配数据时，系统不会编造价格。
- AI 输出仅作为设计与材料参考，不能替代专业施工图、结构安全评估或当地规范审批。

## 数据与安全

- 用户密码使用 PBKDF2-SHA256 加盐哈希存储，不保存明文密码。
- 用户上传图片不会以 Base64 形式写入消息日志；日志仅记录是否包含图片及数据长度。
- 庭院空间图与石材参考图按设计会话私有存入 MySQL，不提供公开静态地址；设计图片和效果图在最后活动 30 天后自动清理。
- 生产环境请使用独立数据库账号、强随机签名密钥，并妥善管理模型服务密钥。
