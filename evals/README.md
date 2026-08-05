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

`state` 的四个布尔值和 `route_chat_intent` 的参数一一对应；`expected.intent` 只能是五个意图之一。

## 后续计划

- 把真实流量日志（`data/router_decisions.jsonl`）清洗后并入数据集；
- 用 LLM 扩写句式变体，扩大覆盖面；
- 加入 SQL 查询评测和回答质量评测。
