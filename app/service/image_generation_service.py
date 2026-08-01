import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models.material import Material


load_dotenv()

GEMINI_IMAGE_MODEL = (
    os.getenv("GEMINI_IMAGE_MODEL")
    or os.getenv("IMAGE_MODEL")
    or "gemini-3.1-flash-image"
)
GEMINI_IMAGE_ENDPOINT = (
    os.getenv("GEMINI_IMAGE_ENDPOINT")
    or os.getenv("GEMINI_IMAGE_ENDPOINT_TEMPLATE")
    or "https://generativelanguage.googleapis.com/v1beta/interactions"
)
GENERATED_DIR = Path(__file__).resolve().parents[2] / "static" / "generated"
MAX_PROMPT_MATERIALS = int(
    os.getenv("IMAGE_PROMPT_MATERIAL_LIMIT")
    or os.getenv("QWEN_IMAGE_PROMPT_MATERIAL_LIMIT")
    or "24"
)

_RENDERING_PATTERNS = (
    re.compile(r"(?:生成|制作|创建|画|绘制|设计|出|做).{0,16}(?:效果图|渲染图|图片|图像|设计图|概念图)"),
    re.compile(r"(?:想要|需要|给我|帮我).{0,16}(?:效果图|渲染图|设计图|概念图)"),
    re.compile(r"(?:按照|参考|基于).{0,16}(?:图片|照片|风格).{0,12}(?:改造|生成|设计)"),
    re.compile(r"(?:装修|改造|布置)(?:后|完|一下).{0,10}(?:什么样|效果|图片|图)"),
    re.compile(r"(?:再来|再出|再做|再生成|重新生成|重做|另做|另出).{0,8}(?:一版|一个|一张|一下|方案|效果图|渲染图|图)"),
    re.compile(r"(?:换成|改成|做成|变成|调整成|改为|换为).{0,16}(?:中式|新中式|欧式|现代|侘寂|奶油|轻奢|极简|法式|美式|日式|原木|工业|地中海|现代风|欧式风格|中式风格|风格)"),
    re.compile(r"(?:按|按照|参考|基于|沿用).{0,10}(?:这个图|这张图|刚才.{0,6}图|上张图|上一张|原图|参考图|图片|照片).{0,16}(?:改造|生成|设计|出图|做|做成|换成|改成)"),
    re.compile(r"(?:做|来|出|给我|帮我).{0,10}(?:另一版|另外一版|不同方案|新方案|备选方案|不同风格|换个风格|另一个风格)"),
    re.compile(r"(?:换个|换一种|换一套|改个|改一种).{0,8}(?:风格|方案|效果)"),
)
_IMAGE_EDIT_ACTION_PATTERN = re.compile(
    r"(?:增加|添加|加上|加入|补上|放上|放置|铺上|铺设|装上|围上|加一圈|围一圈|"
    r"改一下|修改|调整|优化|去掉|移除|删除|减少|替换|换掉|换成|改成|改为|换为)"
)
_IMAGE_EDIT_TARGET_PATTERN = re.compile(
    r"(?:围边|收边|边框|边缘|瓦片|瓷砖|砖|石材|石头|地面|铺地|墙面|台面|花池|"
    r"水景|喷泉|灯|灯带|植物|树|草坪|围栏|栏杆|座椅|休息区|品茶区|茶区|"
    r"汀步|踏步|小路|路径|景观|庭院|院子)"
)
_IMAGE_EDIT_REFERENCE_PATTERN = re.compile(
    r"(?:效果图|渲染图|刚才.{0,8}图|上一张|上张图|这张图|那张图|图里|图片里|画面里|里面).{0,24}"
    r"(?:增加|添加|加上|加入|补上|放上|放置|铺上|铺设|装上|改一下|修改|调整|优化|去掉|移除|删除|替换)"
)
_NON_RENDERING_QUESTION_PATTERN = re.compile(
    r"(?:什么|哪种|哪些|多少|多少钱|价格|报价|估价|预估|预算|单价|"
    r"石头|石材|材料|用料|地上|地面|墙面|台面|规格|颜色).{0,12}(?:是什么|有哪些|多少|多少钱|价格|报价|估价|预估|预算|单价|吗|呢|？|\?)"
    r"|(?:是什么|有哪些|多少|多少钱|价格|报价|估价|预估|预算|单价)"
)
_GENERATED_IMAGE_REFERENCE_PATTERN = re.compile(
    r"(?:效果图|渲染图|刚才.{0,8}图|上一张|上张图|这张图|那张图|图里|图片里|画面里|里面|"
    r"(?:用|铺|选).{0,6}(?:什么|哪种).{0,6}(?:石头|石材|材料)|(?:什么|哪种)(?:石头|石材|材料))"
)


class ImageGenerationError(RuntimeError):
    pass


def is_effect_image_edit_request(message: str) -> bool:
    text = message.strip()
    if not text or _NON_RENDERING_QUESTION_PATTERN.search(text):
        return False
    return bool(
        _IMAGE_EDIT_REFERENCE_PATTERN.search(text)
        or (
            _IMAGE_EDIT_ACTION_PATTERN.search(text)
            and _IMAGE_EDIT_TARGET_PATTERN.search(text)
        )
    )


def needs_effect_image(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    if _NON_RENDERING_QUESTION_PATTERN.search(text):
        return False
    if is_effect_image_edit_request(text):
        return True
    return any(pattern.search(text) for pattern in _RENDERING_PATTERNS)


def references_generated_image(message: str) -> bool:
    return bool(_GENERATED_IMAGE_REFERENCE_PATTERN.search(message.strip()))


def generated_image_as_data_url(image_url: str) -> str:
    prefix = "/static/generated/"
    if not image_url.startswith(prefix):
        raise ImageGenerationError("会话中的效果图地址无效")

    filename = image_url.removeprefix(prefix)
    path = (GENERATED_DIR / filename).resolve()
    generated_root = GENERATED_DIR.resolve()
    if path.parent != generated_root or not path.is_file():
        raise ImageGenerationError("上一张效果图文件已不存在")

    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"

def _build_prompt(message: str, has_reference_image: bool) -> str:
    user_request = message.strip() or "生成一张庭院装修效果图"
    reference_rule = (
        "以输入图片为参考，保留其空间结构、透视关系和主要建筑边界，按照用户要求完成装修设计。"
        if has_reference_image
        else "根据用户的文字描述完成完整的空间设计。"
    )
    return (
        f"{reference_rule}\n"
        f"用户需求：{user_request}\n"
        "生成专业建筑可视化效果图，空间尺度合理，材料纹理真实，施工逻辑可落地，"
        "自然光影，构图完整，高清细节，不添加水印、标题、标注或无关文字。"
    )


def build_design_generation_prompt(
    user_request: str,
    *,
    has_space_image: bool,
    materials,
    editing_previous_effect: bool = False,
) -> str:
    """Build the invariant prompt shared by initial generation and later edits."""
    material_lines = []
    for index, material in enumerate(materials, 1):
        name = getattr(material, "name", None) or getattr(material, "original_name", None)
        usages = tuple(getattr(material, "usages", ()) or ())
        usage_text = "、".join(usages) if usages else "由系统根据方案合理安排"
        material_lines.append(
            f"{index}. {name or f'石材 {index}'}；预期用途：{usage_text}；"
            "颜色、纹理和质感以对应参考图为准。"
        )

    if editing_previous_effect:
        layout_rule = (
            "第 1 张输入图是上一轮已生成的效果图（当前效果图）。保持其构图、透视、建筑边界和未要求修改的元素，"
            "只执行本轮文字修改。不要使用庭院空间图重新布局。"
        )
    elif has_space_image:
        layout_rule = (
            "第 1 张输入图是庭院空间图。严格保留空间结构、透视关系和主要建筑边界，"
            "在该空间内完成庭院设计。"
        )
    else:
        layout_rule = "没有庭院空间图，请根据所选庭院风格自行设计完整、合理的庭院布局。"

    if material_lines:
        material_rule = (
            "后续输入图是石材参考图，顺序与下列清单一致。所有石材都必须在最终效果图中清晰出现；"
            "不得改变石材颜色、纹理或质感，不得替换，也不得增加方案之外的其他石材。"
            "可以使用植物、木材、金属和水景等非石材元素。\n"
            + "\n".join(material_lines)
        )
    else:
        material_rule = "没有石材参考图，可按庭院风格选择合理石材。"

    request = user_request.strip() or "生成庭院效果图"
    return (
        f"{layout_rule}\n{material_rule}\n"
        f"本轮要求：{request}\n"
        "只生成一次最终效果图。输出为 16:9、2K 的专业庭院建筑可视化，"
        "空间尺度合理、施工逻辑可落地、自然光影、高清真实，不添加水印、标题、标注或无关文字。"
    )
def _image_data_url_to_interaction_input(image: str) -> dict:
    try:
        header, data = image.split(",", 1)
        mime_type = header.split(";", 1)[0].split(":", 1)[1]
    except (ValueError, IndexError) as exc:
        raise ImageGenerationError("参考图片必须是有效的 Data URL") from exc

    if not mime_type.startswith("image/") or not data:
        raise ImageGenerationError("参考图片必须是有效的图片 Data URL")

    return {
        "type": "image",
        "mime_type": mime_type,
        "data": data,
    }


def _save_generated_image_data(mime_type: str, data: str) -> str:
    if not mime_type.startswith("image/"):
        raise ImageGenerationError("图片生成服务返回的内容不是图片")

    try:
        image_bytes = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise ImageGenerationError("图片生成服务返回了无效图片数据") from exc

    extension = mimetypes.guess_extension(mime_type) or ".png"
    if extension in {".jpe", ".jpeg"}:
        extension = ".jpg"

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    target = GENERATED_DIR / filename
    try:
        target.write_bytes(image_bytes)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise ImageGenerationError(f"生成图片保存失败：{exc}") from exc

    return f"/static/generated/{filename}"


def _call_gemini_image(
    prompt: str,
    image: str | None,
    additional_images: list[str] | tuple[str, ...] | None = None,
) -> str:
    api_key = os.getenv("GEMINI_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ImageGenerationError("未配置 GEMINI_KEY")

    inputs: list[dict] = []
    if image:
        inputs.append(_image_data_url_to_interaction_input(image))
    for additional_image in additional_images or []:
        inputs.append(_image_data_url_to_interaction_input(additional_image))
    inputs.append({"type": "text", "text": prompt})

    payload = {
        "model": GEMINI_IMAGE_MODEL,
        "input": inputs,
        "response_format": {
            "type": "image",
            "aspect_ratio": "16:9",
            "image_size": "2K",
        },
    }
    request = Request(
        GEMINI_IMAGE_ENDPOINT.format(model=GEMINI_IMAGE_MODEL),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ImageGenerationError(f"效果图生成失败（HTTP {exc.code}）：{detail[:300]}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ImageGenerationError(f"效果图生成服务请求失败：{exc}") from exc

    try:
        for step in reversed(result.get("steps") or []):
            if step.get("type") != "model_output":
                continue
            for content in reversed(step.get("content") or []):
                if content.get("type") != "image" or not content.get("data"):
                    continue
                mime_type = content.get("mime_type") or content.get("mimeType") or "image/jpeg"
                return _save_generated_image_data(mime_type, content["data"])
    except (AttributeError, KeyError, TypeError) as exc:
        message = result.get("error", {}).get("message") or "未返回图片数据"
        raise ImageGenerationError(f"效果图生成失败：{message}") from exc
    raise ImageGenerationError("效果图生成失败：未返回图片数据")


def generate_effect_image(
    message: str,
    image: str | None = None,
    *,
    additional_images: list[str] | tuple[str, ...] | None = None,
) -> str:
    prompt = _build_prompt(message, has_reference_image=bool(image))
    return _call_gemini_image(prompt, image, additional_images)
