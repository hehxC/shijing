# =====================================================================
# 已停用：本项目 RAG 未实际使用（应用侧 RAG 默认关闭 ENABLE_RAG=false）。
#   本评测脚本不再参与 CI 与日常使用，仅作历史参考，保留代码便于将来启用 RAG。
# =====================================================================
"""RAG 回答评测 runner（任务 8）。

对知识咨询问题分别生成"无 RAG"与"有 RAG"回答（复用 _text_agent），
用 LLM-as-judge（DeepSeek，temperature=0）按五个维度打分：
correctness（正确性）/ groundedness（有据率）/ no_hallucination（无幻觉）
/ coverage（覆盖度）/ conciseness（简洁性），对比两种模式的差异。

用法（在项目根目录）：
    uv run python evals/run_rag_answer_eval.py --limit 20
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_deepseek import ChatDeepSeek  # noqa: E402

from app.service.chat_service import _text_agent  # noqa: E402
from app.service.rag_service import retrieve  # noqa: E402
from evals.ci_gate import apply_gate  # noqa: E402

load_dotenv()

JUDGE_DIMENSIONS = ["correctness", "groundedness", "no_hallucination", "coverage", "conciseness"]
JUDGE_SYSTEM_PROMPT = (
    "你是回答质量评审员。根据用户问题和检索知识片段（可能为空），对助手回答逐维度打分（1-5 分）。\n"
    "打分维度：\n"
    "- correctness：回答中的事实是否正确、合理\n"
    "- groundedness：回答是否与检索片段一致（无片段时，判断回答是否有常识依据）\n"
    "- no_hallucination：是否编造了片段或常识之外的具体事实\n"
    "- coverage：是否覆盖了用户问题的关键要点\n"
    "- conciseness：是否直接给结论、不冗长展开\n"
    "只输出 JSON，不要解释："
    '{"scores": {"correctness": 1-5, "groundedness": 1-5, "no_hallucination": 1-5, "coverage": 1-5, "conciseness": 1-5}, "reason": "一句话理由"}'
)


def _extract_json(text: str) -> dict | None:
    """从评审输出里提取 JSON（兼容前后多余文字）。"""
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


def load_dataset(path: Path) -> list[dict]:
    """读取检索评测数据集，只保留有 gold 文档的问题（负样本不生成回答）。"""
    records = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("relevant_docs"):
                records.append(record)
    return records


def answer_with(question: str, session_id: str, context: list[dict] | None) -> str:
    """用 _text_agent 生成回答：context=None 为无 RAG，传入片段为有 RAG。"""
    state = {
        "message": question,
        "session_id": session_id,
        "history": [],
        "retrieved_context": context,
    }
    return "".join(_text_agent(state))


def judge_answer(question: str, context_text: str, answer: str) -> dict | None:
    """调用评审模型打分，返回归一化分数（0-1）与理由；失败返回 None。"""
    if not answer.strip():
        return None
    payload = {"question": question, "context": context_text, "answer": answer}
    try:
        response = ChatDeepSeek(
            model="deepseek-chat",
            api_key=__import__("os").getenv("DEEPSEEK_API_KEY"),
            temperature=0,
        ).invoke(
            [
                SystemMessage(content=JUDGE_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )
        content = response.content if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)
        parsed = _extract_json(content)
        if not parsed or not isinstance(parsed.get("scores"), dict):
            return None
        scores = {}
        for dim in JUDGE_DIMENSIONS:
            try:
                scores[dim] = max(1, min(5, int(parsed["scores"].get(dim)))) / 5
            except (TypeError, ValueError):
                return None
        return {"scores": scores, "reason": str(parsed.get("reason", ""))[:200]}
    except Exception:
        return None


def evaluate(records: list[dict], k: int, max_distance: float) -> dict:
    """逐条生成两版回答并评审，汇总指标。"""
    rows = []
    for index, record in enumerate(records, 1):
        question = record["question"]
        session_id = f"rag-answer-{index}"
        items = retrieve(question, k=k, retriever="vector", max_distance=max_distance)
        # 与 _retrieve_node 相同的片段格式，_text_agent 会按编号注入
        context_items = [
            {
                "ref": f"[{i}]",
                "doc_id": item["doc_id"],
                "source": item["source"],
                "content": item["content"],
            }
            for i, item in enumerate(items, 1)
        ]
        context_text = "\n".join(
            f"{item['ref']} [{item['source']}] {item['content']}" for item in context_items
        )

        answer_no_rag = answer_with(question, session_id, None)
        answer_rag = answer_with(question, session_id, context_items or None)

        judge_no_rag = judge_answer(question, "", answer_no_rag)
        judge_rag = judge_answer(question, context_text, answer_rag)
        rows.append(
            {
                "question": question,
                "has_context": bool(context_items),
                "no_rag": judge_no_rag,
                "rag": judge_rag,
            }
        )

    def dim_average(mode: str, dim: str) -> float:
        values = [row[mode]["scores"][dim] for row in rows if row[mode]]
        return round(sum(values) / len(values), 4) if values else 0.0

    def pass_rate(mode: str) -> float:
        values = [row[mode] for row in rows if row[mode]]
        passed = sum(1 for item in values if all(score >= 0.6 for score in item["scores"].values()))
        return round(passed / len(values), 4) if values else 0.0

    return {
        "total": len(rows),
        "no_rag_pass_rate": pass_rate("no_rag"),
        "rag_pass_rate": pass_rate("rag"),
        "no_rag_avg": round(sum(sum(row["no_rag"]["scores"].values()) for row in rows if row["no_rag"]) / (len([r for r in rows if r["no_rag"]]) * len(JUDGE_DIMENSIONS)), 4),
        "rag_avg": round(sum(sum(row["rag"]["scores"].values()) for row in rows if row["rag"]) / (len([r for r in rows if r["rag"]]) * len(JUDGE_DIMENSIONS)), 4),
        "no_rag_dimensions": {dim: dim_average("no_rag", dim) for dim in JUDGE_DIMENSIONS},
        "rag_dimensions": {dim: dim_average("rag", dim) for dim in JUDGE_DIMENSIONS},
        # RAG 特有：有据率 = groundedness 均分；幻觉率 = no_hallucination < 0.6 的样本占比
        "rag_groundedness": dim_average("rag", "groundedness"),
        "rag_hallucination_rate": round(
            sum(1 for row in rows if row["rag"] and row["rag"]["scores"]["no_hallucination"] < 0.6) / len([r for r in rows if r["rag"]]),
            4,
        ),
    }


def print_report(metrics: dict) -> None:
    """打印两种模式的对比。"""
    print(f"总样本: {metrics['total']}")
    print(f"judge 通过率: 无RAG={metrics['no_rag_pass_rate']:.1%}，有RAG={metrics['rag_pass_rate']:.1%}")
    print(f"平均分: 无RAG={metrics['no_rag_avg']:.3f}，有RAG={metrics['rag_avg']:.3f}")
    print()
    print(f"{'维度':<16}{'无RAG':>10}{'有RAG':>10}{'差值':>10}")
    for dim in JUDGE_DIMENSIONS:
        no_rag = metrics["no_rag_dimensions"][dim]
        rag = metrics["rag_dimensions"][dim]
        print(f"{dim:<16}{no_rag:>10.3f}{rag:>10.3f}{rag - no_rag:>+10.3f}")
    print()
    print(f"RAG 有据率（groundedness 均分）: {metrics['rag_groundedness']:.3f}")
    print(f"RAG 幻觉率（no_hallucination < 0.6 占比）: {metrics['rag_hallucination_rate']:.1%}")


def save_baseline(metrics: dict, out_dir: Path) -> Path:
    """保存基线 JSON。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"domain": "rag_answer", "created_at": datetime.now().isoformat(timespec="seconds"), **metrics}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = [out_dir / f"rag_answer_{timestamp}.json", out_dir / "rag_answer_latest.json"]
    for path in paths:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return paths[-1]


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="RAG 回答评测 runner")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/rag_retrieval_seed.jsonl"))
    parser.add_argument("--limit", type=int, default=0, help="只评测前 N 条（0 表示全部）")
    parser.add_argument("--k", type=int, default=3, help="注入的知识片段数")
    parser.add_argument("--max-distance", type=float, default=1.30, help="检索距离阈值")
    parser.add_argument("--out-dir", type=Path, default=Path("evals/baselines"))
    parser.add_argument("--fail-below", type=float, default=None, help="RAG 有据率低于该阈值时门禁失败")
    parser.add_argument("--compare", type=Path, default=None)
    parser.add_argument("--max-regression", type=float, default=None)
    args = parser.parse_args()

    records = load_dataset(args.dataset)
    if args.limit > 0:
        records = records[: args.limit]

    started = time.perf_counter()
    metrics = evaluate(records, args.k, args.max_distance)
    elapsed = time.perf_counter() - started

    print("======== RAG 回答评测报告 ========")
    print(f"数据集: {args.dataset}（前 {len(records)} 条），耗时: {elapsed:.1f}s")
    print()
    print_report(metrics)

    passed = apply_gate(
        metrics,
        primary="rag_groundedness",
        fail_below=args.fail_below,
        compare=args.compare,
        max_regression=args.max_regression,
    )
    if not passed:
        raise SystemExit(1)
    saved = save_baseline(metrics, args.out_dir)
    print()
    print(f"基线已保存: {saved}")


if __name__ == "__main__":
    main()
