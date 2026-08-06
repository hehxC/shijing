"""SQL 查询评测 runner：量化"材料查询/估价"这条链路干得对不对。

评测对象与生产一致：LangChain create_agent + SQLDatabaseToolkit + ChatDeepSeek，
但数据库指向固定的 SQLite 测试库（evals/data/sql_eval.db），与线上 MySQL 隔离。

对每条样本给出三层判定：
    1. SQL 文本匹配（归一化后比对，仅作参考）；
    2. 执行结果匹配（候选 SQL 与期望 SQL 各自执行后比对返回行）；
    3. 答案事实匹配（最终回答里是否出现期望的价格/单位/费用）。

再叠加工具行为断言：必须查库时是否真的调了 SQL、是否泄露 img 哨兵、无匹配时是否编价。

运行方式（在项目根目录）：
    uv run python evals/seed_sql_fixture.py            # 先生成测试材料表
    uv run python evals/run_sql_eval.py --limit 3      # 冒烟
    uv run python evals/run_sql_eval.py                # 全量
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# 把项目根目录加入导入路径，方便读取提示词文件
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain_community.agent_toolkits import SQLDatabaseToolkit  # noqa: E402
from langchain_community.utilities import SQLDatabase  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.service.sql_tool_guard import guard_sql_tools  # noqa: E402
from evals.ci_gate import apply_gate  # noqa: E402

load_dotenv()

# 评测用的 SQLite 测试库和数据集路径
EVAL_DB_PATH = Path(__file__).resolve().parent / "data" / "sql_eval.db"
DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "sql_query_seed.jsonl"
# 与生产一致：只暴露 materials 表、不采样示例行
PROMPT_PATH = Path(__file__).resolve().parents[1] / "app" / "prompts" / "chat_agent.md"

# 无匹配时回答里应出现的"如实说明"标记
HONEST_MARKERS = ("暂无", "没有找到", "未收录", "没有该", "库里没有", "未找到", "无记录", "没有这个")
# 回答里出现"数字 + 价格单位"即视为编造了价格（用于无匹配样本）
PRICE_PATTERN = re.compile(r"\d+(?:\.\d+)?\s*(?:元|块|吨|袋|/㎡|每平|一平)")
# 单位别名表：模型可能写 元/平方米 / 元/平米，与库里的 元/㎡ 等价
UNIT_ALIASES = {
    "元/㎡": ["元/㎡", "元/平方米", "元/平米", "元每平方米", "元每平米", "元/平方"],
    "元/吨": ["元/吨", "元每吨"],
    "元/袋": ["元/袋", "元每袋"],
    "元/块": ["元/块", "元每块"],
}

# LLM-as-judge：评审模型的打分维度（每项 1-5，归一化到 0-1）
JUDGE_DIMENSIONS = ["correctness", "no_hallucination", "completeness", "conciseness", "compliance"]
JUDGE_SYSTEM_PROMPT = (
    "你是回答质量评审员。根据用户问题、期望事实和评审规则，对助手回答逐维度打分（1-5 分）。\n"
    "打分维度：\n"
    "- correctness：回答中的关键数据（价格、单位、面积、费用）是否与期望事实一致\n"
    "- no_hallucination：是否编造了期望事实之外的价格、规格或市场区间\n"
    "- completeness：期望事实是否都被回答覆盖\n"
    "- conciseness：是否直接给结论、不冗长展开\n"
    "- compliance：是否泄露 SQL 语句、表名、字段名或图片内容，是否使用技术性措辞\n"
    "只输出 JSON，不要解释："
    '{"scores": {"correctness": 1-5, "no_hallucination": 1-5, "completeness": 1-5, "conciseness": 1-5, "compliance": 1-5}, "reason": "一句话理由"}'
)


def _judge_model() -> ChatDeepSeek:
    """评审模型固定 DeepSeek，temperature=0 保证打分尽量稳定。"""
    return ChatDeepSeek(
        model=os.getenv("TEXT_CHAT_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        temperature=0,
    )


def _extract_json(text: str) -> dict | None:
    """从评审模型输出里提取 JSON（兼容前后有多余文字）。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def judge_answer(question: str, expected_facts: dict, answer: str) -> dict | None:
    """调用评审模型给一条回答打分，返回归一化分数（0-1）与理由；失败返回 None。"""
    if not answer.strip():
        return None
    user_payload = {
        "question": question,
        "expected_facts": expected_facts,
        "answer": answer,
    }
    try:
        response = _judge_model().invoke(
            [
                SystemMessage(content=JUDGE_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
            ]
        )
        content = response.content if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)
        payload = _extract_json(content)
        if not payload or not isinstance(payload.get("scores"), dict):
            return None
        scores: dict[str, float] = {}
        for dim in JUDGE_DIMENSIONS:
            raw = payload["scores"].get(dim)
            try:
                scores[dim] = max(1, min(5, int(raw))) / 5
            except (TypeError, ValueError):
                return None
        return {"scores": scores, "reason": str(payload.get("reason", ""))[:200]}
    except Exception:
        return None


def load_dataset(path: Path) -> list[dict]:
    """读取 SQL 评测数据集，校验必需字段。"""
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"数据集第 {line_number} 行不是合法 JSON：{exc}") from exc
            if not {"question", "category", "expected_facts"}.issubset(record):
                raise SystemExit(f"数据集第 {line_number} 行缺少必需字段")
            records.append(record)
    return records


def build_eval_agent():
    """构建与生产等价的 SQL Agent：同一提示词 + SQLDatabaseToolkit + DeepSeek。"""
    if not EVAL_DB_PATH.is_file():
        raise SystemExit(f"找不到测试库 {EVAL_DB_PATH}，请先运行 evals/seed_sql_fixture.py")

    # 与生产一致：ChatDeepSeek 不设 temperature（默认 1.0），只连 SQLite 测试库
    engine = create_engine(f"sqlite:///{EVAL_DB_PATH.as_posix()}", future=True)
    db = SQLDatabase(engine, include_tables=["materials"], sample_rows_in_table_info=0)
    model = ChatDeepSeek(
        model=os.getenv("TEXT_CHAT_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
    )
    toolkit = SQLDatabaseToolkit(db=db, llm=model)
    # 与生产一致：套用同一套 SQL 工具防护（拦截 img、SELECT * 和写操作）
    tools = guard_sql_tools(toolkit.get_tools())
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    return create_agent(model, tools=tools, system_prompt=system_prompt)


def _tool_query(args) -> str:
    """从工具调用参数里提取 SQL 查询语句（兼容 dict 和 JSON 字符串两种形态）。"""
    if isinstance(args, dict):
        return str(args.get("query") or "")
    if isinstance(args, str):
        try:
            return str(json.loads(args).get("query") or "")
        except json.JSONDecodeError:
            return args
    return ""


def run_single(agent, question: str) -> dict:
    """执行一次 Agent 调用，收集 SQL 工具调用、最终回答、延迟与 token 用量。"""
    started = time.perf_counter()
    query_calls: list[str] = []
    all_tool_calls = 0
    final_parts: list[str] = []
    tokens = 0

    for update in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode="updates",
    ):
        for message in update.values() if isinstance(update, dict) else []:
            # 本版本 create_agent 的节点值可能是 {"messages": [...]} 字典，
            # 也可能是消息列表，两种形态都要兼容
            if isinstance(message, dict):
                message = message.get("messages", [])
            if not isinstance(message, list):
                message = [message]
            for msg in message:
                if isinstance(msg, AIMessage):
                    # 统计 token 用量（部分响应可能不带 usage_metadata，做容错）
                    usage = getattr(msg, "usage_metadata", None) or {}
                    tokens += int(usage.get("total_tokens") or 0)
                    # 带工具调用的消息不产出最终文本
                    if msg.tool_calls:
                        all_tool_calls += len(msg.tool_calls)
                        for call in msg.tool_calls:
                            query = _tool_query(call.get("args"))
                            if query:
                                query_calls.append(query)
                    elif msg.content:
                        final_parts.append(msg.content if isinstance(msg.content, str) else str(msg.content))
                elif isinstance(msg, ToolMessage):
                    all_tool_calls += 1

    return {
        "query_calls": query_calls,
        "tool_calls": all_tool_calls,
        "final_answer": "".join(final_parts),
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "tokens": tokens,
    }


def normalize_sql(sql: str) -> str:
    """归一化 SQL：去末尾分号、折叠空白、转小写，便于文本比对。"""
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def execute_rows(engine, sql: str) -> list[tuple]:
    """在测试库上执行 SQL，返回归一化后的行列表；执行失败抛异常。"""
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        return [tuple(str(cell) for cell in row) for row in result]


def check_execution(engine, candidate_sqls: list[str], expected_sql: str | None, facts: dict) -> bool | None:
    """执行结果匹配：任一候选 SQL 的执行结果包含期望的价格/单位即算对。

    不要求候选 SQL 与期望 SQL 逐字一致（模型可能多选列或调整写法），
    只要查回来的数据是对的就算执行匹配；无匹配样本则要求结果为空行。
    """
    if not candidate_sqls or not expected_sql:
        return None
    for candidate in candidate_sqls:
        try:
            rows = execute_rows(engine, candidate)
        except Exception:
            continue
        # 无匹配样本：候选查询返回空行才算符合预期
        if facts.get("exists") is False:
            if rows == []:
                return True
            continue
        flat = " ".join(cell for row in rows for cell in row)
        # 常规查询：结果里要出现期望价格和单位
        if "price" in facts:
            if contains_number(flat, facts["price"]) and facts.get("unit", "") in flat:
                return True
        # 估价样本：结果里要出现期望单价
        elif "unit_price" in facts:
            if contains_number(flat, facts["unit_price"]):
                return True
        elif rows:
            return True
    return False


def contains_number(answer: str, value: float | int) -> bool:
    """宽松判断回答里是否出现某个数字（兼容 65 / 65.0 / 65.00 写法）。"""
    candidates = {f"{value:g}", f"{value:.2f}", str(value)}
    # 去掉千分位逗号（模型可能写 6,800 / 11,400），避免匹配不到
    normalized = answer.replace(",", "").replace("，", "")
    return any(candidate in normalized for candidate in candidates)


def check_answer_facts(sample: dict, answer: str) -> dict:
    """按样本的期望事实检查最终回答，返回逐项布尔结果。"""
    facts = sample["expected_facts"]
    checks: dict[str, bool] = {}

    # 无匹配样本：必须如实说明，且不得编造价格
    if facts.get("exists") is False:
        checks["no_match_honest"] = any(marker in answer for marker in HONEST_MARKERS)
        checks["no_hallucinated_price"] = not PRICE_PATTERN.search(answer)
        return checks

    # 常规查询：期望价格与单位都要出现
    if "price" in facts:
        checks["price"] = contains_number(answer, facts["price"])
        unit = facts.get("unit")
        # 单位按别名匹配，避免 元/㎡ 与 元/平方米 写法差异导致误判
        aliases = UNIT_ALIASES.get(unit, [unit]) if unit else []
        checks["unit"] = any(alias in answer for alias in aliases) if aliases else True

    # 估价：面积 × 单价 的基础费用要出现
    if "base_cost" in facts:
        checks["base_cost"] = contains_number(answer, facts["base_cost"])
        checks["unit_price"] = contains_number(answer, facts["unit_price"])

    # 防护样本：禁止内容不得出现在回答里
    forbidden = facts.get("forbidden_in_answer")
    if forbidden:
        checks["no_forbidden"] = all(item not in answer for item in forbidden)

    return checks


def evaluate(records: list[dict], show_misses: bool, judge: bool = False) -> dict:
    """逐条评测并汇总指标；judge=True 时额外用 LLM-as-judge 给回答打分。"""
    engine = create_engine(f"sqlite:///{EVAL_DB_PATH.as_posix()}", future=True)
    agent = build_eval_agent()
    results = []

    for record in records:
        outcome = run_single(agent, record["question"])
        candidate_sqls = outcome["query_calls"]
        expected_sql = record.get("expected_sql")
        answer_checks = check_answer_facts(record, outcome["final_answer"])

        # 文本匹配：仅作为参考指标
        text_match = (
            any(normalize_sql(query) == normalize_sql(expected_sql) for query in candidate_sqls)
            if candidate_sqls and expected_sql
            else None
        )
        exec_match = check_execution(engine, candidate_sqls, expected_sql, record["expected_facts"])
        must_query_ok = bool(candidate_sqls) if record["must_query"] else None
        judge_score = (
            judge_answer(record["question"], record["expected_facts"], outcome["final_answer"])
            if judge
            else None
        )

        results.append(
            {
                "id": record["id"],
                "category": record["category"],
                "question": record["question"],
                "text_match": text_match,
                "exec_match": exec_match,
                "answer_checks": answer_checks,
                "answer_ok": all(answer_checks.values()) if answer_checks else None,
                "must_query_ok": must_query_ok,
                "tool_calls": outcome["tool_calls"],
                "latency_ms": outcome["latency_ms"],
                "tokens": outcome["tokens"],
                "answer_chars": len(outcome["final_answer"]),
                "judge": judge_score,
            }
        )

    return {
        "results": results,
        "engine": engine,
        "show_misses": show_misses,
    }


def _rate(hits: int, total: int) -> float:
    """计算比率，避免除零。"""
    return round(hits / total, 4) if total else 0.0


def compute_metrics(bundle: dict) -> dict:
    """从逐条结果汇总整体与分类别指标。"""
    results = bundle["results"]
    total = len(results)

    def count(predicate):
        return sum(1 for item in results if predicate(item))

    exec_total = count(lambda r: r["exec_match"] is not None)
    answer_total = count(lambda r: r["answer_ok"] is not None)
    must_total = count(lambda r: r["must_query_ok"] is not None)

    metrics = {
        "total": total,
        "query_text_match_rate": _rate(count(lambda r: r["text_match"] is True), count(lambda r: r["text_match"] is not None)),
        "query_exec_match_rate": _rate(count(lambda r: r["exec_match"] is True), exec_total),
        "answer_ok_rate": _rate(count(lambda r: r["answer_ok"] is True), answer_total),
        "must_query_rate": _rate(count(lambda r: r["must_query_ok"] is True), must_total),
        "avg_tool_calls": round(sum(r["tool_calls"] for r in results) / total, 2) if total else 0,
        "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / total, 1) if total else 0,
        "total_tokens": sum(r["tokens"] for r in results),
        # 平均回答长度（字符数）：和 judge 的 conciseness 分一起看，验证回答是否真的变短
        "avg_answer_chars": round(sum(r["answer_chars"] for r in results) / total, 1) if total else 0,
        # 无匹配样本的诚实率与编价率（红线指标）
        "no_match_honest_rate": _rate(
            count(lambda r: r["category"] == "no_match" and r["answer_checks"].get("no_match_honest") is True),
            count(lambda r: r["category"] == "no_match"),
        ),
        "no_match_hallucination_rate": _rate(
            count(lambda r: r["category"] == "no_match" and r["answer_checks"].get("no_hallucinated_price") is False),
            count(lambda r: r["category"] == "no_match"),
        ),
        # 防护样本通过率
        "guardrail_pass_rate": _rate(
            count(lambda r: r["category"] == "guardrail" and r["answer_checks"].get("no_forbidden") is True),
            count(lambda r: r["category"] == "guardrail"),
        ),
    }

    # LLM-as-judge 汇总：通过率（所有维度 >= 3 分）与各维度平均分
    judge_results = [item["judge"] for item in results if item["judge"]]
    if judge_results:
        metrics["judge_pass_rate"] = _rate(
            sum(1 for item in judge_results if all(score >= 0.6 for score in item["scores"].values())),
            len(judge_results),
        )
        metrics["judge_avg_score"] = round(
            sum(sum(item["scores"].values()) for item in judge_results) / (len(judge_results) * len(JUDGE_DIMENSIONS)),
            4,
        )
        metrics["judge_dimensions"] = {
            dim: round(sum(item["scores"][dim] for item in judge_results) / len(judge_results), 4)
            for dim in JUDGE_DIMENSIONS
        }
    else:
        metrics["judge_pass_rate"] = None
        metrics["judge_avg_score"] = None
        metrics["judge_dimensions"] = {}

    # 分类别：答案正确率 + 执行匹配率
    per_category: dict[str, dict] = {}
    for record in results:
        category = record["category"]
        bucket = per_category.setdefault(category, {"total": 0, "answer_hits": 0, "exec_hits": 0, "exec_total": 0})
        bucket["total"] += 1
        if record["answer_ok"] is True:
            bucket["answer_hits"] += 1
        if record["exec_match"] is not None:
            bucket["exec_total"] += 1
            if record["exec_match"] is True:
                bucket["exec_hits"] += 1
    metrics["per_category"] = {
        category: {
            "total": bucket["total"],
            "answer_ok_rate": _rate(bucket["answer_hits"], bucket["total"]),
            "exec_match_rate": _rate(bucket["exec_hits"], bucket["exec_total"]),
        }
        for category, bucket in per_category.items()
    }
    return metrics


def print_report(metrics: dict) -> None:
    """打印人类可读的评测报告。"""
    print(f"总样本: {metrics['total']}")
    print(f"SQL 文本匹配率: {metrics['query_text_match_rate']:.1%}")
    print(f"SQL 执行匹配率: {metrics['query_exec_match_rate']:.1%}")
    print(f"答案事实匹配率: {metrics['answer_ok_rate']:.1%}")
    print(f"必须查库合规率: {metrics['must_query_rate']:.1%}")
    # 编价率为严格口径：回答中出现价格表述即计，可能包含虚构的市场区间或引用库内真实价格，需人工复核
    print(f"无匹配诚实率: {metrics['no_match_honest_rate']:.1%}（价格表述率 {metrics['no_match_hallucination_rate']:.1%}，严格口径）")
    print(f"防护通过率: {metrics['guardrail_pass_rate']:.1%}")
    print(f"平均工具调用: {metrics['avg_tool_calls']} 次，平均延迟: {metrics['avg_latency_ms']:.0f}ms，总 token: {metrics['total_tokens']}")
    if metrics.get("judge_pass_rate") is not None:
        print(f"LLM-judge 通过率: {metrics['judge_pass_rate']:.1%}（平均分 {metrics['judge_avg_score']:.3f}）")
        dims = "，".join(f"{key}={value:.2f}" for key, value in metrics["judge_dimensions"].items())
        print(f"judge 各维度平均分: {dims}")
        print(f"平均回答长度: {metrics['avg_answer_chars']:.0f} 字符")
    print()
    print("分类别：")
    print(f"{'类别':<16}{'样本':>6}{'答案正确率':>12}{'执行匹配率':>12}")
    for category, item in metrics["per_category"].items():
        print(f"{category:<16}{item['total']:>6}{item['answer_ok_rate']:>12.1%}{item['exec_match_rate']:>12.1%}")


def print_misses(metrics: dict, bundle: dict) -> None:
    """逐条列出答案事实不匹配的样本。"""
    misses = [r for r in bundle["results"] if r["answer_ok"] is False]
    print(f"答案不匹配样本（共 {len(misses)} 条）：")
    for item in misses:
        failed = [key for key, ok in item["answer_checks"].items() if not ok]
        print(f"  #{item['id']} [{item['category']}] 失败项: {failed}")
        print(f"    问题: {item['question']}")


def save_baseline(metrics: dict, out_dir: Path, latest: bool = True) -> Path:
    """保存基线 JSON：带时间戳一份；latest=True 时再写固定 latest（冒烟运行不覆盖参考基线）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "domain": "sql",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **metrics,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = [out_dir / f"sql_{timestamp}.json"]
    if latest:
        paths.append(out_dir / "sql_latest.json")
    for path in paths:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return paths[0]


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="SQL 查询评测 runner")
    parser.add_argument("--dataset", type=Path, default=DATASET_PATH, help="评测数据集路径")
    parser.add_argument("--limit", type=int, default=0, help="只评测前 N 条（0 表示全部），用于快速冒烟")
    parser.add_argument("--out-dir", type=Path, default=Path("evals/baselines"), help="基线文件输出目录")
    parser.add_argument("--show-misses", action="store_true", help="逐条列出答案不匹配的样本")
    parser.add_argument("--judge", action="store_true", help="用 LLM-as-judge 给每条回答打分")
    parser.add_argument("--fail-below", type=float, default=None, help="主指标低于该阈值时门禁失败")
    parser.add_argument("--compare", type=Path, default=None, help="与指定基线 JSON 对比（用于 CI 回归）")
    parser.add_argument("--max-regression", type=float, default=None, help="相对基线允许的最大下降幅度（如 0.05 表示 5 个百分点）")
    args = parser.parse_args()

    if not args.dataset.is_file():
        raise SystemExit(f"找不到数据集：{args.dataset}，请先运行 evals/build_sql_seed_dataset.py")
    records = load_dataset(args.dataset)
    if args.limit > 0:
        records = records[: args.limit]

    if args.judge and not os.getenv("DEEPSEEK_API_KEY"):
        print("警告：--judge 需要 DEEPSEEK_API_KEY，未检测到时评审打分全部为空")

    bundle = evaluate(records, args.show_misses, judge=args.judge)
    metrics = compute_metrics(bundle)

    print("======== SQL 查询评测报告 ========")
    print_report(metrics)
    if args.show_misses:
        print()
        print_misses(metrics, bundle)

    # CI 门禁：--judge 时以 judge 通过率为主指标，否则以规则答案匹配率为主指标
    # 必须先于 save_baseline 执行，否则对比的是刚覆盖的基线文件（等于和自己比）
    primary = "judge_pass_rate" if (args.judge and metrics.get("judge_pass_rate") is not None) else "answer_ok_rate"
    passed = apply_gate(
        metrics,
        primary=primary,
        fail_below=args.fail_below,
        compare=args.compare,
        max_regression=args.max_regression,
    )
    if not passed:
        raise SystemExit(1)

    # 冒烟（--limit）只写时间戳存档，避免污染 CI 对比用的 latest 参考基线
    saved = save_baseline(metrics, args.out_dir, latest=(args.limit == 0))
    print()
    print(f"基线已保存: {saved}")


if __name__ == "__main__":
    main()
