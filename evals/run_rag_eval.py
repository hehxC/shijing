"""检索评测 runner（任务 5）：recall@k / precision@k / MRR / nDCG@k。

相关性按"文档层"判定：检索返回的 chunk 只要其 doc_id 属于标注的相关文档即算命中。
这样换切分参数（chunk_size/overlap）后结果依然可比，才能评测不同切分策略。

用法（在项目根目录）：
    uv run python evals/run_rag_eval.py               # 全量
    uv run python evals/run_rag_eval.py --limit 5     # 冒烟
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.rag_service import retrieve  # noqa: E402
from evals.ci_gate import apply_gate  # noqa: E402

# 报告使用的 top-k 集合
KS = [1, 3, 5]


def load_dataset(path: Path) -> list[dict]:
    """读取检索评测数据集，校验必需字段。"""
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
            if not {"question", "relevant_docs"}.issubset(record):
                raise SystemExit(f"数据集第 {line_number} 行缺少必需字段")
            records.append(record)
    return records


def retrieve_doc_ids(question: str, k: int, retriever: str, max_distance: float | None) -> list[str]:
    """用指定检索器取 top-k，返回去重后的 doc_id 列表（保持相关度顺序）。"""
    items = retrieve(question, k=k, retriever=retriever, max_distance=max_distance)
    doc_ids: list[str] = []
    for item in items:
        doc_id = item.get("doc_id")
        if doc_id and doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return doc_ids


def reciprocal_rank(doc_ids: list[str], gold: set[str]) -> float:
    """MRR：第一个相关文档排名的倒数，没有则 0。"""
    for index, doc_id in enumerate(doc_ids, 1):
        if doc_id in gold:
            return 1.0 / index
    return 0.0


def ndcg_at_k(doc_ids: list[str], gold: set[str], k: int) -> float:
    """nDCG@k：二值相关性（相关=1），按理想排序归一化。"""
    dcg = 0.0
    for index, doc_id in enumerate(doc_ids[:k], 1):
        if doc_id in gold:
            dcg += 1.0 / math.log2(index + 1)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(gold))))
    return dcg / ideal if ideal > 0 else 0.0


def evaluate(records: list[dict], retriever: str, max_distance: float | None) -> dict:
    """逐条评测并汇总指标。"""
    recalls = {k: [] for k in KS}
    precisions = {k: [] for k in KS}
    mrrs: list[float] = []
    ndcgs: list[float] = []
    negative_hits = 0
    negative_total = 0
    per_category: dict[str, dict] = {}

    for record in records:
        question = record["question"]
        gold = set(record["relevant_docs"])
        category = record.get("category", "other")

        # 负样本：gold 为空，检索到任何文档都算一次误命中
        if not gold:
            negative_total += 1
            if retrieve_doc_ids(question, 1, retriever, max_distance):
                negative_hits += 1
            continue

        # 一次检索 top=max(KS)，按 k 切片复用，避免重复调用 embedding
        doc_ids = retrieve_doc_ids(question, max(KS), retriever, max_distance)
        for k in KS:
            top = doc_ids[:k]
            hits = sum(1 for doc_id in top if doc_id in gold)
            recalls[k].append(hits / len(gold))
            precisions[k].append(hits / k)
        mrrs.append(reciprocal_rank(doc_ids, gold))
        ndcgs.append(ndcg_at_k(doc_ids, gold, max(KS)))

        bucket = per_category.setdefault(category, {"total": 0, "recall_hits": 0})
        bucket["total"] += 1
        if set(doc_ids) & gold:
            bucket["recall_hits"] += 1

    total = sum(len(value) for value in recalls.values()) // len(KS) if records else 0
    return {
        "total": total,
        "negative_total": negative_total,
        "recall_at_1": round(sum(recalls[1]) / total, 4) if total else 0.0,
        "recall_at_3": round(sum(recalls[3]) / total, 4) if total else 0.0,
        "recall_at_5": round(sum(recalls[5]) / total, 4) if total else 0.0,
        "precision_at_1": round(sum(precisions[1]) / total, 4) if total else 0.0,
        "precision_at_3": round(sum(precisions[3]) / total, 4) if total else 0.0,
        "precision_at_5": round(sum(precisions[5]) / total, 4) if total else 0.0,
        "mrr": round(sum(mrrs) / total, 4) if total else 0.0,
        "ndcg_at_5": round(sum(ndcgs) / total, 4) if total else 0.0,
        "negative_top1_hit_rate": round(negative_hits / negative_total, 4) if negative_total else 0.0,
        "per_category": {
            category: {
                "total": bucket["total"],
                "recall_at_5": round(bucket["recall_hits"] / bucket["total"], 4),
            }
            for category, bucket in per_category.items()
        },
    }


def print_report(metrics: dict) -> None:
    """打印人类可读的报告。"""
    print(f"总样本: {metrics['total']}（负样本 {metrics['negative_total']} 条）")
    print(f"recall@1 / @3 / @5: {metrics['recall_at_1']:.1%} / {metrics['recall_at_3']:.1%} / {metrics['recall_at_5']:.1%}")
    print(f"precision@1 / @3 / @5: {metrics['precision_at_1']:.1%} / {metrics['precision_at_3']:.1%} / {metrics['precision_at_5']:.1%}")
    print(f"MRR: {metrics['mrr']:.3f}，nDCG@5: {metrics['ndcg_at_5']:.3f}")
    print(f"负样本 top-1 误命中率: {metrics['negative_top1_hit_rate']:.1%}（越低越好）")
    print("分类别 recall@5：")
    for category, item in metrics["per_category"].items():
        print(f"  {category}: {item['recall_at_5']:.1%}（{item['total']} 条）")


def print_misses(records: list[dict], retriever: str, max_distance: float | None) -> None:
    """列出 top-5 未命中任何 gold 文档的样本。"""
    misses = []
    for record in records:
        gold = set(record["relevant_docs"])
        if not gold:
            continue
        doc_ids = retrieve_doc_ids(record["question"], 5, retriever, max_distance)
        if not (set(doc_ids) & gold):
            misses.append(record)
    print(f"top-5 未命中的样本（共 {len(misses)} 条）：")
    for record in misses:
        print(f"  #{record['id']} [{record.get('category')}] {record['question']}")


def save_baseline(metrics: dict, out_dir: Path, latest: bool = True) -> Path:
    """保存基线 JSON：带时间戳一份；latest=True 时再写固定 latest。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"domain": "rag", "created_at": datetime.now().isoformat(timespec="seconds"), **metrics}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = [out_dir / f"rag_{timestamp}.json"]
    if latest:
        paths.append(out_dir / "rag_latest.json")
    for path in paths:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return paths[0]


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="RAG 检索评测 runner")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/rag_retrieval_seed.jsonl"))
    parser.add_argument("--limit", type=int, default=0, help="只评测前 N 条（0 表示全部）")
    parser.add_argument("--out-dir", type=Path, default=Path("evals/baselines"))
    parser.add_argument("--show-misses", action="store_true")
    parser.add_argument("--fail-below", type=float, default=None, help="recall@5 低于该阈值时门禁失败")
    parser.add_argument("--compare", type=Path, default=None, help="与指定基线 JSON 对比")
    parser.add_argument("--max-regression", type=float, default=None, help="相对基线允许的最大下降幅度")
    parser.add_argument("--retriever", choices=["vector", "hybrid"], default="vector", help="检索器")
    parser.add_argument("--max-distance", type=float, default=None, help="向量距离门控阈值（大于则拒绝）")
    args = parser.parse_args()

    if not args.dataset.is_file():
        raise SystemExit(f"找不到数据集：{args.dataset}，请先运行 evals/build_rag_seed_dataset.py")
    records = load_dataset(args.dataset)
    if args.limit > 0:
        records = records[: args.limit]

    started = time.perf_counter()
    metrics = evaluate(records, args.retriever, args.max_distance)
    elapsed = time.perf_counter() - started

    print("======== RAG 检索评测报告 ========")
    print(f"数据集: {args.dataset}，检索器: {args.retriever}，"
          f"max_distance: {args.max_distance}，耗时: {elapsed:.1f}s")
    print()
    print_report(metrics)
    if args.show_misses:
        print()
        print_misses(records, args.retriever, args.max_distance)

    # CI 门禁：先对比再保存，主指标用 recall@5
    passed = apply_gate(
        metrics,
        primary="recall_at_5",
        fail_below=args.fail_below,
        compare=args.compare,
        max_regression=args.max_regression,
    )
    if not passed:
        raise SystemExit(1)
    saved = save_baseline(metrics, args.out_dir, latest=(args.limit == 0))
    print()
    print(f"基线已保存: {saved}")


if __name__ == "__main__":
    main()
