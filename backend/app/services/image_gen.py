"""图像生成服务：mock（离线占位）/ api（OpenAI 兼容文生图）双 Provider。

设计要点：
- 统一 Interface，上层不感知具体实现；切换只改环境变量 IMAGE_PROVIDER；
- mock 不是"假装成功了"，而是输出明确写着「AI 效果示意」的程序化渲染图，
  让链路在没有任何密钥时依然完整可演示（← 参考样例项目的 SDXL+LoRA 路线，
  真实模型接入点预留在此处，不改变调用方代码）。
"""

from __future__ import annotations

import io
import math
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter as _IF

from app.config import settings

# ---------------------------------------------------------------------------
# 颜色/母题词典：仅用于 mock 渲染的视觉提示id，不涉及任何专业结论
# ---------------------------------------------------------------------------
COLOR_WORDS = {
    "红": (178, 34, 34), "朱": (200, 48, 40), "粉": (233, 150, 178),
    "桃": (242, 168, 178), "梅": (214, 78, 96), "丹": (196, 52, 54),
    "橙": (234, 132, 46), "黄": (226, 178, 60), "金": (206, 166, 66),
    "绿": (76, 132, 92), "青": (66, 122, 130), "翠": (58, 138, 116),
    "蓝": (56, 96, 156), "靛": (46, 74, 132), "紫": (124, 88, 158),
    "棕": (140, 96, 62), "褐": (122, 86, 56), "墨": (40, 44, 52),
    "黑": (34, 34, 38), "白": (238, 236, 230), "雪": (244, 242, 238),
    "灰": (150, 150, 152), "赭": (156, 96, 60),
}
DEFAULT_PALETTE = [(206, 166, 66), (196, 52, 54), (66, 122, 130), (76, 132, 92)]

PETAL_WORDS = ("花", "牡丹", "荷", "莲", "梅", "兰", "菊", "叶", "瓣", "春", "芳")
FUR_WORDS = ("虎", "狮", "猫", "毛", "兽", "豹", "犬", "兔", "鬅", "禽", "鸟", "羽")
LANDSCAPE_WORDS = ("山", "水", "云", "江", "湖", "竹", "松", "雪", "月", "风", "景", "楼")
FIGURE_WORDS = ("人", "仕女", "人物", "像", "肖像", "娃", "童", "姑娘")


@dataclass
class GenResult:
    image: Image.Image
    provider: str
    model: str
    is_simulation: bool
    latency_ms: int
    cost: float


def _seed_of(text: str) -> int:
    return int.from_bytes(text.encode("utf-8")[:8].ljust(8, b"\0"), "big") % (2**31)


def pick_palette(prompt: str) -> list[tuple[int, int, int]]:
    found = [(w, c) for w, c in COLOR_WORDS.items() if w in prompt]
    if not found:
        return DEFAULT_PALETTE
    found.sort(key=lambda x: -prompt.index(x[0]) if False else 0)
    base = [c for _, c in found[:3]]
    while len(base) < 3:
        base.append(DEFAULT_PALETTE[len(base) % len(DEFAULT_PALETTE)])
    return base


def detect_motif(prompt: str) -> str:
    if any(w in prompt for w in FUR_WORDS):
        return "fur"
    if any(w in prompt for w in PETAL_WORDS):
        return "petal"
    if any(w in prompt for w in LANDSCAPE_WORDS):
        return "landscape"
    if any(w in prompt for w in FIGURE_WORDS):
        return "figure"
    return "abstract"


def _stroke_layer(
    size: int, palette: list[tuple[int, int, int]], rnd: random.Random, strokes: int
) -> Image.Image:
    """把画布切成小块，每块内用同一方向的短针脚铺满，模拟缎面绣的分块效果。

    与「随机乱画」的区别：同一块内方向一致（像一针挨一针的排线），
    相邻块之间方向缓慢变化，视觉上更像绣面而不是噪点。
    """
    img = Image.new("RGB", (size, size), palette[0])
    draw = ImageDraw.Draw(img, "RGBA")
    grid = 26
    cell = size / grid
    per_cell = max(6, strokes // (grid * grid))
    for gy in range(grid):
        for gx in range(grid):
            cx, cy = (gx + 0.5) * cell, (gy + 0.5) * cell
            # 颜色：沿对角方向在调色板间平滑插值
            t = min(1.0, max(0.0, (gx / grid * 0.65 + gy / grid * 0.35)))
            i = min(len(palette) - 2, int(t * (len(palette) - 1)))
            c1, c2 = palette[i], palette[i + 1]
            f = t * (len(palette) - 1) - i
            base = tuple(int(c1[k] + (c2[k] - c1[k]) * f) for k in range(3))
            # 方向场：整体沿 45°，叠加低频扰动，避免整幅像网格
            angle = 0.785 + 0.55 * math.sin(gx * 0.42 + gy * 0.31)
            dx, dy = math.cos(angle), math.sin(angle)
            px, py = -dy, dx  # 垂直方向，用于逐针平移
            for k in range(per_cell):
                off = (k - per_cell / 2) * (cell / per_cell) + rnd.uniform(-0.8, 0.8)
                sx = cx + px * off + rnd.uniform(-1.6, 1.6)
                sy = cy + py * off + rnd.uniform(-1.6, 1.6)
                ln = cell * rnd.uniform(0.75, 1.15)
                jitter = rnd.randint(-20, 20)
                color = tuple(max(0, min(255, v + jitter)) for v in base)
                draw.line(
                    [(sx, sy), (sx + dx * ln, sy + dy * ln)],
                    fill=color + (235,),
                    width=max(2, int(cell / 7)),
                )
    return img


def _stitch_ellipse(draw: ImageDraw.ImageDraw, box, color, width: int, rnd: random.Random,
                    segments: int = 96, outward: float = 1.0) -> None:
    """用一针一针的短线沿椭圆轮廓描边，比实线更像绣出来的边缘。"""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = (x1 - x0) / 2, (y1 - y0) / 2
    for i in range(segments):
        a = 2 * math.pi * i / segments
        nx, ny = math.cos(a), math.sin(a)
        px, py = cx + rx * nx, cy + ry * ny
        ln = (rx + ry) / 2 * 0.06 * rnd.uniform(0.8, 1.4) * outward
        # 切线方向略微外扩，形成羽毛状针脚
        tx, ty = -ny * rx, nx * ry
        norm = math.hypot(tx, ty) or 1
        draw.line(
            [(px - tx / norm * ln, py - ty / norm * ln),
             (px + tx / norm * ln, py + ty / norm * ln)],
            fill=color, width=width,
        )


def _stitch_polyline(draw: ImageDraw.ImageDraw, points, color, width: int,
                     dash: float = 9.0, gap: float = 5.0) -> None:
    """把折线画成断续的针脚，而不是平滑实线。"""
    carry = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        seg = math.hypot(bx - ax, by - ay)
        if seg <= 0:
            continue
        t = 0.0
        while t < seg:
            start = t
            end = min(seg, t + max(1.0, dash - carry))
            draw.line(
                [(ax + (bx - ax) * start / seg, ay + (by - ay) * start / seg),
                 (ax + (bx - ax) * end / seg, ay + (by - ay) * end / seg)],
                fill=color, width=width,
            )
            carry = 0.0
            t = end + gap
        carry = 0.0


def _motif_layer(size: int, motif: str, palette: list[tuple[int, int, int]], rnd: random.Random) -> Image.Image:
    """在绣面上叠一层母题剪影，让 mock 图可读而不至于完全抽象。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = size / 2, size / 2
    accent = palette[1] if len(palette) > 1 else DEFAULT_PALETTE[1]
    # 轮廓用更深的丝线色，保证在浅底上也能看清
    ink = tuple(max(0, int(c * 0.52)) for c in accent)
    hi = (255, 252, 245)

    if motif == "petal":
        n = rnd.randint(5, 7)
        r = size * rnd.uniform(0.26, 0.33)
        for i in range(n):
            a = 2 * math.pi * i / n + rnd.uniform(-0.12, 0.12)
            rx, ry = rnd.uniform(0.42, 0.58) * r, rnd.uniform(0.95, 1.2) * r
            bx = cx + math.cos(a) * r * 0.35
            by = cy + math.sin(a) * r * 0.35
            _stitch_ellipse(draw, [bx - rx, by - ry, bx + rx, by + ry],
                            ink + (215,), max(3, size // 170), rnd, segments=120)
            # 花瓣内部：顺着长轴的掺针排线，越靠外越淡
            for k in range(46):
                t = rnd.uniform(-1, 1)
                px = bx + t * rx * 0.82
                py = by + rnd.uniform(-ry, ry) * 0.78
                ln = ry * 0.10
                draw.line([(px, py), (px + math.cos(a) * ln, py + math.sin(a) * ln * 0.5)],
                          fill=hi + (rnd.randint(60, 120),), width=max(2, size // 260))
    elif motif == "fur":
        # 一组有弧度的毛发主走向，外加零星逆毛
        for i in range(7):
            base = size * (0.12 + i * 0.12)
            pts = [
                (x, base + math.sin(x / size * math.pi * rnd.uniform(0.7, 1.3)) * size * 0.045 * (i % 2 * 2 - 1))
                for x in range(-10, size + 20, 16)
            ]
            _stitch_polyline(draw, pts, ink + (150,), max(2, size // 200), dash=12, gap=6)
        for _ in range(220):
            x, y = rnd.uniform(0, size), rnd.uniform(0, size)
            ln = rnd.uniform(4, 14)
            ang = -0.5 + rnd.uniform(-0.5, 0.5)
            draw.line([(x, y), (x + math.cos(ang) * ln, y + math.sin(ang) * ln)],
                      fill=hi + (rnd.randint(45, 95),), width=max(1, size // 320))
    elif motif == "landscape":
        for i in range(4):
            base = cy + i * size * 0.13
            amp = size * 0.035 * (0.4 + i * 0.25)
            pts = [(x, base + math.sin(x / size * math.pi * (1.1 + i * 0.5)) * amp)
                   for x in range(-10, size + 20, 14)]
            _stitch_polyline(draw, pts, ink + (150 - i * 26),
                             max(2, size // 220), dash=10, gap=5)
    elif motif == "figure":
        _stitch_ellipse(draw, [cx - size * 0.15, cy - size * 0.30, cx + size * 0.15, cy + size * 0.01],
                        ink + (215,), max(3, size // 170), rnd, segments=90)
        # 肩线：用两条对开短针堆出轮廓
        for side in (-1, 1):
            pts = [(cx + side * size * 0.06 * k, cy + size * 0.03 + size * 0.055 * k ** 1.35)
                   for k in range(6)]
            _stitch_polyline(draw, pts, ink + (180,), max(2, size // 200), dash=9, gap=5)
    else:
        for i in range(3):
            r = size * (0.18 + i * 0.12)
            _stitch_ellipse(draw, [cx - r, cy - r, cx + r, cy + r],
                            ink + (120,), max(2, size // 240), rnd, segments=90)
    return layer


def render_mock(prompt: str) -> Image.Image:
    size = settings.image_size
    rnd = random.Random(_seed_of(prompt or "针语"))
    palette = pick_palette(prompt)
    base = _stroke_layer(size, palette, rnd, settings.mock_render_strokes)
    base = Image.alpha_composite(
        base.convert("RGBA"), _motif_layer(size, detect_motif(prompt), palette, rnd)
    ).convert("RGB")

    # 丝绸底光：中心亮、四周压暗
    vignette = Image.new("L", (size, size), 0)
    ImageDraw.Draw(vignette).ellipse(
        [-size * 0.2, -size * 0.2, size * 1.2, size * 1.2], fill=255
    )
    vignette = vignette.filter(_IF.GaussianBlur(size * 0.14))
    glow = Image.new("RGB", (size, size), (255, 250, 240))
    base = Image.composite(glow, base, vignette.point(lambda v: int(v * 0.45)))
    return base


async def _call_api(prompt: str, negative: str) -> Image.Image:
    """调用 OpenAI 兼容文生图接口（未配置密钥时不启用）。"""
    import httpx  # 局部导入：mock 模式下无需网络栈

    if not (settings.image_api_url and settings.image_api_key):
        raise RuntimeError("未配置图像生成接口")
    payload = {
        "model": settings.image_api_model,
        "prompt": prompt,
        "negative_prompt": negative or "photo, realistic texture, 3d, oil painting",
        "n": 1,
        "response_format": "b64_json",
        "size": f"{settings.image_size}x{settings.image_size}",
    }
    headers = {"Authorization": f"Bearer {settings.image_api_key}"}
    async with httpx.AsyncClient(timeout=settings.image_timeout_sec) as cli:
        r = await cli.post(settings.image_api_url.rstrip("/") + "/v1/images/generations",
                           json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()["data"][0]
        if data.get("b64_json"):
            import base64

            raw = base64.b64decode(data["b64_json"])
        else:
            rr = await cli.get(data["url"])
            raw = rr.content
    return Image.open(io.BytesIO(raw)).convert("RGB")


async def generate_image(prompt: str, negative: str = "") -> GenResult:
    """统一入口。mock 为默认，保证零密钥可跑；api 失败时降级回 mock 并记录。"""
    started = time.perf_counter()
    provider = settings.image_provider.lower()
    if provider == "api":
        try:
            img = await _call_api(prompt, negative)
            return GenResult(
                img, "api", settings.image_api_model, False,
                int((time.perf_counter() - started) * 1000), settings.image_cost_per_call,
            )
        except Exception:  # noqa: BLE001 - 上游异常降级，不中断用户链路
            fallback = render_mock(prompt)
            return GenResult(
                fallback, "api-fallback", "mock-render", True,
                int((time.perf_counter() - started) * 1000), 0.0,
            )
    img = render_mock(prompt)
    return GenResult(
        img, "mock", "mock-render", True,
        int((time.perf_counter() - started) * 1000), 0.0,
    )


def daily_cost(session) -> float:
    from datetime import timedelta

    from sqlalchemy import func

    from app.db.models import Generation

    since = datetime.now(timezone.utc) - timedelta(days=1)
    row = session.query(func.coalesce(func.sum(Generation.cost_estimate), 0.0)).filter(
        Generation.created_at >= since
    ).scalar()
    return float(row or 0.0)
