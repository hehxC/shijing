"""生成检索评测数据集（任务 4）。

每条样本：问题 + 相关的文档 id 集合。标注在 doc 层而非 chunk 层——
换切分参数时 chunk id 会变，而文档相关性稳定，这样才能公平对比不同切分策略。

用法：
    uv run python evals/build_rag_seed_dataset.py
产物：
    evals/datasets/rag_retrieval_seed.jsonl
"""

import json
from pathlib import Path

from evals.fixtures.knowledge_documents import KNOWLEDGE_DOCUMENTS

OUT_PATH = Path(__file__).resolve().parent / "datasets" / "rag_retrieval_seed.jsonl"

# 合法文档 id 集合，生成时校验标注有效
VALID_DOC_IDS = {doc["id"] for doc in KNOWLEDGE_DOCUMENTS}


def sample(question: str, relevant_docs: list[str], category: str, note: str = "") -> dict:
    """构造一条检索评测样本。"""
    return {
        "question": question,
        "relevant_docs": relevant_docs,
        "category": category,
        "note": note,
    }


# 手写样本：按类别组织，相关文档从知识源里人工标注
SEED_SAMPLES: list[dict] = [
    # ---- 风格类 ----
    sample("新中式庭院有什么特点", ["new-chinese"], "style"),
    sample("想做一个枯山水小庭院，怎么设计", ["zen-dry-garden"], "style"),
    sample("宋式文人园适合什么院子", ["song-scholar-garden"], "style"),
    sample("法式规则园怎么布局", ["french-formal-garden"], "style"),
    sample("英式乡村花园用什么材料铺路", ["english-cottage-garden", "guide-material-selection"], "style"),
    sample("苔庭适合什么气候", ["moss-rain-garden"], "style"),
    sample("工业风庭院怎么选材", ["industrial-modern", "guide-material-selection"], "style"),
    sample("茶禅一味庭院怎么设计", ["tea-garden-zen"], "style"),
    sample("极简白盒子风格的核心是什么", ["minimalist-white-box"], "style"),
    sample("地中海庭院用什么植物", ["mediterranean-courtyard", "guide-planting"], "style"),
    sample("自然野趣风怎么种花", ["naturalistic-garden", "guide-planting"], "style"),
    sample("日式池泉庭需要什么系统", ["japanese-pond-garden"], "style"),
    sample("侘寂风用什么材料", ["wabi-sabi-natural", "guide-material-selection"], "style"),
    sample("现代东方极简用什么铺装", ["modern-east-asian-minimal", "guide-material-selection"], "style"),
    sample("美式农场风适合什么家庭", ["american-farmhouse"], "style"),
    sample("明清合院布局讲究什么", ["ming-qing-courtyard"], "style"),
    sample("南洋热带园有什么特点", ["tropical-nanyang"], "style"),
    sample("热带雨林风适合什么气候", ["tropical-jungle"], "style"),
    sample("中式庭院常用什么植物", ["new-chinese", "guide-planting"], "style"),
    sample("想做个禅意小庭院，选枯山水还是茶庭", ["zen-dry-garden", "tea-garden-zen"], "style", "对比类"),
    # ---- 材料类 ----
    sample("庭院地面铺装什么材料防滑耐磨", ["guide-material-selection"], "material"),
    sample("莱姆石适合铺在哪里", ["guide-material-selection"], "material"),
    sample("花岗岩和青石板哪个更适合户外", ["guide-material-selection"], "material"),
    sample("鹅卵石能铺主园路吗", ["guide-material-selection"], "material"),
    sample("水景底部用什么材料", ["guide-material-selection"], "material"),
    sample("石英砖适合什么风格", ["guide-material-selection"], "material"),
    sample("透水砖铺停车位要注意什么", ["guide-material-selection", "guide-construction"], "material"),
    sample("墙面石材怎么选", ["guide-material-selection"], "material"),
    sample("黄砂岩能用于重载地面吗", ["guide-material-selection"], "material"),
    sample("铺装收边用什么材料", ["guide-material-selection", "guide-construction"], "material"),
    # ---- 植物类 ----
    sample("中式庭院种什么树", ["guide-planting", "new-chinese"], "plant"),
    sample("苔藓养护要注意什么", ["guide-planting", "moss-rain-garden"], "plant"),
    sample("庭院植物怎么分层搭配", ["guide-planting"], "plant"),
    sample("水景边种什么植物", ["guide-planting"], "plant"),
    sample("紫藤适合种在哪里", ["guide-planting"], "plant"),
    sample("观赏草适合什么风格", ["guide-planting", "naturalistic-garden", "modern-east-asian-minimal"], "plant"),
    sample("庭院种桂花要注意什么", ["guide-planting"], "plant"),
    sample("自然风格花园用什么花", ["guide-planting", "naturalistic-garden"], "plant"),
    # ---- 施工类 ----
    sample("庭院排水怎么做", ["guide-construction"], "construction"),
    sample("铺装前基层要做什么", ["guide-construction"], "construction"),
    sample("水景施工的防水怎么做", ["guide-construction"], "construction"),
    sample("户外铺装怎么防滑", ["guide-construction"], "construction"),
    sample("墙面石材干挂要注意什么", ["guide-construction", "guide-material-selection"], "construction"),
    sample("庭院照明怎么布置", ["guide-construction"], "construction"),
    sample("隐私遮挡怎么做", ["guide-construction"], "construction"),
    # ---- 布局类 ----
    sample("小庭院怎么设计显大", ["guide-layout"], "layout"),
    sample("别墅庭院怎么分区设计", ["guide-layout"], "layout"),
    sample("庭院园路宽度多少合适", ["guide-layout"], "layout"),
    sample("儿童活动区地面用什么", ["guide-layout"], "layout"),
    sample("菜园放在庭院哪里好", ["guide-layout"], "layout"),
    # ---- 组合/跨类 ----
    sample("新中式庭院地面和植物怎么配", ["new-chinese", "guide-material-selection", "guide-planting"], "mixed"),
    sample("想做一个有水景的日式庭院", ["japanese-pond-garden", "guide-construction"], "mixed"),
    sample("现代风格庭院怎么选材和种树", ["modern-east-asian-minimal", "guide-material-selection", "guide-planting"], "mixed"),
    sample("枯山水维护要注意什么", ["zen-dry-garden", "guide-construction"], "mixed"),
    sample("英式花园需要多少养护", ["english-cottage-garden", "guide-planting"], "mixed"),
    # ---- 无匹配（负样本）----
    sample("帮我查一下周末天气", [], "negative"),
    sample("推荐一部好看的电影", [], "negative"),
    sample("今天股票行情怎么样", [], "negative"),
]


def main() -> None:
    """校验标注、去重后写入 JSONL。"""
    # 校验：相关文档 id 必须存在于知识源，否则说明标注或知识源有误
    invalid: list[str] = []
    for item in SEED_SAMPLES:
        for doc_id in item["relevant_docs"]:
            if doc_id not in VALID_DOC_IDS:
                invalid.append(f"{item['question']} -> {doc_id}")
    if invalid:
        raise SystemExit("标注了不存在的文档 id：\n" + "\n".join(invalid))

    # 按问题去重，保持手写顺序，按 id 编号
    seen: set[str] = set()
    records: list[dict] = []
    for item in SEED_SAMPLES:
        if item["question"] in seen:
            continue
        seen.add(item["question"])
        records.append({"id": len(records) + 1, **item})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    from collections import Counter

    counts = Counter(record["category"] for record in records)
    print(f"已生成 {len(records)} 条检索评测样本 -> {OUT_PATH}")
    for category, count in counts.items():
        print(f"  {category}: {count} 条")


if __name__ == "__main__":
    main()
