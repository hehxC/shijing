# 意图路由评测

把"路由判得准不准"变成可以量化的指标，让每次改动（改提示词、改正则、换模型）都有成绩可对比。

## 三个组成部分

| 名称 | 作用 | 位置 |
| --- | --- | --- |
| 合成数据集 | 试卷：用户消息 + 会话状态 + 期望路由结果 | `evals/datasets/intent_routing_seed.jsonl` |
| 评测 runner | 阅卷机：逐条跑路由、比对答案、算指标 | `evals/run_intent_router_eval.py` |
| 基线 | 成绩单：某次评测的指标快照，用于后续对比 | `evals/baselines/*.json` |

## 使用方法

```bash
# 1. 生成（或重新生成）合成数据集
uv run python evals/build_seed_dataset.py

# 2. 跑纯规则基线（确定性、不调用模型）
uv run python evals/run_intent_router_eval.py --mode rule

# 2.1 想看具体哪些句子被误判，加 --show-misses
uv run python evals/run_intent_router_eval.py --mode rule --show-misses

# 3. 跑混合模式（部分样本调用 LLM；可用 --limit 限制条数快速冒烟）
uv run python evals/run_intent_router_eval.py --mode hybrid --limit 30
```

每次运行会在 `evals/baselines/` 下保存两份文件：带时间戳的存档和固定的 `latest`。对比两次改动，直接看 `latest` 的指标变化即可。
加 `--show-misses` 会逐条列出意图误判的样本（消息、期望 vs 实际、状态、备注），这是定位改进点的第一手证据。

## 数据集格式

每行一个 JSON 对象：

```json
{
  "id": 1,
  "message": "莱姆石铺装多少钱一平",
  "state": {
    "has_uploaded_image": false,
    "has_reference_image": false,
    "has_generated_image": false,
    "has_material_analysis": false
  },
  "expected": { "intent": "estimate_price", "use_image": "none" },
  "note": "问单价"
}
```

`state` 的三个布尔值和 `route_chat_intent` 的参数一一对应；`expected.intent` 只能是五个意图之一。

### 估价与材料查询的边界约定

- `estimate_price`：带面积或项目范围的费用估算，需要按 面积 × 单价 计算，如「50平米院子铺莱姆石大概多少钱」；
- `query_material`：只问材料属性（含单价、规格、颜色），查数据库即可，如「水洗石铺装多少钱一平」。

## 后续计划

- 把真实流量日志（`data/router_decisions.jsonl`）清洗后并入数据集；
- 用 LLM 扩写句式变体，扩大覆盖面；
- 加入 SQL 查询评测和回答质量评测。

## SQL 查询评测（进行中）

量化"材料查询/估价"这条链路（文本 Agent + SQLDatabaseToolkit + materials 表）干得对不对。

当前已完成：

- **固定测试材料表**：`evals/fixture_materials.py` 定义 23 条写死价格/单位的材料，`evals/seed_sql_fixture.py` 生成独立的 SQLite 测试库 `evals/data/sql_eval.db`（与线上 MySQL 隔离、可复现；img 字段写入哨兵值用于检测泄露）；
- **第一批数据集**：`evals/build_sql_seed_dataset.py` 生成 `evals/datasets/sql_query_seed.jsonl`，共 50 条，覆盖六类：精确匹配 / 模糊匹配 / 多条件 / 估价计算 / 无匹配 / 防护。
- **评测 runner**：`evals/run_sql_eval.py` 逐条运行生产同款 SQL Agent（同一提示词 + SQLDatabaseToolkit + DeepSeek，但连测试库），做三层判定（SQL 文本匹配 / 执行结果匹配 / 答案事实匹配）和工具行为断言（必须查库、img 泄露、无匹配编价），输出报告并保存基线 `evals/baselines/sql_latest.json`。

使用方法：

```bash
uv run python evals/seed_sql_fixture.py
uv run python evals/build_sql_seed_dataset.py
uv run python evals/run_sql_eval.py --limit 3     # 冒烟
uv run python evals/run_sql_eval.py --show-misses # 全量
```

### 当前基线（50 条，修复提示词与工具防护后）

| 指标 | 结果 |
| --- | --- |
| SQL 执行匹配率 | 100.0%（查询结果都包含了正确价格/单位） |
| 答案事实匹配率 | 98.0% |
| 必须查库合规率 | 100.0% |
| 无匹配诚实率 | 100.0%（全部如实说明未收录） |
| 无匹配价格表述率 | 12.5%（严格口径，仅剩 1/8 偶发） |
| 防护通过率 | 100.0% |
| 平均工具调用 / 延迟 / token | 2.6 次 / 4.3s / 约 4.2k |

### 修复效果对比（同一数据集，修复前后）

| 指标 | 修改前 | 修改后 |
| --- | --- | --- |
| 答案事实匹配率 | 88.0% | 98.0% |
| 防护通过率（img 泄露） | 87.5% | 100.0% |
| 平均工具调用次数 | 8.7 次 | 2.6 次 |
| 平均延迟 / 总 token | 9.8s / 470k | 4.3s / 208k |
| 无匹配价格表述率 | 62.5% | 12.5% |

对应修复：提示词预置 `materials` 表结构（省掉查 schema 步骤）、明确禁止无匹配时补充市场参考价、工具层拦截 img 列 / SELECT * / 写操作（`app/service/sql_tool_guard.py`）。

### 剩余已知问题

1. 无匹配样本仍有 1/8 偶发出现价格表述（严格口径），来自 LLM 非确定性，可接受或继续压；
2. 评测使用 ChatDeepSeek 默认温度 1.0，单次结果存在波动，多次对比看趋势而非单点。
