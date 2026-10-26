"""合规水印服务：显式可见标识 + 隐式文件元数据，二者缺一不可。

依据《人工智能生成合成内容标识办法》的落地要求：
- 显式标识：图面可见的中文角标，随图传播，用户肉眼可识别；
- 隐式标识：写入 PNG 文本块与同名 .meta.json，机器可读，包含服务提供者编码与记录号。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from PIL import Image, ImageDraw, ImageFilter, ImageFont, PngImagePlugin

from app.config import settings

_FONT_CACHE: Dict[int, object] = {}

CJK_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def load_font(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    font = ImageFont.load_default()
    for path in CJK_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size=size)
                break
            except Exception:  # noqa: BLE001 - 字体损坏则继续尝试下一个
                continue
    _FONT_CACHE[size] = font
    return font


def _luma(rgb: tuple[int, int, int]) -> float:
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def add_visible_label(
    img: Image.Image,
    label: str | None = None,
    record_id: str = "",
) -> Image.Image:
    """贴图面可见标识：左上角胶囊 + 右下角记录号，防止截图裁切丢失标识。"""
    img = img.convert("RGB")
    label = label or settings.ai_visible_label
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size

    font = load_font(max(14, int(h * 0.028)))

    text = label
    tw = draw.textlength(text, font=font)
    pad_x, pad_y = 14, 8
    box_w, box_h = tw + pad_x * 2, font.size + pad_y * 2
    # 半透明深底 + 描边，保证任意底色上可读
    draw.rectangle([0, 0, box_w, box_h], fill=(15, 23, 42, 190))
    draw.text((pad_x, pad_y - 2), text, font=font, fill=(255, 255, 255, 255))

    small = load_font(max(11, int(h * 0.022)))
    foot = f"生成编号 {record_id}" if record_id else settings.ai_provider_code
    fw = draw.textlength(foot, font=small)
    fh = small.size + 12
    draw.rectangle([w - fw - 24, h - fh - 10, w - 8, h - 8], fill=(15, 23, 42, 170))
    draw.text((w - fw - 16, h - fh - 2), foot, font=small, fill=(255, 255, 255, 235))

    # 右下角的斜角丝带，进一步强化"非实物"视觉提示
    draw.polygon(
        [(w - 92, h - 44), (w - 8, h - 44), (w - 8, h - 8)],
        fill=(244, 63, 94, 70),
    )
    return img


def apply_blur_safe(img: Image.Image, radius: float = 1.0) -> Image.Image:
    """轻微模糊，模拟丝线柔光；失败时返回原图。"""
    try:
        return img.filter(ImageFilter.GaussianBlur(radius))
    except Exception:  # noqa: BLE001
        return img


def implicit_meta(
    *,
    record_id: str,
    provider: str,
    model: str,
    prompt: str,
    is_simulation: bool,
    stitch_codes: list[str],
    created_at: str | None = None,
) -> Dict[str, Any]:
    return {
        "AIGC": "true",
        "AI-Generated": "true",
        "AI-Label": settings.ai_visible_label,
        "AI-Provider-Code": settings.ai_provider_code,
        "AI-Provider-Name": settings.app_name,
        "AI-Record-ID": record_id,
        "AI-Model": model,
        "AI-Service-Endpoint": provider,
        "AI-Is-Simulation": "true" if is_simulation else "false",
        "AI-Prompt": prompt[:500],
        "AI-Stitch-Codes": ",".join(stitch_codes),
        "AI-Generated-Time": created_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "Software": settings.app_name,
    }


def save_with_metadata(
    img: Image.Image,
    dest: Path,
    meta: Dict[str, Any],
) -> Path:
    """保存 PNG 并写入隐式元数据块 + 同名 sidecar JSON（便于人工核对）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    png_info = PngImagePlugin.PngInfo()
    for k, v in meta.items():
        png_info.add_text(k, str(v))
    img.save(dest, format="PNG", pnginfo=png_info, optimize=True)

    sidecar = dest.with_suffix(dest.suffix + ".meta.json")
    sidecar.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dest


def read_implicit_meta(path: Path) -> Dict[str, Any]:
    """从 PNG 文本块读回隐式标识，用于合规自检。"""
    out: Dict[str, Any] = {}
    with Image.open(path) as im:
        for k, v in (im.info or {}).items():
            if k.startswith("AI-") or k in {"Software", "AIGC"}:
                out[k] = v
    return out


def assert_compliant(path: Path) -> None:
    """自检：显式标识在图上，隐式标识在元数据里，两者齐全才算合规。"""
    meta = read_implicit_meta(path)
    missing = [k for k in ("AI-Generated", "AI-Provider-Code", "AI-Record-ID") if k not in meta]
    if missing:
        raise RuntimeError(f"隐式标识缺失: {missing} @ {path}")
