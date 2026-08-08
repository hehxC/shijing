"""意图路由评测 runner（"阅卷机"）。

作用：
    逐条读取评测数据集，把消息和状态喂给路由，拿实际结果与期望结果比对，
    统计总准确率、各意图精确率/召回率/F1、混淆矩阵，并把结果保存为基线文件。

运行方式（在项目根目录）：
    # 纯规则模式：直接跑正则兜底逻辑，确定性、不调用任何模型
    uv run python evals/run_intent_router_eval.py --mode rule

    # 混合模式：走真实 route_chat_intent（部分样本会调用 LLM），可用 --limit 限制条数
    uv run python evals/run_intent_router_eval.py --mode hybrid --limit 30
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 把项目根目录加入导入路径，保证可以从 evals/ 目录运行脚本导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.service.chat_intent_router import _fallback_route, route_chat_intent  # noqa: E402
from evals.ci_gate import apply_gate  # noqa: E402

# 五个意图的固定顺序，用于报表和混淆矩阵的列排序
INTENT_ORDER = [
    "generate_effect_image",
    "analyze_image",
    "estimate_price",
    "query_material",
    "general_chat",
]


def load_dataset(path: Path) -> list[dict]:
    """读取 JSONL 数据集，校验必需字段并返回记录列表。"""
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
            # 必需字段缺失时直接报错，避免静默产出错误评测结果
            required = {"message", "state", "expected"}
            if not required.issubset(record):
                raise SystemExit(f"数据集第 {line_number} 行缺少字段：{required - set(record)}")
            records.append(record)
    return records


def predict_rule(message: str, st: dict):
    """纯规则模式：直接调用正则兜底逻辑，不经过 LLM。"""
    return _fallback_route(message, **st)


def predict_hybrid(message: str, st: dict):
    """混合模式：走真实 route_chat_intent（生成/分析类仍由规则直返，其余走 LLM）。

    路由模型已固定为 DeepSeek（ROUTER_MODEL），不再接收 text_chat_model 参数。
    """
    return route_chat_intent(message, **st)


def compute_metrics(records: list[dict], predictions: list) -> dict:
    """根据期望与预测计算各项指标。"""
    total = len(records)
    intent_hits = 0
    image_hits = 0

    # 混淆矩阵：expected -> {predicted: count}
    confusion: dict[str, dict[str, int]] = {}
    # 每个意图的 tp/fp/fn 计数
    per_intent: dict[str, dict[str, int]] = {intent: {"tp": 0, "fp": 0, "fn": 0} for intent in INTENT_ORDER}

    for record, predicted in zip(records, predictions):
        expected_intent = record["expected"]["intent"]
        expected_image = record["expected"].get("use_image", "none")
        actual_intent = predicted.intent
        actual_image = predicted.use_image

        # 意图正确数：完整结果正确才算数
        if actual_intent == expected_intent:
            intent_hits += 1
        # 图片来源正确数（单独统计，作为次级指标）
        if actual_image == expected_image:
            image_hits += 1

        # 混淆矩阵计数
        confusion.setdefault(expected_intent, {})
        confusion[expected_intent][actual_intent] = confusion[expected_intent].get(actual_intent, 0) + 1

        # 逐意图指标计数
        if actual_intent == expected_intent:
            per_intent[expected_intent]["tp"] += 1
        else:
            per_intent[expected_intent]["fn"] += 1
            per_intent[actual_intent]["fp"] += 1

    # 计算每个意图的精确率、召回率、F1
    per_intent_metrics = {}
    for intent, counts in per_intent.items():
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_intent_metrics[intent] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "samples": tp + fn,
        }

    return {
        "total": total,
        "intent_accuracy": round(intent_hits / total, 4) if total else 0.0,
        "use_image_accuracy": round(image_hits / total, 4) if total else 0.0,
        "intent_hits": intent_hits,
        "image_hits": image_hits,
        "per_intent": per_intent_metrics,
        "confusion": confusion,
    }


def print_report(metrics: dict) -> None:
    """把指标打印成人类可读的报告。"""
    total = metrics["total"]
    print(f"总准确率（意图）: {metrics['intent_accuracy']:.1%} ({metrics['intent_hits']}/{total})")
    print(f"use_image 准确率: {metrics['use_image_accuracy']:.1%} ({metrics['image_hits']}/{total})")
    print()
    print("各意图指标：")
    print(f"{'意图':<24}{'精确率':>8}{'召回率':>8}{'F1':>8}{'样本数':>8}")
    for intent in INTENT_ORDER:
        item = metrics["per_intent"][intent]
        print(f"{intent:<24}{item['precision']:>8.1%}{item['recall']:>8.1%}{item['f1']:>8.3f}{item['samples']:>8}")
    print()
    print("混淆矩阵（行 = 期望意图，列 = 实际意图）：")
    # 只保留实际出现过的意图作为列，避免空列
    columns = [intent for intent in INTENT_ORDER if any(intent in row for row in metrics["confusion"].values())]
    header = f"{'':<24}" + "".join(f"{intent[:12]:>14}" for intent in columns)
    print(header)
    for expected in INTENT_ORDER:
        row = metrics["confusion"].get(expected, {})
        if not row:
            continue
        cells = "".join(f"{row.get(intent, 0):>14}" for intent in columns)
        print(f"{expected:<24}{cells}")


def _state_summary(st: dict) -> str:
    """把会话状态压缩成一行可读文字，方便快速对照。"""
    flags = [
        ("上传图", st.get("has_uploaded_image", False)),
        ("参考图", st.get("has_reference_image", False)),
        ("效果图", st.get("has_generated_image", False)),
    ]
    return " ".join(f"{label}={('是' if value else '否')}" for label, value in flags)


def print_misses(records: list[dict], predictions: list) -> None:
    """逐条列出意图误判的样本：消息、期望 vs 实际、状态、备注。"""
    misses = [
        (record, predicted)
        for record, predicted in zip(records, predictions)
        if predicted.intent != record["expected"]["intent"]
    ]
    print(f"意图误判样本（共 {len(misses)} 条）：")
    if not misses:
        print("  无")
        return
    for record, predicted in misses:
        expected = record["expected"]
        # 意图和图片来源都匹配才算完全正确，这里标出图片来源是否也判错
        image_mismatch = predicted.use_image != expected.get("use_image", "none")
        suffix = "（use_image 也不匹配）" if image_mismatch else ""
        print(f"  #{record['id']} 期望={expected['intent']} 实际={predicted.intent}{suffix}")
        print(f"    消息: {record['message']}")
        print(f"    状态: {_state_summary(record['state'])}")
        if record.get("note"):
            print(f"    备注: {record['note']}")
        print()


def save_baseline(metrics: dict, mode: str, dataset_path: Path, out_dir: Path, latest: bool = True) -> Path:
    """把指标保存为基线 JSON：一份带时间戳；latest=True 时再写固定 latest 便于对比（冒烟不覆盖）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": mode,
        "dataset": str(dataset_path),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **metrics,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    paths = [out_dir / f"{mode}_{timestamp}.json"]
    if latest:
        paths.append(out_dir / f"{mode}_latest.json")
    for path in paths:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
    return paths[0]


def main() -> None:
    """命令行入口：解析参数、跑评测、打印报告并保存基线。"""
    parser = argparse.ArgumentParser(description="意图路由评测 runner")
    parser.add_argument("--dataset", type=Path, default=Path("evals/datasets/intent_routing_seed.jsonl"), help="评测数据集路径")
    parser.add_argument("--mode", choices=["rule", "hybrid", "both"], default="rule", help="评测模式")
    parser.add_argument("--limit", type=int, default=0, help="只评测前 N 条（0 表示全部），用于快速冒烟")
    parser.add_argument("--out-dir", type=Path, default=Path("evals/baselines"), help="基线文件输出目录")
    parser.add_argument("--show-misses", action="store_true", help="逐条列出意图误判的样本")
    parser.add_argument("--fail-below", type=float, default=None, help="意图准确率低于该阈值时门禁失败")
    parser.add_argument("--compare", type=Path, default=None, help="与指定基线 JSON 对比（用于 CI 回归）")
    parser.add_argument("--max-regression", type=float, default=None, help="相对基线允许的最大下降幅度（如 0.02 表示 2 个百分点）")
    args = parser.parse_args()

    # 数据集不存在时给出明确提示，而不是神秘报错
    if not args.dataset.is_file():
        raise SystemExit(f"找不到数据集：{args.dataset}，请先运行 evals/build_seed_dataset.py 生成")
    records = load_dataset(args.dataset)
    if args.limit > 0:
        records = records[: args.limit]

    # 混合模式依赖 DEEPSEEK_API_KEY，缺失时提醒但不阻止（会自动回退规则）
    if args.mode in {"hybrid", "both"} and not os.getenv("DEEPSEEK_API_KEY"):
        print("警告：未检测到 DEEPSEEK_API_KEY，混合模式会全部回退到规则结果")

    modes = ["rule", "hybrid"] if args.mode == "both" else [args.mode]
    for mode in modes:
        predictions = []
        started = time.perf_counter()
        for record in records:
            message = record["message"]
            st = record["state"]
            if mode == "rule":
                predictions.append(predict_rule(message, st))
            else:
                # 评测期间的决策日志重定向到 evals/logs/，避免污染真实流量采集日志
                from app.service import chat_intent_router as cir

                eval_log_dir = Path(__file__).resolve().parent / "logs"
                cir.ROUTER_DECISION_LOG_PATH = eval_log_dir / f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
                predictions.append(predict_hybrid(message, st))
        elapsed = time.perf_counter() - started

        metrics = compute_metrics(records, predictions)
        print(f"======== 意图路由评测报告 (mode={mode}) ========")
        print(f"数据集: {args.dataset}（{metrics['total']} 条）")
        print(f"耗时: {elapsed:.1f}s")
        print()
        print_report(metrics)
        # 误判样本是改进的第一手证据，默认不打印，需要时用 --show-misses 打开
        if args.show_misses:
            print()
            print_misses(records, predictions)

        # CI 门禁：按阈值和基线对比决定退出码，供 workflow 拦截回归
        # 必须先于 save_baseline 执行，否则对比的是刚覆盖的基线文件（等于和自己比）
        passed = apply_gate(
            metrics,
            primary="intent_accuracy",
            fail_below=args.fail_below,
            compare=args.compare,
            max_regression=args.max_regression,
        )
        if not passed:
            raise SystemExit(1)

        # 冒烟（--limit）只写时间戳存档，避免污染 CI 对比用的 latest 参考基线
        saved = save_baseline(metrics, mode, args.dataset, args.out_dir, latest=(args.limit == 0))
        print()
        print(f"基线已保存: {saved}")
        print()


if __name__ == "__main__":
    main()
