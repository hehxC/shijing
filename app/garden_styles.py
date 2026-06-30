"""内置庭院风格目录。

风格属于稳定的产品策展数据，不写入材料库；前端和文生图服务均通过本模块使用同一份定义。
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GardenStyle:
    id: str
    name: str
    category: str
    description: str


GARDEN_STYLES: tuple[GardenStyle, ...] = (
    GardenStyle(
        "new-chinese",
        "新中式",
        "中式·当代",
        "当下最受欢迎的中式演绎：保留青砖、竹格栅、漏窗等中国文化符号，以现代材料和简洁线条重组，有中国味但不复古沉重。",
    ),
    GardenStyle(
        "song-scholar-garden",
        "宋式文人园",
        "中式·宋",
        "取法宋人山水画意，以太湖石叠山，白墙黛瓦为底，修竹梅花为骨，追求清雅脱俗的文人气韵。",
    ),
    GardenStyle(
        "ming-qing-courtyard",
        "明清合院",
        "中式·明清",
        "方正严谨的合院布局，莱姆石铺地，青砖影壁，搭配天井与盆景，体现礼制秩序与自然情趣的平衡。",
    ),
    GardenStyle(
        "zen-dry-garden",
        "枯山水禅庭",
        "日式·禅",
        "以白碎石耙纹象征水波，配以青板岩踏石，苔藓点缀，营造静谧禅意空间。",
    ),
    GardenStyle(
        "japanese-pond-garden",
        "日式池泉庭",
        "日式·池泉",
        "鹅卵石铺就曲径，花岗岩踏石引路，红枫与石灯笼点缀，四季皆有景致。",
    ),
    GardenStyle(
        "moss-rain-garden",
        "苔庭·雨露风",
        "日式·苔庭",
        "以大面积青苔为地毯，鹅卵石溪流蜿蜒，石灯笼半掩苔痕，湿润阴翳中透出生机，尤适成都多雨气候。",
    ),
    GardenStyle(
        "wabi-sabi-natural",
        "侘寂自然风",
        "日式·侘寂",
        "接受不完美与无常，以自然风化石材、野生苔藓、枯木残枝构建庭院，在岁月痕迹中寻见宁静与深邃。",
    ),
    GardenStyle(
        "modern-east-asian-minimal",
        "现代东方极简",
        "融合·现代",
        "以现代简约手法重诠东方留白美学，花岗岩大板铺地，清水混凝土墙面，线形水景倒映天光，去繁归简。",
    ),
    GardenStyle(
        "industrial-modern",
        "工业现代风",
        "现代·工业",
        "耐候钢板自然锈蚀成暖棕色，清水混凝土墙配砾石地面，铁艺构架点缀观赏草，粗粝中透出克制的当代气质。",
    ),
    GardenStyle(
        "minimalist-white-box",
        "极简白盒子",
        "现代·极简",
        "纯白墙面与浅色大板铺地，以比例和光影为设计语言，减去一切多余元素，让空间本身成为主角。",
    ),
    GardenStyle(
        "english-cottage-garden",
        "英式乡村花园",
        "欧式·英",
        "自由生长的宿根花卉溢满边界，莱姆石小径蜿蜒其间，铸铁园艺装饰点缀，浪漫而不失野趣的英伦乡村气息。",
    ),
    GardenStyle(
        "french-formal-garden",
        "法式规则园",
        "欧式·法",
        "笛卡尔式几何美学，严格对称轴线，花岗岩铺就放射形园路，修剪精整的黄杨绿篱，中央喷泉为视觉焦点。",
    ),
    GardenStyle(
        "mediterranean-courtyard",
        "地中海庭院",
        "欧式·地中海",
        "粗犷石灰岩铺地，赭红陶罐盛满薰衣草，古老橄榄树撑起一片荫凉，藤架缠绕葡萄，尽是南欧慵懒诗意。",
    ),
    GardenStyle(
        "american-farmhouse",
        "美式农场风",
        "欧式·美",
        "白色木栅栏围合大草坪，碎石小路串联花境与菜园，秋千与壁炉台点缀其间，实用、放松、充满生活气息。",
    ),
    GardenStyle(
        "tropical-nanyang",
        "南洋热带园",
        "热带·南洋",
        "融合中式与东南亚热带风情，黄砂岩铺地，茂密热带植物，流水叮咚，呈现闽南华侨庭院的南洋情调。",
    ),
    GardenStyle(
        "tropical-jungle",
        "热带雨林风",
        "热带·雨林",
        "浓密植物层层叠叠，芭蕉与龟背竹营造丛林感，岩石叠层引水成瀑，成都夏季的湿热气候是天然优势。",
    ),
    GardenStyle(
        "naturalistic-garden",
        "自然野趣风",
        "自然",
        "模仿自然生态群落，自播野花、观赏草与本土植物混种，自然石随意散落，减少人工干预，与自然共生。",
    ),
    GardenStyle(
        "tea-garden-zen",
        "茶禅一味",
        "中日·茶境",
        "以茶事为核心动线，青板岩铺就的入茶之路，竹篱围合静谧茶席空间，流水声掩去城市喧嚣，专为品茗而造。",
    ),
)

_STYLES_BY_ID = {style.id: style for style in GARDEN_STYLES}


def list_garden_styles() -> list[dict[str, str]]:
    """返回可直接提供给前端的风格目录。"""
    return [asdict(style) for style in GARDEN_STYLES]


def get_garden_style(style_id: str | None) -> GardenStyle | None:
    return _STYLES_BY_ID.get(style_id or "")


def build_style_generation_request(style: GardenStyle, user_request: str) -> str:
    """构造文生图的确定性设计约束，不依赖模型猜测用户选中的风格。"""
    cleaned_request = user_request.strip() or "未提供额外要求，请完整呈现该风格的庭院设计。"
    return (
        "用户已明确选择庭院设计风格，必须将其作为核心设计约束。\n"
        f"风格名称：{style.name}\n"
        f"风格分类：{style.category}\n"
        f"风格设计说明：{style.description}\n"
        f"用户补充需求：{cleaned_request}"
    )
