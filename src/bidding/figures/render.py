"""Pillow renderers for deterministic, schematic bid figures."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1800
PAGE_MARGIN = 80
FONT_ENV = "BID_FIGURE_FONT"
DPI = 200

NAVY = "#0f2f4a"
BLUE = "#175d8d"
TEAL = "#0f766e"
LIGHT_BLUE = "#e8f2f8"
LIGHT_TEAL = "#e6f4f1"
LIGHT_GREY = "#f3f6f8"
MID_GREY = "#64748b"
LINE_GREY = "#94a3b8"
TEXT = "#172033"
WHITE = "#ffffff"
AMBER = "#b45309"
LIGHT_AMBER = "#fef3c7"

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)
_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
)


class FigureRenderError(RuntimeError):
    """A figure could not be rendered; assembly must degrade, not fail."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    path: Path
    width: int
    height: int


def _find_font(explicit: str | None, *, bold: bool = False) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path
        raise FigureRenderError(f"configured figure font does not exist: {path}")
    candidates = _BOLD_CANDIDATES if bold else _FONT_CANDIDATES
    for candidate in candidates:
        path = Path(candidate)
        if path.is_file():
            return path
    raise FigureRenderError(
        "no CJK font found; set BID_FIGURE_FONT to a .ttf/.ttc file"
    )


class _Fonts:
    def __init__(self, explicit: str | None) -> None:
        self.regular_path = _find_font(explicit)
        self.bold_path = _find_font(explicit, bold=True)
        self._cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def get(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, bold)
        if key not in self._cache:
            path = self.bold_path if bold else self.regular_path
            self._cache[key] = ImageFont.truetype(str(path), size)
        return self._cache[key]


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    return draw.textlength(text, font=font)


def _wrap(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: float,
    max_lines: int = 3,
) -> list[str]:
    value = " ".join((text or "").split())
    if not value:
        return [""]
    lines: list[str] = []
    current = ""
    for char in value:
        candidate = current + char
        if _text_width(draw, candidate, font) <= max_width or not current:
            current = candidate
            continue
        lines.append(current.rstrip())
        current = char.lstrip()
        if len(lines) >= max_lines:
            break
    if len(lines) < max_lines and current:
        lines.append(current.rstrip())
    if not lines:
        return [""]
    if len(lines) == max_lines and _text_width(draw, lines[-1], font) > max_width:
        lines[-1] = lines[-1][:-1] + "…"
    return lines[:max_lines]


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    fill: str,
    max_width: float,
    max_lines: int = 3,
    anchor: str = "mm",
    spacing: float = 1.25,
) -> None:
    lines = _wrap(draw, text, font, max_width, max_lines)
    line_height = font.size * spacing
    x, y = xy
    start = y - (len(lines) - 1) * line_height / 2
    for index, line in enumerate(lines):
        draw.text((x, start + index * line_height), line, font=font, fill=fill, anchor="mm")


def _draw_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    *,
    fill: str,
    outline: str,
    lines: Iterable[tuple[str, int, bool, str]],
    radius: int = 16,
    max_width: float | None = None,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2)
    x1, y1, x2, y2 = box
    entries = list(lines)
    heights = [size * 1.35 for _text, size, _bold, _color in entries]
    total = sum(heights)
    cursor = y1 + ((y2 - y1) - total) / 2
    width = max_width if max_width is not None else (x2 - x1 - 32)
    for (text, size, bold, color), height in zip(entries, heights):
        font = _font_for(size, bold=bold)
        _draw_wrapped(
            draw,
            ((x1 + x2) / 2, cursor + height / 2),
            text,
            font,
            fill=color,
            max_width=width,
            max_lines=2,
            spacing=1.1,
        )
        cursor += height


# The helper is attached by _Renderer to avoid threading a font object through
# every box call.
def _font_for(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    fonts = _ACTIVE_FONTS
    if fonts is None:
        raise FigureRenderError("figure renderer fonts were not initialized")
    return fonts.get(size, bold=bold)


_ACTIVE_FONTS: _Fonts | None = None


def _header(
    draw: ImageDraw.ImageDraw,
    title: str,
    *,
    schematic: bool,
) -> None:
    _draw_wrapped(
        draw,
        (WIDTH / 2, 58),
        title,
        _font_for(44, bold=True),
        fill=NAVY,
        max_width=WIDTH - 2 * PAGE_MARGIN,
        max_lines=2,
        spacing=1.1,
    )
    if schematic:
        draw.rounded_rectangle((PAGE_MARGIN, 28, PAGE_MARGIN + 128, 70), radius=10, fill=LIGHT_AMBER, outline=AMBER, width=2)
        draw.text(
            (PAGE_MARGIN + 64, 49),
            "示意图",
            font=_font_for(24, bold=True),
            fill=AMBER,
            anchor="mm",
        )
    draw.line((PAGE_MARGIN, 110, WIDTH - PAGE_MARGIN, 110), fill=LINE_GREY, width=2)


def _footer(draw: ImageDraw.ImageDraw, disclaimer: str, *, height: int) -> int:
    top = height - 150
    draw.line((PAGE_MARGIN, top - 18, WIDTH - PAGE_MARGIN, top - 18), fill="#dbe3ea", width=2)
    lines = _wrap(draw, f"注：{disclaimer}", _font_for(23), WIDTH - 2 * PAGE_MARGIN, 3)
    for index, line in enumerate(lines):
        draw.text(
            (PAGE_MARGIN, top + index * 32),
            line,
            font=_font_for(23),
            fill=MID_GREY,
            anchor="la",
        )
    return top - 40


def _draw_org_chart(draw: ImageDraw.ImageDraw, data: dict[str, Any], body_bottom: int) -> None:
    company = str(data.get("company") or "【待填写：公司名称】")
    manager = data.get("manager") if isinstance(data.get("manager"), dict) else {}
    members = [item for item in data.get("members") or [] if isinstance(item, dict)]

    root_box = (WIDTH / 2 - 290, 150, WIDTH / 2 + 290, 270)
    _draw_box(
        draw,
        root_box,
        fill=LIGHT_BLUE,
        outline=BLUE,
        lines=[(company, 34, True, NAVY)],
        max_width=520,
    )
    manager_box = (WIDTH / 2 - 230, 370, WIDTH / 2 + 230, 490)
    _draw_box(
        draw,
        manager_box,
        fill=WHITE,
        outline=TEAL,
        lines=[
            (str(manager.get("role") or "项目经理"), 24, False, TEAL),
            (str(manager.get("name") or "【待填写：项目经理姓名】"), 30, True, TEXT),
        ],
        max_width=420,
    )
    draw.line((WIDTH / 2, 270, WIDTH / 2, 370), fill=LINE_GREY, width=3)

    if not members:
        return
    per_row = 4
    box_w, box_h, gap_x, gap_y = 300, 120, 28, 44
    bus_y = 550
    draw.line((manager_box[0] + 200, 490, manager_box[2] - 200, 490), fill=LINE_GREY, width=3)
    draw.line((WIDTH / 2, 490, WIDTH / 2, bus_y), fill=LINE_GREY, width=3)

    rows = [members[index : index + per_row] for index in range(0, len(members), per_row)]
    for row_index, row in enumerate(rows):
        row_width = len(row) * box_w + (len(row) - 1) * gap_x
        x_start = (WIDTH - row_width) / 2
        y1 = bus_y + 60 + row_index * (box_h + gap_y)
        if row_index == 0:
            draw.line((WIDTH / 2, bus_y, WIDTH / 2, bus_y + 30), fill=LINE_GREY, width=3)
        for column, member in enumerate(row):
            x1 = x_start + column * (box_w + gap_x)
            center_x = x1 + box_w / 2
            draw.line((center_x, bus_y, center_x, y1), fill=LINE_GREY, width=2)
            _draw_box(
                draw,
                (x1, y1, x1 + box_w, y1 + box_h),
                fill=WHITE,
                outline=LINE_GREY,
                lines=[
                    (str(member.get("role") or "【待填写：岗位】"), 23, False, TEAL),
                    (str(member.get("name") or "【待填写：人员姓名】"), 26, True, TEXT),
                ],
                max_width=box_w - 28,
            )
        if y1 + box_h > body_bottom:
            break


def _draw_process_flow(draw: ImageDraw.ImageDraw, data: dict[str, Any], body_bottom: int) -> None:
    steps = [str(step) for step in data.get("steps") or [] if str(step).strip()][:6]
    if not steps:
        return
    top = 160
    gap = 42
    available = max(300, body_bottom - top)
    box_h = min(132, (available - gap * (len(steps) - 1)) / len(steps))
    box_w = 1000
    x1 = (WIDTH - box_w) / 2
    for index, step in enumerate(steps):
        y1 = top + index * (box_h + gap)
        fill = LIGHT_BLUE if index % 2 == 0 else WHITE
        _draw_box(
            draw,
            (x1, y1, x1 + box_w, y1 + box_h),
            fill=fill,
            outline=BLUE,
            lines=[(f"{index + 1}. {step}", 32, False, TEXT)],
            max_width=box_w - 80,
        )
        if index < len(steps) - 1:
            arrow_y = y1 + box_h
            draw.line((WIDTH / 2, arrow_y, WIDTH / 2, arrow_y + gap - 8), fill=TEAL, width=4)
            draw.polygon(
                [
                    (WIDTH / 2 - 14, arrow_y + gap - 20),
                    (WIDTH / 2 + 14, arrow_y + gap - 20),
                    (WIDTH / 2, arrow_y + gap - 2),
                ],
                fill=TEAL,
            )


def _draw_gantt(draw: ImageDraw.ImageDraw, data: dict[str, Any], body_bottom: int) -> None:
    phases = [item for item in data.get("phases") or [] if isinstance(item, dict)]
    total = max(1, int(data.get("total_days") or 1))
    chart_left, chart_right = 470, WIDTH - PAGE_MARGIN
    chart_width = chart_right - chart_left
    label_left = PAGE_MARGIN
    axis_y = 180
    draw.line((chart_left, axis_y, chart_right, axis_y), fill=NAVY, width=3)
    tick_font = _font_for(22)
    for fraction in (0, 0.25, 0.5, 0.75, 1.0):
        x = chart_left + chart_width * fraction
        draw.line((x, axis_y, x, body_bottom), fill="#e2e8f0", width=2)
        draw.line((x, axis_y - 8, x, axis_y + 8), fill=NAVY, width=3)
        day = round(total * fraction)
        draw.text((x, axis_y - 26), f"{day} 天", font=tick_font, fill=MID_GREY, anchor="mm")
    draw.text(
        (label_left, axis_y - 26),
        "阶段",
        font=_font_for(24, bold=True),
        fill=NAVY,
        anchor="la",
    )
    draw.text(
        (chart_right, axis_y - 54),
        f"总工期 {total} 天（示意）",
        font=_font_for(24, bold=True),
        fill=TEAL,
        anchor="ra",
    )
    row_height = max(130, (body_bottom - 300) / max(1, len(phases)))
    for index, phase in enumerate(phases):
        y = 300 + index * row_height
        name = str(phase.get("name") or "阶段")
        draw.text((label_left, y + 24), name, font=_font_for(28, bold=True), fill=TEXT, anchor="la")
        start = max(0, min(total, int(phase.get("start_day") or 0)))
        duration = max(1, min(total - start, int(phase.get("duration_days") or 1)))
        x1 = chart_left + chart_width * start / total
        x2 = chart_left + chart_width * (start + duration) / total
        draw.rounded_rectangle(
            (x1, y, x2, y + 50),
            radius=10,
            fill=TEAL if index % 2 == 0 else BLUE,
        )
        draw.text(
            (x1 + 14, y + 25),
            f"{start + 1}-{start + duration} 天",
            font=_font_for(21),
            fill=WHITE,
            anchor="lm",
        )
    draw.text(
        (label_left, 255),
        f"计划开始：{data.get('start_text') or '【待填写】'}    "
        f"计划完成：{data.get('end_text') or '【待填写】'}",
        font=_font_for(24),
        fill=MID_GREY,
        anchor="la",
    )


def _draw_site_plan(draw: ImageDraw.ImageDraw, data: dict[str, Any], body_bottom: int) -> None:
    zones = [item for item in data.get("zones") or [] if isinstance(item, dict)][:6]
    left, right = PAGE_MARGIN, WIDTH - PAGE_MARGIN
    top = 150
    bottom = min(body_bottom, top + 720)
    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill="#fbfdff", outline=NAVY, width=4)
    road_top = top + (bottom - top) * 0.48
    road_h = 86
    draw.rectangle((left + 30, road_top, right - 30, road_top + road_h), fill="#e5e7eb")
    draw.line(
        (left + 30, road_top + road_h / 2, right - 30, road_top + road_h / 2),
        fill=WHITE,
        width=4,
    )
    draw.text(
        (left + 50, road_top + road_h / 2),
        "临时施工道路",
        font=_font_for(22, bold=True),
        fill=MID_GREY,
        anchor="lm",
    )
    draw.polygon(
        [(right - 46, road_top - 20), (right - 18, road_top - 20), (right - 32, road_top - 2)],
        fill=AMBER,
    )
    draw.text((right - 32, road_top - 34), "主出入口", font=_font_for(22, bold=True), fill=AMBER, anchor="mm")

    columns = 3
    box_w = (right - left - 80 - 40 * (columns - 1)) / columns
    top_area = (road_top - 24) - (top + 36)
    bottom_area = (bottom - 28) - (road_top + road_h + 24)
    box_h = min(180, max(120, min(top_area, bottom_area) / 2))
    for index, zone in enumerate(zones):
        row = index // columns
        column = index % columns
        if row > 1:
            break
        x1 = left + 40 + column * (box_w + 40)
        y1 = (top + 36) if row == 0 else (road_top + road_h + 24)
        fill = LIGHT_TEAL if index % 2 == 0 else LIGHT_BLUE
        _draw_box(
            draw,
            (x1, y1, x1 + box_w, y1 + box_h),
            fill=fill,
            outline=TEAL if index % 2 == 0 else BLUE,
            lines=[(str(zone.get("name") or "区域"), 30, True, NAVY)],
            max_width=box_w - 36,
        )
    # North arrow.
    north_x, north_y = right - 90, top + 54
    draw.line((north_x, north_y + 50, north_x, north_y + 6), fill=NAVY, width=4)
    draw.polygon(
        [(north_x, north_y - 16), (north_x - 13, north_y + 18), (north_x + 13, north_y + 18)],
        fill=NAVY,
    )
    draw.text((north_x, north_y + 64), "N", font=_font_for(24, bold=True), fill=NAVY, anchor="mm")


_DRAWERS = {
    "org_chart": _draw_org_chart,
    "process_flow": _draw_process_flow,
    "gantt": _draw_gantt,
    "site_plan": _draw_site_plan,
}
_HEIGHTS = {
    "org_chart": 1240,
    "process_flow": 1420,
    "gantt": 1100,
    "site_plan": 1240,
}


def render_figure(
    *,
    kind: str,
    data: dict[str, Any],
    title: str,
    disclaimer: str,
    output_path: str | Path,
    width_px: int = WIDTH,
    font_path: str | None = None,
) -> RenderResult:
    """Render one figure to a PNG. Raises FigureRenderError on any failure."""
    global _ACTIVE_FONTS
    if kind not in _DRAWERS:
        raise FigureRenderError(f"unsupported figure kind: {kind}")
    if width_px < 800 or width_px > 4000:
        raise FigureRenderError("figure width must be between 800 and 4000 pixels")
    explicit = font_path or os.environ.get(FONT_ENV) or None
    _ACTIVE_FONTS = _Fonts(explicit)
    scale = width_px / WIDTH
    height = int(_HEIGHTS[kind] * scale)
    canvas = Image.new("RGB", (width_px, height), WHITE)
    draw = ImageDraw.Draw(canvas)
    try:
        if scale != 1:
            # All layout uses the 1800px design grid; render it and downscale
            # only when the caller explicitly requests another width.
            base = Image.new("RGB", (WIDTH, _HEIGHTS[kind]), WHITE)
            base_draw = ImageDraw.Draw(base)
            _header(
                base_draw,
                title,
                schematic=bool(data.get("schematic", kind in {"site_plan", "process_flow"})),
            )
            bottom = _footer(base_draw, disclaimer, height=_HEIGHTS[kind])
            bottom = min(bottom, _HEIGHTS[kind] - 240)
            _DRAWERS[kind](base_draw, data, bottom)
            canvas = base.resize((width_px, height), Image.Resampling.LANCZOS)
            draw = ImageDraw.Draw(canvas)
        else:
            _header(
                draw,
                title,
                schematic=bool(data.get("schematic", kind in {"site_plan", "process_flow"})),
            )
            bottom = _footer(draw, disclaimer, height=height)
            bottom = min(bottom, height - 240)
            _DRAWERS[kind](draw, data, bottom)
    except FigureRenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - convert renderer errors to a controlled type
        raise FigureRenderError(f"could not render {kind}: {exc}") from exc
    finally:
        _ACTIVE_FONTS = None
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".figure-", suffix=".png", dir=str(output.parent))
    os.close(fd)
    try:
        canvas.save(temp_name, format="PNG", dpi=(DPI, DPI), optimize=True)
        os.replace(temp_name, output)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return RenderResult(path=output, width=canvas.width, height=canvas.height)


__all__ = ["FigureRenderError", "RenderResult", "render_figure"]
