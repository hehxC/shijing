"""SQL 评测用的固定测试材料清单。

这些材料只存在于评测用的 SQLite 测试库里，与线上 MySQL materials 表完全隔离，
价格、单位、颜色、规格全部写死，保证评测可复现。
img 字段由种子脚本统一写入哨兵字符串（EVAL_IMG_SENTINEL_...），
用于检测模型是否把图片字段泄露进了回答。
"""

# 每条 = 一行 materials 表记录；cat 为分类，desc 为描述
FIXTURE_MATERIALS = [
    {"material": "莱姆石", "color": "米白", "spec": "600x600x20", "price": 65.00, "unit": "元/㎡", "cat": "石材", "desc": "户外地面铺装"},
    {"material": "青石板岩", "color": "青灰", "spec": "300x600x30", "price": 85.00, "unit": "元/㎡", "cat": "石材", "desc": "园路铺装"},
    {"material": "黄砂岩", "color": "黄褐", "spec": "600x300x25", "price": 58.00, "unit": "元/㎡", "cat": "石材", "desc": "墙面干挂"},
    {"material": "花岗岩", "color": "芝麻灰", "spec": "600x600x20", "price": 95.00, "unit": "元/㎡", "cat": "石材", "desc": "地面铺装"},
    {"material": "花岗岩", "color": "芝麻白", "spec": "600x600x20", "price": 108.00, "unit": "元/㎡", "cat": "石材", "desc": "地面铺装"},
    {"material": "水洗石", "color": "灰色", "spec": "散装", "price": 45.00, "unit": "元/吨", "cat": "骨料", "desc": "水洗石地面"},
    {"material": "鹅卵石", "color": "杂色", "spec": "3-5cm", "price": 32.00, "unit": "元/袋", "cat": "骨料", "desc": "水景观"},
    {"material": "白碎石", "color": "白色", "spec": "5-8cm", "price": 28.00, "unit": "元/袋", "cat": "骨料", "desc": "园路铺面"},
    {"material": "石英砖", "color": "暖灰", "spec": "600x600x18", "price": 120.00, "unit": "元/㎡", "cat": "砖材", "desc": "地面铺装"},
    {"material": "锈石", "color": "锈黄", "spec": "600x600x20", "price": 88.00, "unit": "元/㎡", "cat": "石材", "desc": "景墙"},
    {"material": "芝麻黑", "color": "黑色", "spec": "600x600x20", "price": 102.00, "unit": "元/㎡", "cat": "石材", "desc": "地面铺装"},
    {"material": "中国黑", "color": "黑色", "spec": "600x600x25", "price": 130.00, "unit": "元/㎡", "cat": "石材", "desc": "台阶踏步"},
    {"material": "蘑菇石", "color": "灰白", "spec": "200x400x50", "price": 75.00, "unit": "元/㎡", "cat": "石材", "desc": "外墙装饰"},
    {"material": "雨花石", "color": "多彩", "spec": "1-2cm", "price": 18.00, "unit": "元/袋", "cat": "骨料", "desc": "水景观"},
    {"material": "板岩", "color": "深灰", "spec": "600x300x20", "price": 66.00, "unit": "元/㎡", "cat": "石材", "desc": "地面铺装"},
    {"material": "大理石", "color": "白色", "spec": "800x800x20", "price": 260.00, "unit": "元/㎡", "cat": "石材", "desc": "室内台面"},
    {"material": "洞石", "color": "米黄", "spec": "600x600x20", "price": 240.00, "unit": "元/㎡", "cat": "石材", "desc": "墙面"},
    {"material": "透水砖", "color": "红色", "spec": "200x100x60", "price": 40.00, "unit": "元/㎡", "cat": "砖材", "desc": "停车位铺装"},
    {"material": "耐火砖", "color": "红褐色", "spec": "230x114x65", "price": 3.50, "unit": "元/块", "cat": "砖材", "desc": "壁炉烟道"},
    {"material": "文化石", "color": "仿古", "spec": "不规则", "price": 55.00, "unit": "元/㎡", "cat": "石材", "desc": "墙面装饰"},
    {"material": "碎拼石", "color": "杂色", "spec": "不规则", "price": 48.00, "unit": "元/㎡", "cat": "石材", "desc": "园路碎拼"},
    {"material": "青石", "color": "青灰", "spec": "600x600x30", "price": 90.00, "unit": "元/㎡", "cat": "石材", "desc": "台阶踏步"},
    {"material": "泰山石", "color": "灰色", "spec": "1-3吨", "price": 500.00, "unit": "元/吨", "cat": "景观石", "desc": "置石景观"},
]

# 图片字段的哨兵值：种子脚本写入，评测时只要回答里出现它就说明模型泄露了 img 字段
IMG_SENTINEL = "EVAL_IMG_SENTINEL_do_not_return_this"
