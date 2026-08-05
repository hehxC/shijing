"""生成第一批意图路由评测用的合成数据集（"试卷"）。

运行方式（在项目根目录）：
    uv run python evals/build_seed_dataset.py

产物：
    evals/datasets/intent_routing_seed.jsonl

设计说明：
    - 每条数据包含：用户消息、会话状态（四个布尔值）、期望的路由结果（intent + use_image）；
    - 数据集混合了手写难例与模板组合样本，覆盖五个意图和关键状态组合；
    - 脚本是确定性的，重复运行会覆盖同一份 JSONL，保证评测可复现。
"""

import json
from pathlib import Path

# 输出文件路径：固定在 evals/datasets/ 下
OUT_PATH = Path(__file__).resolve().parent / "datasets" / "intent_routing_seed.jsonl"

# 领域词汇表：风格、材料、用途、面积，取材自项目内的 garden_styles 与材料库
STYLES = ["新中式", "宋式文人园", "枯山水", "侘寂", "现代东方极简", "英式乡村"]
MATERIALS = ["莱姆石", "花岗岩", "芝麻灰", "水洗石", "青石板岩", "黄砂岩"]
USAGES = ["围边", "台阶", "园路"]
AREAS = ["50平米", "80平", "120平方米"]


def state(
    uploaded: bool = False,
    reference: bool = False,
    generated: bool = False,
    material_analysis: bool = False,
) -> dict:
    """构造会话状态字典，与 route_chat_intent 的参数一一对应。"""
    return {
        "has_uploaded_image": uploaded,
        "has_reference_image": reference,
        "has_generated_image": generated,
        "has_material_analysis": material_analysis,
    }


def sample(
    message: str,
    st: dict,
    intent: str,
    use_image: str,
    note: str = "",
) -> dict:
    """构造一条评测样本：消息 + 状态 + 期望结果。"""
    return {
        "message": message,
        "state": st,
        "expected": {"intent": intent, "use_image": use_image},
        "note": note,
    }


# 手写样本：重点覆盖难例、状态依赖句和五个意图的基本表达
HANDCRAFTED = [
    # ---- 生成效果图（全新生成）----
    sample("帮我生成一张新中式效果图", state(reference=True), "generate_effect_image", "reference", "全新生成，有参考图"),
    sample("看看我家院子做成枯山水什么样，出一张图", state(reference=True), "generate_effect_image", "reference", "全新生成，有参考图"),
    sample("生成一张庭院改造效果图", state(), "generate_effect_image", "none", "无素材全新生成"),
    sample("我想看看地中海风格的庭院效果", state(reference=True), "generate_effect_image", "reference", "风格 + 效果图"),
    sample("按现代东方极简风格给我设计一个院子，出效果图", state(), "generate_effect_image", "none", "无素材全新生成"),
    sample("做一张侘寂风渲染图", state(), "generate_effect_image", "none", "简短生成请求"),
    sample("这个院子改造成英式乡村风会怎样，出个图", state(reference=True), "generate_effect_image", "reference", "改造场景"),
    sample("给我看看我家的院子改成日式会是什么样", state(reference=True), "generate_effect_image", "reference", "换风格出图"),
    sample("出一张宋式文人园的设计图", state(), "generate_effect_image", "none", "无素材全新生成"),
    # ---- 生成效果图（基于上一张效果图编辑）----
    sample("增加瓦片围边，再出一版", state(generated=True, reference=True), "generate_effect_image", "generated", "改图"),
    sample("把台阶换成水洗石重新生成", state(generated=True), "generate_effect_image", "generated", "改图"),
    sample("去掉水景，改一版效果图", state(generated=True), "generate_effect_image", "generated", "改图"),
    sample("再加一圈围边", state(generated=True), "generate_effect_image", "generated", "短句改图"),
    sample("再出一版", state(generated=True), "generate_effect_image", "generated", "状态依赖：有效果图时是改图"),
    sample("再出一版", state(generated=False, reference=True), "generate_effect_image", "reference", "状态依赖：无效果图时是全新生成"),
    sample("墙面粉成白色，更新效果图", state(generated=True), "generate_effect_image", "generated", "改图"),
    sample("换一种风格试试", state(generated=True), "generate_effect_image", "generated", "基于效果图换风格"),
    # ---- 图片分析 ----
    sample("看看这张图里用的是什么石头", state(uploaded=True), "analyze_image", "uploaded", "分析上传图"),
    sample("分析一下这张照片的铺装材料", state(uploaded=True), "analyze_image", "uploaded", "分析上传图"),
    sample("这张庭院图里的地面是什么材质", state(uploaded=True), "analyze_image", "uploaded", "分析上传图"),
    sample("上一张效果图里用了什么材料", state(generated=True), "analyze_image", "generated", "分析效果图"),
    sample("分析一下刚才生成的那张图", state(generated=True), "analyze_image", "generated", "分析效果图"),
    sample("这张效果图的水景是什么石材", state(generated=True), "analyze_image", "generated", "分析效果图"),
    sample("效果图里的地面铺的是什么", state(generated=True), "analyze_image", "generated", "分析效果图"),
    sample("我上传的参考图里有什么", state(reference=True), "analyze_image", "reference", "分析参考图"),
    # ---- 估价 ----
    sample("50平米院子铺莱姆石大概多少钱", state(), "estimate_price", "none", "面积 + 材料估价"),
    sample("花岗岩铺装多少钱一平", state(), "estimate_price", "none", "问单价"),
    sample("帮我估一下80平庭院的材料预算", state(), "estimate_price", "none", "预算估算"),
    sample("这个方案大概要花多少钱", state(material_analysis=True), "estimate_price", "none", "基于材料分析估价"),
    sample("水洗石单价多少", state(), "estimate_price", "none", "问单价"),
    sample("报价大概多少", state(), "estimate_price", "none", "模糊报价"),
    # ---- 材料查询 ----
    sample("莱姆石有什么规格", state(), "query_material", "none", "查规格"),
    sample("芝麻灰有哪些颜色", state(), "query_material", "none", "查颜色"),
    sample("花岗岩和莱姆石哪个更适合户外", state(), "query_material", "none", "材料对比咨询"),
    sample("材料库里有没有水洗石", state(), "query_material", "none", "查库存"),
    sample("黄砂岩的材质特点是什么", state(), "query_material", "none", "查材质描述"),
    sample("鹅卵石和砾石的区别", state(), "query_material", "none", "材料对比"),
    # ---- 普通闲聊 ----
    sample("你好", state(), "general_chat", "none", "打招呼"),
    sample("谢谢", state(), "general_chat", "none", "道谢"),
    sample("帮我推荐一下适合小院子的植物", state(), "general_chat", "none", "植物咨询"),
    sample("庭院设计要注意什么", state(), "general_chat", "none", "设计咨询"),
    sample("出图要多久", state(), "general_chat", "none", "含图但不是生成意图"),
    sample("我觉得这个方案不错", state(generated=True), "general_chat", "none", "评价方案"),
    sample("嗯", state(), "general_chat", "none", "无意义输入"),
    sample("台阶多高合适", state(), "general_chat", "none", "设计咨询不是改图"),
    # ---- 跨意图难例 ----
    sample("这个报价单帮我出个效果图", state(), "generate_effect_image", "none", "报价 vs 生成边界"),
    sample("把台阶改高一点", state(generated=True), "generate_effect_image", "generated", "有效果图时是改图"),
    sample("把台阶改高一点", state(generated=False), "general_chat", "none", "无效果图时更像咨询"),
    sample("石材价格表能看下吗", state(), "query_material", "none", "价格表是资料不是估价"),
    sample("帮我看看这个方案", state(generated=True), "analyze_image", "generated", "指代效果图"),
    sample("帮我看看这个方案", state(generated=False), "general_chat", "none", "无效果图时无法分析"),
    sample("生成效果图要收费吗", state(), "general_chat", "none", "问服务价格不是材料"),
    sample("铺地面的石材选什么好", state(), "query_material", "none", "选材咨询"),
    sample("这石头多少钱一块", state(uploaded=True), "estimate_price", "none", "有图但问价格"),
    sample("这石头是什么品种", state(uploaded=True), "analyze_image", "uploaded", "有图问品种"),
]


def build_templates() -> list[dict]:
    """用领域词汇表批量组合样本，扩大每个意图的句式覆盖面。"""
    generated: list[dict] = []

    # 生成类：风格名 × 句式模板（一半带参考图、一半不带）
    for style in STYLES:
        generated.append(sample(f"帮我生成一张{style}效果图", state(reference=True), "generate_effect_image", "reference"))
        generated.append(sample(f"出一张{style}庭院设计图", state(), "generate_effect_image", "none"))
        generated.append(sample(f"我想看看{style}的院子效果", state(reference=True), "generate_effect_image", "reference"))
        generated.append(sample(f"把院子做成{style}，出一张效果图", state(reference=True), "generate_effect_image", "reference"))

    # 编辑类：用途 × 编辑句式，必须有已生成的效果图
    for usage in USAGES:
        generated.append(sample(f"再加一圈{usage}，重新生成", state(generated=True), "generate_effect_image", "generated"))
        generated.append(sample(f"把{usage}换成水洗石再出一版", state(generated=True), "generate_effect_image", "generated"))

    # 估价类：面积 × 材料
    for area in AREAS:
        for material in MATERIALS[:4]:
            generated.append(sample(f"{area}院子铺{material}大概多少钱", state(), "estimate_price", "none"))
    for material in MATERIALS:
        generated.append(sample(f"{material}铺装多少钱一平", state(), "estimate_price", "none"))

    # 查询类：材料 × 查询句式
    for material in MATERIALS:
        generated.append(sample(f"{material}有什么规格", state(), "query_material", "none"))
    for material in MATERIALS[:4]:
        generated.append(sample(f"{material}有哪些颜色", state(), "query_material", "none"))
        generated.append(sample(f"材料库里有没有{material}", state(), "query_material", "none"))

    # 闲聊类：固定句式
    generated.append(sample("院子里乔木怎么搭配", state(), "general_chat", "none"))
    generated.append(sample("院子里灌木怎么搭配", state(), "general_chat", "none"))
    generated.append(sample("帮我规划一下庭院功能区", state(), "general_chat", "none"))
    generated.append(sample("小院子适合什么风格", state(), "general_chat", "none"))
    return generated


def main() -> None:
    """组合手写样本与模板样本，去重后写入 JSONL。"""
    samples = HANDCRAFTED + build_templates()

    # 去重键 = 消息 + 状态：同一句话在不同状态下是合法的不同样本（如"再出一版"）
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for item in samples:
        key = (item["message"], json.dumps(item["state"], ensure_ascii=False, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    # 按意图归类排序，保证输出稳定；按顺序编号
    order = {"generate_effect_image": 0, "analyze_image": 1, "estimate_price": 2, "query_material": 3, "general_chat": 4}
    unique.sort(key=lambda item: (order.get(item["expected"]["intent"], 9), item["message"]))
    records = [{"id": index + 1, **item} for index, item in enumerate(unique)]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 输出统计：总数 + 每个意图的样本数，方便快速核对覆盖面
    counts: dict[str, int] = {}
    for record in records:
        intent = record["expected"]["intent"]
        counts[intent] = counts.get(intent, 0) + 1
    print(f"已生成 {len(records)} 条样本 -> {OUT_PATH}")
    for intent, count in counts.items():
        print(f"  {intent}: {count} 条")


if __name__ == "__main__":
    main()
