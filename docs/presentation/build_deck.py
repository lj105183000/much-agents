"""Build the Much Agents product & architecture deck.

Run from the repository root:

    uv run --with python-pptx --with pillow python docs/presentation/build_deck.py

The deck is intentionally generated from code so layout and copy can be reviewed
and regenerated. Speaker notes carry the supporting detail that does not fit on
the slides.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from lxml import etree
from PIL import Image, ImageDraw, ImageFilter
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------

SW = 13.333
SH = 7.5
M = 0.72

INK = "09121F"
INK_2 = "101D2F"
INK_3 = "18283D"
PAPER = "F6F5F0"
CARD = "FFFFFF"
TEXT = "0E1929"
TEXT_2 = "526072"
TEXT_3 = "8A94A4"
LINE_L = "DDE2E8"
LINE_D = "2A384B"

TEAL = "0F766E"
TEAL_DARK = "5EEAD4"
TEAL_BRIGHT = "2DD4BF"
AMBER = "B45309"
AMBER_DARK = "F6C85F"
VIOLET = "6D28D9"
VIOLET_DARK = "C4B5FD"
BLUE = "2563EB"
BLUE_DARK = "93C5FD"
RED = "B91C1C"
RED_DARK = "FCA5A5"
GREEN = "047857"
GREEN_DARK = "6EE7B7"

FONT = "Microsoft YaHei"
FONT_MONO = "Consolas"

OUT = Path(__file__).with_name("much-agents-product-architecture.pptx")


def rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value)


# ---------------------------------------------------------------------------
# Low-level shape and text helpers
# ---------------------------------------------------------------------------


def _set_run_font(
    run,
    *,
    size: float,
    color: str,
    bold: bool = False,
    font: str = FONT,
    italic: bool = False,
    spacing: int | None = None,
) -> None:
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = rgb(color)
    rpr = run._r.get_or_add_rPr()
    if spacing is not None:
        rpr.set("spc", str(spacing))
    rpr.set("lang", "zh-CN")
    latin = rpr.find(qn("a:latin"))
    insert_at = list(rpr).index(latin) + 1 if latin is not None else len(rpr)
    for tag in ("a:ea", "a:cs"):
        element = rpr.find(qn(tag))
        if element is None:
            element = etree.Element(qn(tag))
            rpr.insert(insert_at, element)
            insert_at += 1
        element.set("typeface", font)


def add_text(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    size: float = 12,
    color: str = TEXT,
    bold: bool = False,
    font: str = FONT,
    italic: bool = False,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
    line_spacing: float = 1.28,
    space_after: float = 0,
    margins: tuple[float, float, float, float] = (0, 0, 0, 0),
    spacing: int | None = None,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = Inches(margins[0])
    tf.margin_right = Inches(margins[1])
    tf.margin_top = Inches(margins[2])
    tf.margin_bottom = Inches(margins[3])
    paragraphs = text.split("\n")
    for index, line in enumerate(paragraphs):
        p = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        p.space_before = Pt(0)
        p.space_after = Pt(space_after)
        run = p.add_run()
        run.text = line
        _set_run_font(
            run,
            size=size,
            color=color,
            bold=bold,
            font=font,
            italic=italic,
            spacing=spacing,
        )
    return box


def add_rich_text(
    slide,
    runs: list[tuple[str, dict]],
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    align=PP_ALIGN.LEFT,
    anchor=MSO_ANCHOR.TOP,
    line_spacing: float = 1.28,
    margins: tuple[float, float, float, float] = (0, 0, 0, 0),
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = Inches(margins[0])
    tf.margin_right = Inches(margins[1])
    tf.margin_top = Inches(margins[2])
    tf.margin_bottom = Inches(margins[3])
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = line_spacing
    for text, style in runs:
        run = p.add_run()
        run.text = text
        _set_run_font(
            run,
            size=style.get("size", 12),
            color=style.get("color", TEXT),
            bold=style.get("bold", False),
            font=style.get("font", FONT),
            italic=style.get("italic", False),
            spacing=style.get("spacing"),
        )
    return box


def add_bullets(
    slide,
    items: list[str],
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    size: float = 11.5,
    color: str = TEXT_2,
    bullet_color: str = TEAL,
    line_spacing: float = 1.35,
    gap: float = 3,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    for index, item in enumerate(items):
        p = tf.paragraphs[0] if index == 0 else tf.add_paragraph()
        p.line_spacing = line_spacing
        p.space_before = Pt(0)
        p.space_after = Pt(gap)
        bullet = p.add_run()
        bullet.text = "•  "
        _set_run_font(bullet, size=size, color=bullet_color, bold=True)
        run = p.add_run()
        run.text = item
        _set_run_font(run, size=size, color=color)
    return box


def _set_fill_alpha(shape, opacity: float) -> None:
    if opacity >= 100:
        return
    solid = shape.fill._xPr.find(qn("a:solidFill"))
    if solid is None:
        return
    srgb = solid.find(qn("a:srgbClr"))
    if srgb is None:
        return
    alpha = etree.SubElement(srgb, qn("a:alpha"))
    alpha.set("val", str(int(opacity * 1000)))


def _set_line_alpha(shape, opacity: float) -> None:
    if opacity >= 100:
        return
    ln = shape.line._get_or_add_ln()
    solid = ln.find(qn("a:solidFill"))
    if solid is None:
        return
    srgb = solid.find(qn("a:srgbClr"))
    if srgb is None:
        return
    alpha = etree.SubElement(srgb, qn("a:alpha"))
    alpha.set("val", str(int(opacity * 1000)))


def add_rect(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str | None = CARD,
    line: str | None = None,
    line_width: float = 1,
    radius: float = 0.06,
    shape_type=MSO_SHAPE.ROUNDED_RECTANGLE,
    opacity: float = 100,
    line_opacity: float = 100,
):
    shape = slide.shapes.add_shape(
        shape_type, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.shadow.inherit = False
    style = shape._element.find(qn("p:style"))
    if style is not None:
        effect_ref = style.find(qn("a:effectRef"))
        if effect_ref is not None:
            effect_ref.set("idx", "0")
    if shape_type == MSO_SHAPE.ROUNDED_RECTANGLE:
        try:
            shape.adjustments[0] = radius
        except (IndexError, ValueError):
            pass
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = rgb(fill)
        _set_fill_alpha(shape, opacity)
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = rgb(line)
        shape.line.width = Pt(line_width)
        _set_line_alpha(shape, line_opacity)
    shape.text_frame.text = ""
    return shape


def add_circle(
    slide,
    x: float,
    y: float,
    d: float,
    *,
    fill: str | None = None,
    line: str | None = None,
    line_width: float = 1,
    opacity: float = 100,
    line_opacity: float = 100,
):
    return add_rect(
        slide,
        x,
        y,
        d,
        d,
        fill=fill,
        line=line,
        line_width=line_width,
        shape_type=MSO_SHAPE.OVAL,
        opacity=opacity,
        line_opacity=line_opacity,
    )


def add_line(
    slide,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str = LINE_L,
    width: float = 1,
    dash: bool = False,
    arrow: bool = False,
    opacity: float = 100,
):
    connector = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT,
        Inches(x1),
        Inches(y1),
        Inches(x2),
        Inches(y2),
    )
    connector.line.color.rgb = rgb(color)
    connector.line.width = Pt(width)
    if dash:
        connector.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    _set_line_alpha(connector, opacity)
    if arrow:
        ln = connector.line._get_or_add_ln()
        tail = etree.SubElement(ln, qn("a:tailEnd"))
        tail.set("type", "triangle")
        tail.set("w", "sm")
        tail.set("len", "sm")
    return connector


def add_pill(
    slide,
    text: str,
    x: float,
    y: float,
    w: float,
    h: float = 0.25,
    *,
    fill: str = TEAL,
    color: str = "FFFFFF",
    size: float = 8.5,
    line: str | None = None,
    bold: bool = True,
    radius: float = 0.5,
    opacity: float = 100,
):
    add_rect(
        slide,
        x,
        y,
        w,
        h,
        fill=fill,
        line=line,
        radius=radius,
        opacity=opacity,
    )
    add_text(
        slide,
        text,
        x,
        y,
        w,
        h,
        size=size,
        color=color,
        bold=bold,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
        spacing=40,
    )


def add_dot(slide, x: float, y: float, d: float, color: str, opacity: float = 100):
    return add_circle(slide, x, y, d, fill=color, opacity=opacity)


def _set_picture_alt(picture, text: str) -> None:
    picture._element.nvPicPr.cNvPr.set("descr", text)


def add_notes(slide, text: str) -> None:
    slide.notes_slide.notes_text_frame.text = text


# ---------------------------------------------------------------------------
# Backgrounds and chrome
# ---------------------------------------------------------------------------


def make_dark_background(path: Path) -> None:
    width, height = 2400, 1350
    top = (5, 10, 19)
    bottom = (11, 26, 41)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / (height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse(
        (width * 0.47, -height * 0.28, width * 1.05, height * 0.82),
        fill=(45, 212, 191, 55),
    )
    gd.ellipse(
        (width * 0.75, -height * 0.18, width * 1.14, height * 0.38),
        fill=(246, 200, 95, 26),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(220))
    image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for x in range(0, width, 80):
        od.line([(x, 0), (x, height)], fill=(255, 255, 255, 7), width=1)
    for y in range(0, height, 80):
        od.line([(0, y), (width, y)], fill=(255, 255, 255, 7), width=1)
    cx, cy = width * 0.82, height * 0.52
    for radius in (250, 390, 530, 670):
        od.arc(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            start=205,
            end=345,
            fill=(94, 234, 212, 24),
            width=2,
        )
    for angle in range(0, 360, 30):
        rad = math.radians(angle)
        px = cx + math.cos(rad) * 330
        py = cy + math.sin(rad) * 330
        od.ellipse((px - 3, py - 3, px + 3, py + 3), fill=(94, 234, 212, 70))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    noise = Image.effect_noise((width, height), 8).convert("L")
    noise_layer = Image.merge(
        "RGBA",
        (
            noise,
            noise,
            noise,
            Image.new("L", (width, height), 8),
        ),
    )
    image = Image.alpha_composite(image.convert("RGBA"), noise_layer).convert("RGB")
    image.save(path, quality=92)


def add_dark_background(slide, path: Path, overlay_opacity: float = 16) -> None:
    picture = slide.shapes.add_picture(
        str(path), 0, 0, width=Inches(SW), height=Inches(SH)
    )
    _set_picture_alt(picture, "深色渐变背景，含细网格与抽象弧线")
    if overlay_opacity:
        add_rect(
            slide,
            0,
            0,
            SW,
            SH,
            fill=INK,
            line=None,
            opacity=overlay_opacity,
        )


def add_footer(slide, page: int, *, dark: bool = False) -> None:
    color = "5D6B7E" if dark else "9AA3B1"
    line_color = "243246" if dark else LINE_L
    add_line(slide, M, 7.12, SW - M, 7.12, color=line_color, width=0.7)
    add_text(
        slide,
        "Much Agents  ·  产品设计与技术架构",
        M,
        7.18,
        6.4,
        0.2,
        size=8,
        color=color,
        line_spacing=1,
    )
    add_text(
        slide,
        f"{page:02d}",
        SW - M - 0.7,
        7.15,
        0.7,
        0.25,
        size=8.5,
        color=color,
        bold=True,
        align=PP_ALIGN.RIGHT,
        line_spacing=1,
    )


def add_kicker(
    slide,
    text: str,
    y: float = 0.36,
    *,
    dark: bool = False,
    color: str = TEAL,
    x: float = M,
) -> None:
    if dark:
        color = TEAL_DARK
    add_rect(slide, x, y + 0.045, 0.12, 0.12, fill=color, line=None, radius=0.0)
    add_text(
        slide,
        text.upper(),
        x + 0.23,
        y,
        8.6,
        0.22,
        size=8,
        color=color,
        bold=True,
        line_spacing=1,
        spacing=90,
    )


def content_slide(
    prs: Presentation,
    kicker: str,
    title: str,
    *,
    subtitle: str | None = None,
    page: int,
    dark: bool = False,
    bg: Path | None = None,
    title_size: float = 27,
):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    if dark:
        add_dark_background(slide, bg)  # type: ignore[arg-type]
    else:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = rgb(PAPER)
    add_kicker(slide, kicker, dark=dark)
    add_text(
        slide,
        title,
        M,
        0.62,
        SW - 2 * M,
        0.72,
        size=title_size,
        color="F5F3EE" if dark else TEXT,
        bold=True,
        line_spacing=1.12,
    )
    if subtitle:
        add_text(
            slide,
            subtitle,
            M,
            1.18,
            SW - 2 * M,
            0.42,
            size=11,
            color="A9B5C6" if dark else TEXT_2,
            line_spacing=1.35,
        )
    add_footer(slide, page, dark=dark)
    return slide


def add_section_label(
    slide,
    text: str,
    x: float,
    y: float,
    *,
    color: str = TEXT_3,
    width: float | None = None,
) -> None:
    add_text(
        slide,
        text.upper(),
        x,
        y,
        width or 3.2,
        0.2,
        size=7.5,
        color=color,
        bold=True,
        spacing=80,
        line_spacing=1,
    )


# ---------------------------------------------------------------------------
# Slide 01 - Cover
# ---------------------------------------------------------------------------


def slide_cover(prs: Presentation, bg: Path) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_dark_background(slide, bg, overlay_opacity=0)

    # Left editorial block.
    add_kicker(slide, "PRODUCT DESIGN  ×  TECHNICAL ARCHITECTURE", y=0.55, dark=True)
    add_text(
        slide,
        "把标书生产\n做成一条可信流水线",
        M,
        1.18,
        6.5,
        2.4,
        size=40,
        color="F7F5EF",
        bold=True,
        line_spacing=1.12,
    )
    add_text(
        slide,
        "Much Agents 多 Agent 标书编写系统",
        M,
        3.78,
        6.4,
        0.45,
        size=16,
        color=TEAL_DARK,
        bold=True,
        line_spacing=1.2,
    )
    add_text(
        slide,
        "从招标文件解析，到证据检索、受控生成、合规门禁与 DOCX 交付。\n"
        "一份关于产品设计、工程架构与边界的完整说明。",
        M,
        4.35,
        6.15,
        0.95,
        size=11.5,
        color="AEBACA",
        line_spacing=1.6,
    )

    add_pill(
        slide,
        "开源 · AGPL-3.0-or-later",
        M,
        5.75,
        2.12,
        0.28,
        fill="123044",
        color=TEAL_DARK,
        size=8.5,
        line="275069",
    )
    add_pill(
        slide,
        "本地优先 · 可私有化部署",
        M + 2.26,
        5.75,
        2.42,
        0.28,
        fill="123044",
        color="AEBACA",
        size=8.5,
        line="275069",
    )
    add_text(
        slide,
        "2026.10  /  lj105183000",
        M,
        6.55,
        5.0,
        0.3,
        size=8.5,
        color="66778C",
        spacing=50,
        line_spacing=1,
    )

    # Right-side end-to-end pipeline.
    panel_x, panel_y, panel_w = 8.05, 0.95, 4.55
    add_section_label(
        slide,
        "END-TO-END PIPELINE",
        panel_x + 0.1,
        panel_y,
        color="7B8CA2",
        width=3.2,
    )
    steps = [
        ("01", "招标文件 PDF", "版式解析 · 目录对齐 · 结构化抽取"),
        ("02", "可检索知识", "章节 / 页码 / 评标因素 / 证据片段"),
        ("03", "多 Agent 编写", "Writer → Reviewer → Reviser"),
        ("04", "质量门禁", "21 项合规 · 一致性台账 · 配图"),
        ("05", "可交付标书", "DOCX · 产物 · 审计记录"),
    ]
    start_y = 1.4
    card_h = 0.78
    gap = 0.22
    line_x = panel_x + 0.36
    add_line(
        slide,
        line_x,
        start_y + 0.24,
        line_x,
        start_y + len(steps) * (card_h + gap) - gap - 0.08,
        color=TEAL_DARK,
        width=1.3,
        opacity=38,
    )
    for index, (number, title, detail) in enumerate(steps):
        y = start_y + index * (card_h + gap)
        add_circle(
            slide,
            line_x - 0.07,
            y + 0.17,
            0.14,
            fill=TEAL_DARK if index in (2, 3) else "15293D",
            line=TEAL_DARK,
            line_width=0.8,
            line_opacity=60,
        )
        add_rect(
            slide,
            panel_x + 0.62,
            y,
            panel_w - 0.62,
            card_h,
            fill="0F1D2E",
            line="2A4055",
            radius=0.12,
            opacity=78,
            line_opacity=55,
        )
        add_text(
            slide,
            number,
            panel_x + 0.78,
            y + 0.15,
            0.38,
            0.2,
            size=8,
            color=TEAL_DARK,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            title,
            panel_x + 1.16,
            y + 0.11,
            2.8,
            0.25,
            size=12.5,
            color="F2F0EA",
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            detail,
            panel_x + 1.16,
            y + 0.39,
            2.8,
            0.24,
            size=8.2,
            color="8FA0B5",
            line_spacing=1.1,
        )

    add_text(
        slide,
        "AI 生成  ·  确定性校验  ·  人工决策",
        panel_x + 0.62,
        6.52,
        3.7,
        0.3,
        size=8.5,
        color="7B8CA2",
        bold=True,
        spacing=40,
        line_spacing=1,
    )
    add_notes(
        slide,
        "开场：Much Agents 不是通用写作工具，而是一套把招标文件转成可交付标书的生产系统。"
        "右侧五个阶段对应真实链路：解析与抽取、证据检索、多 Agent 编写、质量控制、DOCX 交付。"
        "今天分两部分讲：产品如何设计，以及技术架构如何支撑这些承诺。",
    )


# ---------------------------------------------------------------------------
# Slide 02 - Executive summary
# ---------------------------------------------------------------------------


def slide_summary(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "EXECUTIVE SUMMARY  /  01",
        "一条把 AI 产能变成可交付结果的流水线",
        subtitle="不是“一键写文章”，而是可观察、可干预、可审计的标书生产系统。",
        page=2,
    )

    add_section_label(slide, "WHAT IT IS", M, 1.62)
    add_text(
        slide,
        "AI 负责规模，\n确定性规则负责底线，\n人负责最终决策。",
        M,
        1.92,
        5.65,
        1.8,
        size=24,
        color=TEXT,
        bold=True,
        line_spacing=1.35,
    )
    add_text(
        slide,
        "Much Agents 把解析、检索、生成、检查与交付串成闭环。"
        "每一步都有明确状态、落盘产物和人工接管点，失败可以续跑，修改可以回滚，导出必须过门禁。",
        M,
        3.96,
        5.65,
        1.15,
        size=11.3,
        color=TEXT_2,
        line_spacing=1.65,
    )
    add_text(
        slide,
        "“",
        M - 0.05,
        1.73,
        0.6,
        0.6,
        size=40,
        color=TEAL,
        bold=True,
        line_spacing=1,
    )

    # Stat tiles.
    tiles = [
        ("21", "项合规检查", "主体、工期、资格、人员、业绩、保证金与评分覆盖"),
        ("21", "类一致性事实", "canonical 值 + 每处 occurrence + 冲突状态"),
        ("11", "个叙事章节", "并行生成；表单与附件保持确定性模板"),
        ("5", "类业务角色", "全局角色 ∩ 项目角色；另有不可登录的 service"),
    ]
    tile_x = 6.65
    tile_y = 1.62
    tile_w = 2.85
    tile_h = 1.18
    gap_x = 0.22
    gap_y = 0.18
    for index, (value, label, detail) in enumerate(tiles):
        col = index % 2
        row = index // 2
        x = tile_x + col * (tile_w + gap_x)
        y = tile_y + row * (tile_h + gap_y)
        add_rect(slide, x, y, tile_w, tile_h, fill=CARD, line=LINE_L, radius=0.08)
        add_text(
            slide,
            value,
            x + 0.2,
            y + 0.14,
            0.7,
            0.5,
            size=28,
            color=TEAL,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            label,
            x + 0.88,
            y + 0.2,
            1.75,
            0.28,
            size=11,
            color=TEXT,
            bold=True,
            line_spacing=1.1,
        )
        add_text(
            slide,
            detail,
            x + 0.2,
            y + 0.66,
            tile_w - 0.4,
            0.42,
            size=8.2,
            color=TEXT_3,
            line_spacing=1.35,
        )

    # Bottom principles.
    principles = [
        ("证据优先", "事实来自招标文件与企业资料；缺失一律 【待填写】。", TEAL),
        ("人在环上", "大纲、章节、修复方案与导出都可人工接管。", AMBER),
        ("企业边界", "默认拒绝、项目隔离、审计哈希链与私有 profile。", VIOLET),
    ]
    start_x = M
    card_w = 3.88
    for index, (title, detail, color) in enumerate(principles):
        x = start_x + index * (card_w + 0.17)
        add_rect(slide, x, 5.32, card_w, 1.28, fill=CARD, line=LINE_L, radius=0.09)
        add_rect(slide, x, 5.32, 0.055, 1.28, fill=color, line=None, radius=0.02)
        add_text(
            slide,
            title,
            x + 0.2,
            5.48,
            1.0,
            0.25,
            size=10.5,
            color=TEXT,
            bold=True,
        )
        add_text(
            slide,
            detail,
            x + 0.2,
            5.79,
            card_w - 0.38,
            0.6,
            size=9.2,
            color=TEXT_2,
            line_spacing=1.45,
        )
    add_notes(
        slide,
        "四个数字是产品的骨架：21 项合规检查、21 类一致性事实、11 个需要 LLM 的叙事章节、5 类业务角色。"
        "服务账号不可登录，因此单独说明。三条原则贯穿全篇：证据优先、人在环上、企业边界。",
    )


# ---------------------------------------------------------------------------
# Slide 03 - Problem and positioning
# ---------------------------------------------------------------------------


def slide_problem(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "PRODUCT CONTEXT  /  02",
        "标书生产的难点，从来不是“写一段话”",
        subtitle="真正稀缺的是：在有限时间内，把正确的事实放到正确的位置，并证明它正确。",
        page=3,
    )

    problems = [
        (
            "01",
            "信息密度高",
            "数百页 PDF、分散的评分点、资格条件与附件要求，人工检索容易遗漏。",
            TEAL,
        ),
        (
            "02",
            "协同链路长",
            "检索、编写、复核、排版、盖章，任何环节返工都会拖垮交付节奏。",
            AMBER,
        ),
        (
            "03",
            "质量不可见",
            "漏项、事实冲突和占位符，往往在最后一刻才被发现。",
            VIOLET,
        ),
    ]
    for index, (number, title, detail, color) in enumerate(problems):
        y = 1.66 + index * 1.28
        add_rect(slide, M, y, 6.3, 1.1, fill=CARD, line=LINE_L, radius=0.08)
        add_text(
            slide,
            number,
            M + 0.22,
            y + 0.23,
            0.55,
            0.3,
            size=10,
            color=color,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            title,
            M + 0.8,
            y + 0.16,
            1.7,
            0.3,
            size=13,
            color=TEXT,
            bold=True,
        )
        add_text(
            slide,
            detail,
            M + 0.8,
            y + 0.52,
            5.1,
            0.4,
            size=9.8,
            color=TEXT_2,
            line_spacing=1.45,
        )

    # Right-side positioning card.
    px, py, pw = 7.45, 1.66, 5.15
    add_rect(slide, px, py, pw, 4.88, fill=INK, line=None, radius=0.08)
    add_text(
        slide,
        "PRODUCT POSITION",
        px + 0.38,
        py + 0.34,
        2.8,
        0.22,
        size=7.5,
        color=TEAL_DARK,
        bold=True,
        spacing=80,
        line_spacing=1,
    )
    add_text(
        slide,
        "把非结构化招标文件，\n转化为可检查的生产资料。",
        px + 0.38,
        py + 0.72,
        pw - 0.76,
        1.15,
        size=19,
        color="F7F5EF",
        bold=True,
        line_spacing=1.4,
    )

    flow = [
        ("输入", "招标文件 PDF", "版式、目录、评分办法"),
        ("生产", "证据 + Agent", "检索、编写、审查、修订"),
        ("交付", "合规标书", "门禁、配图、DOCX"),
    ]
    for index, (label, title, detail) in enumerate(flow):
        y = py + 2.25 + index * 0.78
        add_text(
            slide,
            label,
            px + 0.38,
            y,
            0.55,
            0.22,
            size=8,
            color=TEAL_DARK,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            title,
            px + 1.05,
            y - 0.04,
            1.85,
            0.25,
            size=11.5,
            color="EEF1F4",
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            detail,
            px + 1.05,
            y + 0.27,
            2.8,
            0.22,
            size=8.5,
            color="8FA0B5",
            line_spacing=1,
        )
        if index < 2:
            add_line(
                slide,
                px + 0.45,
                y + 0.4,
                px + 0.45,
                y + 0.74,
                color=TEAL_DARK,
                width=0.8,
                opacity=35,
            )

    add_text(
        slide,
        "产品答案",
        px + 0.38,
        py + 4.36,
        0.9,
        0.22,
        size=8,
        color="8FA0B5",
        bold=True,
        spacing=50,
        line_spacing=1,
    )
    add_text(
        slide,
        "把“写作问题”变成“生产系统问题”。",
        px + 1.4,
        py + 4.33,
        pw - 1.75,
        0.28,
        size=10.5,
        color=AMBER_DARK,
        bold=True,
        line_spacing=1.1,
    )
    add_notes(
        slide,
        "痛点不是文字生成，而是信息、协同和可验证性。右侧把产品定位压成三层：输入结构化、生产过程化、交付可验证。"
        "这也是后面所有功能取舍的出发点。",
    )


# ---------------------------------------------------------------------------
# Slide 04 - Product map
# ---------------------------------------------------------------------------


def slide_product_map(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "PRODUCT MAP  /  03",
        "一个工作台，覆盖标书全生命周期",
        subtitle="六条业务主线，三层治理能力；Web 与 Electron 共用同一套 API、会话与项目上下文。",
        page=4,
    )

    add_section_label(slide, "BUSINESS WORKBENCH", M, 1.58)
    modules = [
        ("01", "上传解析", "PDF → 结构化知识", "MinerU + PyMuPDF", TEAL),
        ("02", "编写标书", "大纲 → 章节 → DOCX", "多 Agent + 门禁", TEAL),
        ("03", "公司库", "企业资料复用", "PostgreSQL JSONB", AMBER),
        ("04", "文档检索", "语义 + 关键词", "章节 / 页码 / 证据", BLUE),
        ("05", "文档库", "解析产物与历史 run", "按项目隔离", VIOLET),
        ("06", "任务历史", "服务端 run + 本地任务", "可继续编辑", VIOLET),
    ]
    card_w = 1.87
    gap = 0.17
    start_x = M
    for index, (number, title, detail, tech, color) in enumerate(modules):
        x = start_x + index * (card_w + gap)
        add_rect(slide, x, 1.9, card_w, 2.42, fill=CARD, line=LINE_L, radius=0.08)
        add_text(
            slide,
            number,
            x + 0.18,
            2.08,
            0.4,
            0.22,
            size=8,
            color=color,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            title,
            x + 0.18,
            2.42,
            card_w - 0.36,
            0.32,
            size=14,
            color=TEXT,
            bold=True,
            line_spacing=1.05,
        )
        add_text(
            slide,
            detail,
            x + 0.18,
            2.92,
            card_w - 0.36,
            0.55,
            size=9.5,
            color=TEXT_2,
            line_spacing=1.4,
        )
        add_line(slide, x + 0.18, 3.52, x + card_w - 0.18, 3.52, color=LINE_L, width=0.7)
        add_text(
            slide,
            tech,
            x + 0.18,
            3.65,
            card_w - 0.36,
            0.42,
            size=8,
            color=color,
            bold=True,
            line_spacing=1.2,
        )

    add_section_label(slide, "GOVERNANCE LAYER", M, 4.62)
    governance = [
        ("项目管理", "项目是默认上下文；成员与对象级 ACL 决定可见范围。", TEAL),
        ("审计日志", "append-only 哈希链；写操作有显式事件或请求兜底。", AMBER),
        ("用户与角色", "5 类业务角色 + service；会话可撤销，默认拒绝。", VIOLET),
    ]
    card_w2 = 3.88
    for index, (title, detail, color) in enumerate(governance):
        x = M + index * (card_w2 + 0.17)
        add_rect(slide, x, 4.95, card_w2, 1.3, fill=CARD, line=LINE_L, radius=0.08)
        add_circle(slide, x + 0.22, 5.16, 0.28, fill=color, opacity=12)
        add_circle(slide, x + 0.29, 5.23, 0.14, fill=color)
        add_text(
            slide,
            title,
            x + 0.64,
            5.12,
            1.5,
            0.26,
            size=10.5,
            color=TEXT,
            bold=True,
        )
        add_text(
            slide,
            detail,
            x + 0.22,
            5.55,
            card_w2 - 0.44,
            0.55,
            size=9.1,
            color=TEXT_2,
            line_spacing=1.45,
        )

    add_text(
        slide,
        "所有长任务异步化  ·  所有产物可追溯  ·  所有决策有留痕",
        M,
        6.53,
        SW - 2 * M,
        0.26,
        size=9,
        color=TEXT_3,
        bold=True,
        align=PP_ALIGN.CENTER,
        spacing=30,
        line_spacing=1,
    )
    add_notes(
        slide,
        "业务面六条主线，治理面三件事。项目管理不是可选配置，而是默认上下文：文档、run、产物都按项目过滤。"
        "工作台同时服务浏览器和 Electron，共用同源 API。",
    )


# ---------------------------------------------------------------------------
# Slide 05 - Roles and permissions
# ---------------------------------------------------------------------------


def slide_roles(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "ACCESS MODEL  /  04",
        "权限不是菜单开关，而是两道交集",
        subtitle="全局角色 ∩ 项目角色 = 有效权限；跨项目访问统一按 404 处理，不泄露资源存在性。",
        page=5,
    )

    # Matrix is drawn as a grid so the color treatment stays under our control.
    x0, y0 = M, 1.72
    col_ws = [2.15, 1.2, 1.25, 1.2, 1.15, 1.15, 1.0]
    header_h = 0.62
    row_h = 0.64
    headers = ["角色", "上传解析", "编写 / 编辑", "合规复核", "公司库", "常规导出", "用户管理"]
    total_w = sum(col_ws)

    add_rect(slide, x0, y0, total_w, header_h, fill=INK, line=None, radius=0.04)
    cx = x0
    for index, (header, width) in enumerate(zip(headers, col_ws)):
        add_text(
            slide,
            header,
            cx + 0.12,
            y0,
            width - 0.18,
            header_h,
            size=8.2 if index else 8.6,
            color="F4F2EC" if index == 0 else "C3CEDA",
            bold=index == 0,
            align=PP_ALIGN.LEFT if index == 0 else PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.MIDDLE,
            line_spacing=1.05,
        )
        cx += width

    rows = [
        ("查看者  viewer", [0, 0, 0, 0, 0, 0], "仅可查看项目内文档、任务与产物"),
        ("编写员  writer", [1, 1, 1, 0, 0, 0], "可上传、生成、编辑与续跑；不能导出"),
        ("审核员  reviewer", [0, 0, 1, 0, 0, 0], "只读 + 重新检查合规、生成修复方案"),
        ("投标经理  bid_manager", [1, 1, 1, 1, 1, 0], "公司库、常规导出与评审"),
        ("管理员  admin", [1, 1, 1, 1, 1, 1], "用户管理、删除公司与强制导出"),
        ("服务账号  service", [1, 1, 1, 1, 1, 0], "MCP 专用，不可登录；不含强制导出"),
    ]
    for row_index, (role, permissions, hint) in enumerate(rows):
        y = y0 + header_h + row_index * row_h
        fill = CARD if row_index % 2 == 0 else "F1F0EA"
        add_rect(slide, x0, y, total_w, row_h, fill=fill, line=None, radius=0.0)
        add_text(
            slide,
            role,
            x0 + 0.12,
            y,
            col_ws[0] - 0.18,
            row_h,
            size=9.3,
            color=TEXT,
            bold=row_index in (4, 5),
            anchor=MSO_ANCHOR.MIDDLE,
            line_spacing=1,
        )
        cx = x0 + col_ws[0]
        for col_index, allowed in enumerate(permissions):
            center_x = cx + col_ws[col_index + 1] / 2
            if allowed:
                add_circle(
                    slide,
                    center_x - 0.075,
                    y + row_h / 2 - 0.075,
                    0.15,
                    fill=TEAL if row_index < 4 else VIOLET,
                )
            else:
                add_text(
                    slide,
                    "—",
                    center_x - 0.2,
                    y,
                    0.4,
                    row_h,
                    size=10,
                    color="C5CBD3",
                    align=PP_ALIGN.CENTER,
                    anchor=MSO_ANCHOR.MIDDLE,
                    line_spacing=1,
                )
            cx += col_ws[col_index + 1]
        add_line(slide, x0, y + row_h, x0 + total_w, y + row_h, color="E7E5DE", width=0.5)

    add_text(
        slide,
        "●  允许    —  无权限    ·  实际权限同时受全局角色与项目成员角色限制",
        x0,
        y0 + header_h + len(rows) * row_h + 0.14,
        total_w,
        0.25,
        size=8,
        color=TEXT_3,
        line_spacing=1.2,
    )
    add_text(
        slide,
        "前端隐藏按钮只是体验优化；后端守卫才是权威。",
        x0,
        y0 + header_h + len(rows) * row_h + 0.46,
        total_w,
        0.24,
        size=7.4,
        color=TEXT_3,
        line_spacing=1.2,
    )

    # Right callout.
    px, py, pw = 10.02, 1.72, 2.55
    add_rect(slide, px, py, pw, 4.46, fill=INK, line=None, radius=0.08)
    add_text(
        slide,
        "EFFECTIVE ROLE",
        px + 0.22,
        py + 0.24,
        1.7,
        0.2,
        size=7,
        color=TEAL_DARK,
        bold=True,
        spacing=80,
        line_spacing=1,
    )
    add_text(
        slide,
        "全局角色\n  ∩\n项目角色",
        px + 0.22,
        py + 0.58,
        pw - 0.44,
        1.25,
        size=18,
        color="F7F5EF",
        bold=True,
        line_spacing=1.28,
    )
    add_line(
        slide,
        px + 0.22,
        py + 2.0,
        px + pw - 0.22,
        py + 2.0,
        color="2B3D52",
        width=0.8,
    )
    add_bullets(
        slide,
        [
            "X-Project-Id 必填，缺失 400",
            "非成员 / 不存在统一 404",
            "公司库全局共享，敏感字段脱敏",
            "最后一个启用管理员不可降级",
        ],
        px + 0.22,
        py + 2.2,
        pw - 0.44,
        1.45,
        size=8.4,
        color="B3BFCE",
        bullet_color=TEAL_DARK,
        line_spacing=1.35,
        gap=4,
    )
    add_notes(
        slide,
        "角色矩阵不是按菜单设计的，而是按操作设计的。真正的权限是全局角色和项目角色的交集，"
        "跨项目统一 404，避免泄露项目存在性。服务账号供 MCP 调用，不能登录，也不能强制导出。",
    )


# ---------------------------------------------------------------------------
# Slide 06 - Core journey
# ---------------------------------------------------------------------------


def slide_journey(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "CORE JOURNEY  /  05",
        "五个阶段，四个必须由人拍板的节点",
        subtitle="生成可以自动化，责任不能。系统把每一步变成可检查的状态，而不是黑盒等待。",
        page=6,
    )

    steps = [
        (
            "01",
            "上传解析",
            "MinerU / PyMuPDF\n抽取 · 分块 · 入库",
            "选择解析器\n是否抽取与入库",
            "INPUT",
            TEAL,
        ),
        (
            "02",
            "大纲确认",
            "生成章节骨架\n目标字数与要点",
            "编辑标题、要点\n锁定表单与附件",
            "GATE",
            AMBER,
        ),
        (
            "03",
            "章节生成",
            "检索证据\n写 → 审 → 条件修订",
            "续跑、单节重写\n或直接编辑正文",
            "GATE",
            AMBER,
        ),
        (
            "04",
            "质量复核",
            "21 项检查\n一致性台账 + 修复",
            "选择并应用修复\n重新组装",
            "GATE",
            AMBER,
        ),
        (
            "05",
            "导出交付",
            "门禁评估\nDOCX 严格导出",
            "处理 block\n强制导出需留痕",
            "GATE",
            AMBER,
        ),
    ]
    card_w = 2.25
    gap = 0.135
    start_x = M
    for index, (number, title, system_text, human_text, tag, color) in enumerate(steps):
        x = start_x + index * (card_w + gap)
        add_rect(slide, x, 1.62, card_w, 4.42, fill=CARD, line=LINE_L, radius=0.08)
        add_text(
            slide,
            number,
            x + 0.2,
            1.82,
            0.45,
            0.25,
            size=9,
            color=color,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            title,
            x + 0.2,
            2.12,
            card_w - 0.4,
            0.36,
            size=14,
            color=TEXT,
            bold=True,
            line_spacing=1.05,
        )
        add_text(
            slide,
            "系统",
            x + 0.2,
            2.65,
            0.5,
            0.2,
            size=7.5,
            color=TEXT_3,
            bold=True,
            spacing=60,
            line_spacing=1,
        )
        add_text(
            slide,
            system_text,
            x + 0.2,
            2.9,
            card_w - 0.4,
            0.55,
            size=9.2,
            color=TEXT_2,
            line_spacing=1.45,
        )
        add_line(slide, x + 0.2, 3.62, x + card_w - 0.2, 3.62, color=LINE_L, width=0.7)
        add_text(
            slide,
            "人",
            x + 0.2,
            3.78,
            0.4,
            0.2,
            size=7.5,
            color=AMBER,
            bold=True,
            spacing=60,
            line_spacing=1,
        )
        add_text(
            slide,
            human_text,
            x + 0.2,
            4.03,
            card_w - 0.4,
            0.55,
            size=9.2,
            color=TEXT,
            bold=True,
            line_spacing=1.45,
        )
        add_pill(
            slide,
            tag,
            x + 0.2,
            5.28,
            0.8,
            0.23,
            fill=color if index else TEAL,
            color="FFFFFF",
            size=7,
        )
        if index < len(steps) - 1:
            add_line(
                slide,
                x + card_w + 0.02,
                3.3,
                x + card_w + gap - 0.015,
                3.3,
                color="B9C1CC",
                width=1.0,
                arrow=True,
            )

    add_rect(slide, M, 6.27, SW - 2 * M, 0.5, fill=INK, line=None, radius=0.06)
    add_text(
        slide,
        "系统负责把不确定性显性化，人负责在有限选项中做取舍。",
        M + 0.25,
        6.27,
        SW - 2 * M - 0.5,
        0.5,
        size=10.5,
        color="F2F0EA",
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )
    add_notes(
        slide,
        "五个阶段对应四个关键人工门：大纲、章节、修复、导出。第一步也有输入选择，但真正的责任门从大纲开始。"
        "强调系统不是替人做决定，而是把可选项、风险和影响范围摆出来。",
    )


# ---------------------------------------------------------------------------
# Slide 07 - Workbench interaction
# ---------------------------------------------------------------------------


def slide_workbench(prs: Presentation, bg: Path) -> None:
    slide = content_slide(
        prs,
        "WORKBENCH  /  06",
        "把长任务做成可观察、可干预的状态机",
        subtitle="项目上下文、任务进度、章节状态与质量门禁，始终在同一个工作面里。",
        page=7,
        dark=True,
        bg=bg,
    )

    callouts = [
        ("01", "项目是默认上下文", "文档、run 与产物都按项目过滤。", TEAL_DARK),
        ("02", "任务状态实时可见", "queued / generating /\npaused / assembled。", BLUE_DARK),
        ("03", "章节级干预", "编辑大纲、续跑、重写或保存人工稿。", AMBER_DARK),
        ("04", "质量与交付", "合规、一致性、配图、门禁与导出。", VIOLET_DARK),
    ]
    for index, (number, title, detail, color) in enumerate(callouts):
        y = 1.72 + index * 1.22
        add_rect(slide, M, y, 2.3, 1.04, fill="0D1A2A", line="25364A", radius=0.08, opacity=80)
        add_rect(slide, M, y, 0.05, 1.04, fill=color, line=None, radius=0.02)
        add_text(
            slide,
            number,
            M + 0.18,
            y + 0.14,
            0.36,
            0.22,
            size=7.5,
            color=color,
            bold=True,
            line_spacing=1,
        )
        add_text(
            slide,
            title,
            M + 0.18,
            y + 0.38,
            1.95,
            0.28,
            size=10.5,
            color="F3F1EC",
            bold=True,
            line_spacing=1.1,
        )
        add_text(
            slide,
            detail,
            M + 0.18,
            y + 0.72,
            1.95,
            0.25,
            size=7.8,
            color="8FA0B5",
            line_spacing=1.25,
        )

    # App frame.
    ux, uy, uw, uh = 3.35, 1.58, 9.28, 5.1
    add_rect(slide, ux, uy, uw, uh, fill="0B1624", line="2B3D52", radius=0.045)
    add_text(
        slide,
        "工作台交互示意",
        ux + uw - 1.55,
        uy - 0.27,
        1.42,
        0.18,
        size=6.8,
        color="667A91",
        align=PP_ALIGN.RIGHT,
        line_spacing=1,
    )

    # Sidebar.
    add_rect(slide, ux, uy, 1.42, uh, fill="08121E", line=None, radius=0.045)
    add_circle(slide, ux + 0.22, uy + 0.22, 0.32, fill=TEAL, opacity=20)
    add_text(
        slide,
        "M",
        ux + 0.22,
        uy + 0.22,
        0.32,
        0.32,
        size=11,
        color=TEAL_DARK,
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )
    add_text(
        slide,
        "Much Agents",
        ux + 0.62,
        uy + 0.24,
        0.72,
        0.16,
        size=7.2,
        color="E6ECF2",
        bold=True,
        line_spacing=1,
    )
    add_text(
        slide,
        "标书工作台",
        ux + 0.62,
        uy + 0.41,
        0.72,
        0.14,
        size=5.8,
        color="65768B",
        line_spacing=1,
    )
    navs = ["上传解析", "编写标书", "文档库", "文档检索", "项目管理"]
    for index, nav in enumerate(navs):
        y = uy + 0.94 + index * 0.43
        if index == 1:
            add_rect(slide, ux + 0.14, y - 0.06, 1.12, 0.32, fill=TEAL, opacity=16, line="1A5E62", radius=0.3)
        add_circle(slide, ux + 0.24, y + 0.04, 0.1, fill=TEAL_DARK if index == 1 else "405266")
        add_text(
            slide,
            nav,
            ux + 0.42,
            y,
            0.8,
            0.2,
            size=7.2,
            color="F2F0EA" if index == 1 else "7E90A5",
            bold=index == 1,
            line_spacing=1,
        )
    add_circle(slide, ux + 0.24, uy + uh - 0.36, 0.1, fill=GREEN_DARK)
    add_text(
        slide,
        "后端在线",
        ux + 0.42,
        uy + uh - 0.4,
        0.75,
        0.18,
        size=6.4,
        color="9FB0C2",
        line_spacing=1,
    )

    # Top bar.
    main_x = ux + 1.42
    main_w = uw - 1.42
    add_rect(slide, main_x, uy, main_w, 0.58, fill="0D1A2A", line=None, radius=0.045)
    add_text(
        slide,
        "编写标书",
        main_x + 0.2,
        uy + 0.15,
        1.0,
        0.22,
        size=9.8,
        color="F5F3EE",
        bold=True,
        line_spacing=1,
    )
    add_rect(slide, main_x + 4.0, uy + 0.15, 1.57, 0.27, fill="142A3E", line="27445B", radius=0.3)
    add_text(
        slide,
        "▾  ×× 市政工程",
        main_x + 4.0,
        uy + 0.15,
        1.57,
        0.27,
        size=6.2,
        color=TEAL_DARK,
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )
    add_circle(slide, main_x + main_w - 0.43, uy + 0.14, 0.28, fill="21364B")
    add_text(
        slide,
        "管",
        main_x + main_w - 0.43,
        uy + 0.14,
        0.28,
        0.28,
        size=6.8,
        color="D7E1EA",
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )

    # Mini status cards.
    stats = [
        ("RUN 状态", "已组装", "gate.json 已生成", GREEN_DARK),
        ("章节进度", "11 / 11", "叙事章节已完成", TEAL_DARK),
        ("质量提醒", "2 warning", "无 block，可导出", AMBER_DARK),
    ]
    for index, (label, value, detail, color) in enumerate(stats):
        x = main_x + 0.2 + index * 2.1
        y = uy + 0.74
        add_rect(slide, x, y, 1.98, 0.6, fill="0E1C2D", line="26384D", radius=0.08)
        add_text(
            slide,
            label,
            x + 0.14,
            y + 0.1,
            0.8,
            0.17,
            size=6.2,
            color="8294A8",
            line_spacing=1,
        )
        add_text(
            slide,
            value,
            x + 0.92,
            y + 0.09,
            0.92,
            0.2,
            size=8.8,
            color=color,
            bold=True,
            align=PP_ALIGN.RIGHT,
            line_spacing=1,
        )
        add_text(
            slide,
            detail,
            x + 0.14,
            y + 0.34,
            1.25,
            0.16,
            size=6.0,
            color="5F7388",
            line_spacing=1,
        )

    # Outline panel.
    ox, oy, ow, oh = main_x + 0.2, uy + 1.52, 3.7, 2.4
    add_rect(slide, ox, oy, ow, oh, fill="0E1B2C", line="26384D", radius=0.06)
    add_text(slide, "大纲", ox + 0.16, oy + 0.14, 0.6, 0.2, size=8.6, color="F2F0EA", bold=True, line_spacing=1)
    add_text(
        slide,
        "11 个叙事章节",
        ox + 0.62,
        oy + 0.16,
        1.2,
        0.16,
        size=6.2,
        color="6E8298",
        line_spacing=1,
    )
    add_rect(slide, ox + 0.16, oy + 0.44, ow - 0.32, 0.05, fill="20374B", line=None, radius=0.5)
    add_rect(slide, ox + 0.16, oy + 0.44, ow - 0.32, 0.05, fill=TEAL_BRIGHT, line=None, radius=0.5, opacity=72)
    outline_rows = [
        ("6.2  施工方案与技术措施", "final", GREEN_DARK),
        ("6.3  质量管理体系与措施", "final", GREEN_DARK),
        ("6.4  安全管理体系与措施", "writing", AMBER_DARK),
        ("6.5  环境保护与文明施工", "final", GREEN_DARK),
    ]
    for index, (label, state, color) in enumerate(outline_rows):
        y = oy + 0.62 + index * 0.35
        add_circle(slide, ox + 0.19, y + 0.05, 0.09, fill=color)
        add_text(
            slide,
            label,
            ox + 0.37,
            y,
            2.2,
            0.18,
            size=6.8,
            color="C9D4DF",
            line_spacing=1,
        )
        add_text(
            slide,
            state,
            ox + ow - 0.58,
            y,
            0.4,
            0.17,
            size=5.7,
            color=color,
            bold=True,
            align=PP_ALIGN.RIGHT,
            line_spacing=1,
        )
    add_text(
        slide,
        "续跑",
        ox + 0.16,
        oy + oh - 0.34,
        0.55,
        0.2,
        size=5.8,
        color=TEAL_DARK,
        line_spacing=1,
    )
    add_text(
        slide,
        "取消",
        ox + 0.82,
        oy + oh - 0.34,
        0.55,
        0.2,
        size=5.8,
        color="7E90A5",
        line_spacing=1,
    )

    # Quality panel.
    qx, qy, qw, qh = ox + ow + 0.16, oy, 3.5, oh
    add_rect(slide, qx, qy, qw, qh, fill="0E1B2C", line="26384D", radius=0.06)
    add_text(slide, "质量门禁", qx + 0.16, qy + 0.14, 1.0, 0.2, size=8.6, color="F2F0EA", bold=True, line_spacing=1)
    add_pill(slide, "2 WARN", qx + qw - 0.84, qy + 0.13, 0.68, 0.22, fill=AMBER, color="FFFFFF", size=5.6)
    quality_rows = [
        ("合规检查", "21 项", "2 warning", AMBER_DARK),
        ("一致性", "21 类", "consistent", GREEN_DARK),
        ("AI 配图", "4 张", "manifest", GREEN_DARK),
        ("页数估算", "142 页", "< 150", GREEN_DARK),
    ]
    for index, (label, value, state, color) in enumerate(quality_rows):
        y = qy + 0.58 + index * 0.37
        add_text(slide, label, qx + 0.16, y, 0.8, 0.18, size=6.8, color="C9D4DF", line_spacing=1)
        add_text(
            slide,
            value,
            qx + 0.94,
            y,
            0.55,
            0.18,
            size=6.8,
            color="F2F0EA",
            bold=True,
            line_spacing=1,
        )
        add_circle(slide, qx + 1.62, y + 0.04, 0.08, fill=color)
        add_text(
            slide,
            state,
            qx + 1.78,
            y,
            0.84,
            0.18,
            size=5.8,
            color=color,
            line_spacing=1,
        )
    add_rect(slide, qx + 0.16, qy + oh - 0.52, qw - 0.32, 0.31, fill=TEAL, line=None, radius=0.3)
    add_text(
        slide,
        "严格导出 DOCX",
        qx + 0.16,
        qy + oh - 0.52,
        qw - 0.32,
        0.31,
        size=6.5,
        color="FFFFFF",
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )

    # Artifact strip.
    ax, ay, aw, ah = main_x + 0.2, uy + 4.08, main_w - 0.4, 0.72
    add_rect(slide, ax, ay, aw, ah, fill="0A1420", line="223246", radius=0.06)
    add_text(
        slide,
        "产物",
        ax + 0.14,
        ay + 0.11,
        0.5,
        0.16,
        size=6.0,
        color="6E8298",
        bold=True,
        line_spacing=1,
    )
    add_text(
        slide,
        "bid.md   ·   bid.docx   ·   gate.json   ·   compliance.json   ·   figures/",
        ax + 0.14,
        ay + 0.32,
        aw - 0.28,
        0.18,
        size=6.2,
        color="A9B7C7",
        font=FONT_MONO,
        line_spacing=1,
    )
    add_notes(
        slide,
        "工作台不是聊天窗口。项目切换在顶部，任务状态实时更新，章节可以单节续跑或编辑，质量门禁与导出按钮在同一屏。"
        "界面为示意图，用来解释信息层级，不代表某个固定皮肤。",
    )


# ---------------------------------------------------------------------------
# Slide 08 - Generation pipeline
# ---------------------------------------------------------------------------


def slide_generation(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "GENERATION  /  07",
        "确定性骨架，包住受控的 Agent",
        subtitle="大纲和组装是确定性代码；只有 11 个叙事章节经过 Writer / Reviewer / Reviser。",
        page=8,
    )

    nodes = [
        ("01", "大纲", "标题 · 字数 · 要点", "确定性", AMBER),
        ("02", "逐节检索", "Chroma + 关键词", "混合", BLUE),
        ("03", "Writer", "按节生成", "LLM", TEAL),
        ("04", "Reviewer", "问题清单", "LLM", TEAL),
        ("05", "Reviser", "条件触发", "LLM", VIOLET),
        ("06", "组装", "模板 + 配图", "确定性", AMBER),
    ]
    node_w, gap = 1.75, 0.25
    start_x = 0.78
    for index, (number, title, detail, tag, color) in enumerate(nodes):
        x = start_x + index * (node_w + gap)
        add_rect(slide, x, 1.56, node_w, 1.58, fill=CARD, line=LINE_L, radius=0.07)
        add_rect(slide, x, 1.56, node_w, 0.06, fill=color, line=None, radius=0.03)
        add_text(slide, number, x + 0.15, 1.74, 0.35, 0.2, size=7.2, color=color, bold=True, line_spacing=1)
        add_text(slide, title, x + 0.15, 1.98, node_w - 0.3, 0.28, size=12, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.15, 2.38, node_w - 0.3, 0.22, size=8.2, color=TEXT_2, line_spacing=1.15)
        add_pill(slide, tag, x + 0.15, 2.79, 0.62, 0.2, fill=color, color="FFFFFF", size=5.8)
        if index < len(nodes) - 1:
            add_line(
                slide,
                x + node_w + 0.035,
                2.35,
                x + node_w + gap - 0.025,
                2.35,
                color="9EA8B7",
                width=1.0,
                arrow=True,
            )

    # LLM scope bracket.
    bracket_x1 = start_x + 2 * (node_w + gap)
    bracket_x2 = start_x + 4 * (node_w + gap) + node_w
    add_line(slide, bracket_x1, 3.26, bracket_x2, 3.26, color=TEAL, width=1.0)
    add_line(slide, bracket_x1, 3.26, bracket_x1, 3.35, color=TEAL, width=1.0)
    add_line(slide, bracket_x2, 3.26, bracket_x2, 3.35, color=TEAL, width=1.0)
    add_text(
        slide,
        "LLM 只覆盖 11 个叙事章节  ·  并行 fan-out / 条件 revise / fan-in",
        bracket_x1,
        3.4,
        bracket_x2 - bracket_x1,
        0.22,
        size=8.2,
        color=TEAL,
        bold=True,
        align=PP_ALIGN.CENTER,
        line_spacing=1,
    )

    features = [
        ("A", "可编辑大纲", "标题、目标字数、编写要点、写作重点；用 revision 乐观锁保存。", TEAL),
        ("B", "章节检查点", "write / review / revise 后落库；resume 只补未完成章节。", BLUE),
        ("C", "单节控制", "编辑正文或重新生成；新稿成功前保留旧稿。", AMBER),
        ("D", "确定性边界", "表单与附件由模板生成；不让 LLM 改结构或编号。", VIOLET),
    ]
    feature_w = 2.9
    feature_gap = 0.15
    for index, (letter, title, detail, color) in enumerate(features):
        x = M + index * (feature_w + feature_gap)
        y = 4.18
        add_rect(slide, x, y, feature_w, 1.86, fill=CARD, line=LINE_L, radius=0.08)
        add_circle(slide, x + 0.18, y + 0.18, 0.3, fill=color, opacity=12)
        add_text(
            slide,
            letter,
            x + 0.18,
            y + 0.18,
            0.3,
            0.3,
            size=7.5,
            color=color,
            bold=True,
            align=PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.MIDDLE,
            line_spacing=1,
        )
        add_text(slide, title, x + 0.58, y + 0.2, 1.9, 0.26, size=11, color=TEXT, bold=True, line_spacing=1)
        add_text(
            slide,
            detail,
            x + 0.18,
            y + 0.72,
            feature_w - 0.36,
            0.75,
            size=8.8,
            color=TEXT_2,
            line_spacing=1.5,
        )
        tag_text = ["revision 乐观锁", "resume / checkpoint", "old draft retained", "template only"][index]
        add_pill(
            slide,
            tag_text,
            x + 0.18,
            y + 1.48,
            1.55,
            0.22,
            fill=color,
            color=color,
            size=6.2,
            opacity=12,
        )
    add_text(
        slide,
        "人工编辑、单节重写或大纲失效都会清空旧 gate 与过期报告；下一次组装重新计算。",
        M,
        6.38,
        SW - 2 * M,
        0.26,
        size=8.4,
        color=TEXT_3,
        align=PP_ALIGN.CENTER,
        line_spacing=1,
    )
    add_notes(
        slide,
        "这是产品层看到的生产线：确定性大纲进入检索，LLM 只处理 11 个叙事章节，最后由确定性代码组装。"
        "底部四个特征对应可编辑大纲、章节检查点、单节控制和确定性边界。表单与附件不经过 LLM。",
    )


# ---------------------------------------------------------------------------
# Slide 09 - Quality control
# ---------------------------------------------------------------------------


def slide_quality(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "QUALITY GATE  /  08",
        "让 AI 的产出，先过工程的门",
        subtitle="21 项合规检查与 21 类事实台账独立于生成模型；门禁只认确定性结果。",
        page=9,
    )

    layers = [
        (
            1,
            "合规检查",
            "21 项",
            "主体身份 · 工期质量 · 资格人员 · 业绩财务 · 保证金 · 章节完整 · 评分覆盖",
            "缺失资料或占位符是 warn；真实矛盾与失败章节是 block。",
            TEAL,
            1.55,
            1.0,
        ),
        (
            2,
            "一致性台账",
            "21 类",
            "21 类事实逐项记录 canonical 值、每处 occurrence 与冲突状态。",
            "关键事实冲突使台账进入 conflict；缺失和未核实只产生 warning。",
            BLUE,
            2.68,
            1.95,
        ),
        (
            3,
            "修复与门禁",
            "人可接管",
            "确定性 replace 优先；可选 LLM 候选也必须重新校验 find 唯一、章节可编辑、无未授权事实。",
            "应用修复等同人工编辑，run 退回 paused/edited，下一次组装重新计算。",
            VIOLET,
            4.76,
            1.08,
        ),
    ]
    for number, title, badge, detail, sub, color, y, h in layers:
        add_rect(slide, M, y, 7.55, h, fill=CARD, line=LINE_L, radius=0.07)
        add_rect(slide, M, y, 0.06, h, fill=color, line=None, radius=0.03)
        add_circle(slide, M + 0.22, y + 0.2, 0.34, fill=color, opacity=12)
        add_text(
            slide,
            f"0{number}",
            M + 0.22,
            y + 0.2,
            0.34,
            0.34,
            size=7.5,
            color=color,
            bold=True,
            align=PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.MIDDLE,
            line_spacing=1,
        )
        add_text(slide, title, M + 0.7, y + 0.17, 1.4, 0.24, size=11.5, color=TEXT, bold=True, line_spacing=1)
        add_pill(slide, badge, M + 2.08, y + 0.18, 0.75, 0.22, fill=color, color="FFFFFF", size=6.0)
        add_text(slide, detail, M + 0.7, y + 0.5, 6.55, 0.32, size=8.3, color=TEXT_2, line_spacing=1.3)
        add_text(slide, sub, M + 0.7, y + h - 0.3, 6.45, 0.22, size=7.4, color=TEXT_3, line_spacing=1.15)

    # Mini ledger inside layer two.
    lx, ly, lw = M + 4.5, 3.55, 2.95
    add_rect(slide, lx, ly, lw, 0.72, fill="F8F7F2", line="E6E3DA", radius=0.05)
    add_text(slide, "工期  180 日历天", lx + 0.12, ly + 0.1, 1.45, 0.18, size=6.7, color=TEXT, bold=True, line_spacing=1)
    add_text(slide, "6.2/6.6", lx + 1.5, ly + 0.1, 0.55, 0.18, size=6.4, color=TEXT_3, line_spacing=1)
    add_pill(slide, "conflict", lx + 2.12, ly + 0.08, 0.7, 0.2, fill=RED, color="FFFFFF", size=5.5)
    add_text(slide, "质量  合格", lx + 0.12, ly + 0.41, 1.45, 0.18, size=6.7, color=TEXT, bold=True, line_spacing=1)
    add_text(slide, "6.3", lx + 1.5, ly + 0.41, 0.55, 0.18, size=6.4, color=TEXT_3, line_spacing=1)
    add_pill(slide, "consistent", lx + 2.06, ly + 0.39, 0.76, 0.2, fill=GREEN, color="FFFFFF", size=5.3)

    # Gate card.
    gx, gy, gw, gh = 8.55, 1.55, 4.05, 4.29
    add_rect(slide, gx, gy, gw, gh, fill=INK, line=None, radius=0.08)
    add_text(
        slide,
        "EXPORT GATE",
        gx + 0.26,
        gy + 0.25,
        1.8,
        0.22,
        size=7.2,
        color=TEAL_DARK,
        bold=True,
        spacing=80,
        line_spacing=1,
    )
    add_text(
        slide,
        "严格导出只认 gate.json",
        gx + 0.26,
        gy + 0.54,
        gw - 0.52,
        0.3,
        size=13,
        color="F5F3EE",
        bold=True,
        line_spacing=1.1,
    )
    add_text(slide, "BLOCK", gx + 0.26, gy + 1.0, 0.6, 0.18, size=6.5, color=RED_DARK, bold=True, spacing=70, line_spacing=1)
    block_items = ["缺少 selected 内容", "存在失败章节", "高危 / redaction", "估算页数 > 150"]
    for index, item in enumerate(block_items):
        add_circle(slide, gx + 0.28, gy + 1.28 + index * 0.26, 0.07, fill=RED_DARK)
        add_text(slide, item, gx + 0.43, gy + 1.24 + index * 0.26, 2.9, 0.18, size=7.6, color="C7D1DD", line_spacing=1)
    add_text(slide, "WARN", gx + 0.26, gy + 2.43, 0.6, 0.18, size=6.5, color=AMBER_DARK, bold=True, spacing=70, line_spacing=1)
    warn_items = ["占位符 / 缺资料", "评分因素未覆盖", "配图失败或跳过", "合规 warn 级失败"]
    for index, item in enumerate(warn_items):
        add_circle(slide, gx + 0.28, gy + 2.7 + index * 0.24, 0.07, fill=AMBER_DARK)
        add_text(slide, item, gx + 0.43, gy + 2.66 + index * 0.24, 2.9, 0.18, size=7.6, color="C7D1DD", line_spacing=1)
    add_rect(slide, gx + 0.26, gy + gh - 0.58, gw - 0.52, 0.32, fill=TEAL, line=None, radius=0.3)
    add_text(
        slide,
        "POST /export  ·  严格拦截",
        gx + 0.26,
        gy + gh - 0.58,
        gw - 0.52,
        0.32,
        size=7,
        color="FFFFFF",
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )

    add_rect(slide, M, 5.98, SW - 2 * M, 0.58, fill=CARD, line=LINE_L, radius=0.06)
    add_text(
        slide,
        "直接下载产物保持宽松；只有严格导出拦截 block。force=true 会记录 forced 标记并进入审计。",
        M + 0.22,
        5.98,
        SW - 2 * M - 0.44,
        0.58,
        size=8.8,
        color=TEXT_2,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )
    add_notes(
        slide,
        "质量层与生成模型解耦：合规检查、事实台账、修复方案、门禁都由确定性代码给出最终结论。"
        "修复方案可以先确定性生成，也可以让 LLM 提供候选，但候选必须重新校验。"
        "直接下载和严格导出是两种语义，force=true 需要留痕。",
    )


# ---------------------------------------------------------------------------
# Slide 10 - AI figures
# ---------------------------------------------------------------------------


def _figure_org(slide, x: float, y: float, w: float, h: float) -> None:
    top_w, top_h = 0.72, 0.28
    top_x = x + w / 2 - top_w / 2
    add_rect(slide, top_x, y + 0.12, top_w, top_h, fill="122638", line="3A596F", radius=0.04)
    add_text(slide, "项目班子", top_x, y + 0.12, top_w, top_h, size=5.3, color="D9E5EE", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    mid_y = y + 0.65
    for index, label in enumerate(("项目经理", "技术负责人")):
        bx = x + 0.25 + index * 1.1
        add_rect(slide, bx, mid_y, 0.72, 0.27, fill="112233", line="335168", radius=0.04)
        add_text(slide, label, bx, mid_y, 0.72, 0.27, size=4.8, color="B9C9D6", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    bus_y = y + 0.5
    add_line(slide, x + w / 2, y + 0.4, x + w / 2, bus_y, color="4B6A82", width=0.8)
    add_line(slide, x + 0.61, bus_y, x + 1.71, bus_y, color="4B6A82", width=0.8)
    add_line(slide, x + 0.61, bus_y, x + 0.61, mid_y, color="4B6A82", width=0.8)
    add_line(slide, x + 1.71, bus_y, x + 1.71, mid_y, color="4B6A82", width=0.8)
    add_text(slide, "岗位 / 姓名来自资料库", x, y + 1.33, w, 0.2, size=5.8, color="8095A8", align=PP_ALIGN.CENTER, line_spacing=1)


def _figure_process(slide, x: float, y: float, w: float, h: float) -> None:
    labels = ["1", "2", "3", "4"]
    box_w, gap = 0.45, 0.15
    start = x + (w - (box_w * 4 + gap * 3)) / 2
    for index, label in enumerate(labels):
        bx = start + index * (box_w + gap)
        add_rect(slide, bx, y + 0.34, box_w, 0.48, fill="123044" if index % 2 == 0 else "1A3A49", line="3A5D72", radius=0.05, shape_type=MSO_SHAPE.CHEVRON)
        add_text(slide, label, bx, y + 0.34, box_w, 0.48, size=7, color="D9E5EE", bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_line(slide, x + 0.4, y + 1.02, x + w - 0.4, y + 1.02, color="3A5D72", width=0.8)
    add_text(slide, "每一步必须能在正文找到引用", x, y + 1.18, w, 0.2, size=5.8, color="8095A8", align=PP_ALIGN.CENTER, line_spacing=1)


def _figure_gantt(slide, x: float, y: float, w: float, h: float) -> None:
    for index in range(4):
        gy = y + 0.2 + index * 0.3
        add_text(slide, f"W{index + 1}", x + 0.2, gy - 0.02, 0.25, 0.14, size=4.8, color="71869A", line_spacing=1)
        add_line(slide, x + 0.5, gy + 0.08, x + w - 0.22, gy + 0.08, color="23394D", width=0.5)
        bar_x = x + 0.55 + index * 0.22
        bar_w = 0.55 + (3 - index) * 0.26
        add_rect(slide, bar_x, gy, bar_w, 0.15, fill=TEAL_DARK if index < 2 else AMBER_DARK, line=None, radius=0.4, opacity=85)
    add_line(slide, x + w - 0.3, y + 0.1, x + w - 0.3, y + 1.16, color="5C7A91", width=0.8, dash=True)
    add_text(slide, "里程碑", x + w - 0.5, y + 1.2, 0.45, 0.16, size=5.2, color="8095A8", align=PP_ALIGN.RIGHT, line_spacing=1)


def _figure_site(slide, x: float, y: float, w: float, h: float) -> None:
    add_rect(slide, x + 0.18, y + 0.1, 2.22, 1.2, fill=None, line="5A7A91", line_width=0.9, radius=0.02)
    add_rect(slide, x + 0.35, y + 0.28, 0.78, 0.42, fill="153347", line="3A5D72", radius=0.02)
    add_text(slide, "施工区", x + 0.35, y + 0.28, 0.78, 0.42, size=4.8, color="AFC2D0", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_rect(slide, x + 1.28, y + 0.28, 0.55, 0.34, fill="153347", line="3A5D72", radius=0.02)
    add_text(slide, "料场", x + 1.28, y + 0.28, 0.55, 0.34, size=4.8, color="AFC2D0", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_rect(slide, x + 0.35, y + 0.83, 0.66, 0.3, fill="153347", line="3A5D72", radius=0.02)
    add_text(slide, "办公", x + 0.35, y + 0.83, 0.66, 0.3, size=4.8, color="AFC2D0", align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_line(slide, x + 1.55, y + 1.3, x + 1.85, y + 1.3, color=AMBER_DARK, width=1.4)
    add_text(slide, "大门", x + 1.55, y + 1.14, 0.45, 0.14, size=4.8, color=AMBER_DARK, align=PP_ALIGN.CENTER, line_spacing=1)
    add_text(slide, "场地要素来自附件与正文", x, y + 1.4, w, 0.18, size=5.6, color="8095A8", align=PP_ALIGN.CENTER, line_spacing=1)


def slide_figures(prs: Presentation, bg: Path) -> None:
    slide = content_slide(
        prs,
        "AI FIGURES  /  09",
        "配图只做“事实可视化”，不做文生图",
        subtitle="四类图由确定性渲染器生成；数据只来自招标事实、投标人资料与已脱敏正文。",
        page=10,
        dark=True,
        bg=bg,
    )
    figures = [
        ("FIG-01", "组织架构图", "项目班子与岗位关系", "top-07 章节末尾", _figure_org),
        ("FIG-02", "工艺流程图", "可引用的关键工序", "narr-06-02 章节末尾", _figure_process),
        ("FIG-03", "进度横道图", "工期、里程碑与资源", "替换附件四占位符", _figure_gantt),
        ("FIG-04", "施工总平面图", "场地、临建与大门", "替换附件五占位符", _figure_site),
    ]
    card_w = 2.82
    gap = 0.17
    for index, (fig_id, title, detail, placement, draw_fn) in enumerate(figures):
        x = M + index * (card_w + gap)
        y = 1.68
        add_rect(slide, x, y, card_w, 3.78, fill="0E1B2C", line="2A3C51", radius=0.06)
        add_text(slide, fig_id, x + 0.18, y + 0.16, 0.9, 0.18, size=6.4, color=TEAL_DARK, bold=True, spacing=50, line_spacing=1)
        add_text(slide, "确定渲染", x + card_w - 0.76, y + 0.16, 0.58, 0.18, size=5.6, color="6F8499", align=PP_ALIGN.RIGHT, line_spacing=1)
        draw_fn(slide, x + 0.24, y + 0.42, card_w - 0.48, 1.62)
        add_text(slide, title, x + 0.18, y + 2.2, card_w - 0.36, 0.28, size=12.5, color="F2F0EA", bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.18, y + 2.56, card_w - 0.36, 0.24, size=7.4, color="A5B4C4", line_spacing=1.2)
        add_line(slide, x + 0.18, y + 3.0, x + card_w - 0.18, y + 3.0, color="263A4D", width=0.7)
        add_text(slide, placement, x + 0.18, y + 3.18, card_w - 0.36, 0.3, size=6.8, color="7B8EA2", line_spacing=1.25)

    # Principles strip.
    strip_y = 5.78
    add_rect(slide, M, strip_y, SW - 2 * M, 0.96, fill="0C1725", line="263A4D", radius=0.06, opacity=85)
    principles = [
        ("缺失即标注", "人员 / 日期 / 工期缺失 → 【待填写】或跳过", TEAL_DARK),
        ("不文生图", "不调用文生图模型，不补造事实", VIOLET_DARK),
        ("可重建", "source_hash 复用未变图片；编辑后清空", BLUE_DARK),
        ("不阻断", "配图失败只产生 warn，不改变 21 项检查", AMBER_DARK),
    ]
    col_w = (SW - 2 * M) / 4
    for index, (title, detail, color) in enumerate(principles):
        x = M + index * col_w
        add_circle(slide, x + 0.18, strip_y + 0.22, 0.1, fill=color)
        add_text(slide, title, x + 0.37, strip_y + 0.16, 1.35, 0.2, size=7.8, color="E9EEF3", bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.37, strip_y + 0.43, col_w - 0.58, 0.32, size=6.6, color="8FA2B5", line_spacing=1.3)
    add_notes(
        slide,
        "AI 配图是最容易被误解的部分，所以边界要说清楚：不是文生图，不是装饰图，而是把已有事实确定性画出来。"
        "组织架构、工艺流程、进度横道、施工总平面各自有固定插入位置和数据来源。"
        "缺资料时标注或跳过，绘图失败只产生 warn，不阻断组装。",
    )


# ---------------------------------------------------------------------------
# Slide 11 - Architecture overview
# ---------------------------------------------------------------------------


def slide_architecture(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "SYSTEM ARCHITECTURE  /  10",
        "一条主链路，四类基础设施",
        subtitle="同步 API 负责状态与权限，异步 worker 负责重任务；四种存储各守一条边界。",
        page=11,
    )

    x_content, w_content = 1.92, 7.62
    bands = [
        ("体验层", "EXPERIENCE", 1.55, 0.72, TEAL),
        ("应用层", "APPLICATION", 2.4, 1.02, TEAL),
        ("任务层", "ASYNC TASKS", 3.58, 0.9, AMBER),
        ("数据层", "DATA", 4.64, 1.05, VIOLET),
    ]
    for title, english, y, h, color in bands:
        add_text(slide, title, M, y + 0.1, 0.9, 0.22, size=9.2, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, english, M, y + 0.34, 1.1, 0.16, size=5.8, color=color, bold=True, spacing=60, line_spacing=1)
        add_rect(slide, x_content, y, w_content, h, fill=CARD, line=LINE_L, radius=0.06)

    def box(x, y, w, h, title, detail, color, *, dark=False):
        add_rect(slide, x, y, w, h, fill="111E2F" if dark else "F8F8F5", line="2D4054" if dark else "E1E5EA", radius=0.05)
        add_text(slide, title, x + 0.12, y + 0.08, w - 0.24, 0.2, size=7.6, color="F1EFEA" if dark else TEXT, bold=True, line_spacing=1)
        if detail:
            add_text(slide, detail, x + 0.12, y + 0.3, w - 0.24, 0.22, size=6.2, color="95A8BB" if dark else TEXT_3, line_spacing=1.15)

    # Experience.
    box(x_content + 0.15, 1.67, 3.6, 0.48, "React 19 / Electron 工作台", "Web 与桌面共用 API、会话与项目上下文", TEAL)
    box(x_content + 3.9, 1.67, 3.55, 0.48, "Nginx", "TLS · 静态托管 · /api 反向代理", TEAL)

    # Application.
    box(x_content + 0.15, 2.54, 2.05, 0.74, "FastAPI", "认证 · 路由 · 流式产物", TEAL)
    box(x_content + 2.3, 2.54, 1.6, 0.74, "Auth + ACL", "RBAC · 项目守卫", TEAL)
    box(x_content + 4.0, 2.54, 1.6, 0.74, "BidRun", "大纲 · 章节 · 门禁", TEAL)
    box(x_content + 5.7, 2.54, 1.55, 0.74, "MCP proxy", "白名单工具", TEAL)

    # Async tasks.
    box(x_content + 0.15, 3.72, 2.35, 0.6, "Celery · parse", "MinerU / PyMuPDF · 抽取 · 入库", AMBER)
    box(x_content + 2.62, 3.72, 2.35, 0.6, "Celery · bid", "章节生成 · 审查 · 组装", AMBER)
    box(x_content + 5.09, 3.72, 2.15, 0.6, "local runner", "无 Redis 的本机兜底", AMBER)

    # Data.
    data = [
        ("PostgreSQL", "run / 大纲 / 审计", TEAL),
        ("Redis", "任务状态 · 24h TTL", AMBER),
        ("MinIO", "对象权威 · 私有 profile", VIOLET),
        ("Chroma", "向量索引 · 可重建", BLUE),
    ]
    for index, (title, detail, color) in enumerate(data):
        bx = x_content + 0.14 + index * 1.82
        box(bx, 4.79, 1.7, 0.74, title, detail, color)

    # External providers.
    ex, ey, ew, eh = 9.85, 1.55, 2.75, 4.14
    add_rect(slide, ex, ey, ew, eh, fill=INK, line=None, radius=0.07)
    add_text(slide, "EXTERNAL", ex + 0.22, ey + 0.22, 1.5, 0.18, size=6.8, color=TEAL_DARK, bold=True, spacing=80, line_spacing=1)
    add_text(slide, "外部依赖", ex + 0.22, ey + 0.48, 1.7, 0.26, size=12.5, color="F5F3EE", bold=True, line_spacing=1)
    providers = [
        ("MinerU", "云端解析 · PyMuPDF 兜底"),
        ("DeepSeek", "结构化抽取 · 写作 Agent"),
        ("智谱", "embedding-3 向量"),
        ("MCP Servers", "证据 / 资料 / 绘图 · 默认关闭"),
    ]
    for index, (name, detail) in enumerate(providers):
        y = ey + 1.08 + index * 0.66
        add_circle(slide, ex + 0.24, y + 0.03, 0.08, fill=TEAL_DARK)
        add_text(slide, name, ex + 0.4, y - 0.03, 1.9, 0.2, size=8.2, color="EDEFF1", bold=True, line_spacing=1)
        add_text(slide, detail, ex + 0.4, y + 0.22, 2.05, 0.2, size=6.2, color="8FA2B5", line_spacing=1.1)
    add_text(
        slide,
        "可替换 · 可降级 · MCP 外部证据默认不信任",
        ex + 0.22,
        ey + eh - 0.4,
        ew - 0.44,
        0.22,
        size=6.2,
        color="6F8499",
        line_spacing=1,
    )

    # Short connectors between the layers.
    for y1, y2, color in ((2.27, 2.4, TEAL), (3.42, 3.58, TEAL), (4.48, 4.64, AMBER)):
        add_line(slide, x_content + 3.8, y1, x_content + 3.8, y2 - 0.02, color=color, width=1.0, arrow=True, opacity=55)

    # Bottom state legend.
    legend = [
        ("PostgreSQL", "事实源", TEAL),
        ("Redis", "瞬时任务", AMBER),
        ("MinIO", "对象权威", VIOLET),
        ("Chroma", "语义索引", BLUE),
    ]
    for index, (name, role, color) in enumerate(legend):
        x = M + index * 2.95
        add_circle(slide, x, 5.97, 0.1, fill=color)
        add_text(slide, name, x + 0.18, 5.9, 1.0, 0.2, size=7.4, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, role, x + 0.18, 6.13, 1.7, 0.18, size=6.4, color=TEXT_3, line_spacing=1)
    add_text(
        slide,
        "PostgreSQL 是 run / 大纲 / 章节的事实源；Redis 丢失不影响持久化；MinIO 对象是权威；Chroma 可从 chunks 重建。",
        M,
        6.48,
        SW - 2 * M,
        0.2,
        size=7.2,
        color=TEXT_3,
        line_spacing=1,
    )
    add_notes(
        slide,
        "架构先看四条横向边界：体验层只通过 API；应用层统一做认证、项目守卫、run 服务和产物流；"
        "任务层把 parse 与 bid 拆成不同队列；数据层四种存储各司其职。右侧外部依赖都可替换或降级。"
        "下面一行再次强调四种存储的事实源角色。",
    )


# ---------------------------------------------------------------------------
# Slide 12 - Parsing and retrieval
# ---------------------------------------------------------------------------


def slide_parsing(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "PARSING & RETRIEVAL  /  11",
        "先把招标文件，变成可检索、可引用的知识",
        subtitle="解析、结构对齐、抽取、分块、向量化；每一步都留下可复用的确定性产物。",
        page=12,
    )

    stages = [
        ("01", "文档解析", "MinerU v4 / PyMuPDF 兜底；超过 200 页按平衡页数分片。"),
        ("02", "结构对齐", "PDF 书签 + page_idx；TOC end_page 按同级或更高级目录计算。"),
        ("03", "清洗", "页脚哈希水印与页码清理；不使用 redaction 破坏原文。"),
        ("04", "结构化抽取", "DeepSeek JSON 模式 + Pydantic 校验；失败回退正则。"),
        ("05", "分块", "以 TOC section 为边界；默认 700 字 / 120 重叠。"),
        ("06", "向量入库", "智谱 embedding-3 → Chroma；upsert 并清理旧 id。"),
    ]
    x = 0.82
    start_y = 1.6
    row_step = 0.66
    line_x = x + 0.13
    add_line(
        slide,
        line_x,
        start_y + 0.22,
        line_x,
        start_y + (len(stages) - 1) * row_step + 0.22,
        color="C7D1DB",
        width=1.0,
    )
    for index, (number, title, detail) in enumerate(stages):
        y = start_y + index * row_step
        add_circle(slide, x, y + 0.08, 0.26, fill=TEAL if index < 4 else VIOLET, line=None)
        add_text(
            slide,
            number,
            x,
            y + 0.08,
            0.26,
            0.26,
            size=5.8,
            color="FFFFFF",
            bold=True,
            align=PP_ALIGN.CENTER,
            anchor=MSO_ANCHOR.MIDDLE,
            line_spacing=1,
        )
        add_text(slide, title, x + 0.38, y + 0.02, 1.35, 0.22, size=10.2, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.38, y + 0.28, 6.48, 0.26, size=8.0, color=TEXT_2, line_spacing=1.3)
        if index < len(stages) - 1:
            add_line(slide, x, y + 0.58, x + 7.0, y + 0.58, color="EAE7DF", width=0.6)

    # Artifact tree.
    ax, ay, aw, ah = 8.42, 1.56, 4.18, 3.25
    add_rect(slide, ax, ay, aw, ah, fill=INK, line=None, radius=0.07)
    add_text(
        slide,
        "ARTIFACTS  /  PARSED",
        ax + 0.24,
        ay + 0.22,
        2.4,
        0.18,
        size=6.8,
        color=TEAL_DARK,
        bold=True,
        spacing=80,
        line_spacing=1,
    )
    add_text(
        slide,
        "upload/parsed/<doc_id>/",
        ax + 0.24,
        ay + 0.48,
        3.5,
        0.2,
        size=7.8,
        color="A9B7C7",
        font=FONT_MONO,
        line_spacing=1,
    )
    files = [
        ("full.md", "全文 Markdown"),
        ("content_list.json", "版式与页码块"),
        ("toc.json", "目录与起止页"),
        ("extraction.json", "评标因素与事实"),
        ("chunks.jsonl", "检索分块"),
        ("ingest.json", "向量入库记录"),
        ("meta.json", "sha256 缓存键"),
    ]
    for index, (name, detail) in enumerate(files):
        y = ay + 0.82 + index * 0.3
        add_text(slide, "—", ax + 0.24, y - 0.01, 0.15, 0.18, size=7.2, color="456179", line_spacing=1)
        add_text(slide, name, ax + 0.42, y - 0.02, 1.45, 0.18, size=7.0, color="E3E9EF", font=FONT_MONO, line_spacing=1)
        add_text(slide, detail, ax + 1.88, y - 0.02, 1.9, 0.18, size=6.3, color="8296AA", line_spacing=1)

    add_text(
        slide,
        "同一内容重复上传命中 SHA-256 解析缓存；\nChroma / 智谱不可用时，检索自动回退关键词。",
        ax + 0.02,
        4.96,
        aw - 0.04,
        0.56,
        size=7.4,
        color=TEXT_3,
        line_spacing=1.45,
    )

    callouts = [
        ("200 页上限", "MinerU 超过 200 页时按平衡页数分片，再按页码偏移合并。", AMBER),
        ("三种抽取结果", "method 记录 llm / llm+regex / regex_fallback；失败不阻断。", TEAL),
        ("证据颗粒度", "每个 chunk 带 section_id 与页码，检索结果可直接回溯原文。", BLUE),
    ]
    for index, (title, detail, color) in enumerate(callouts):
        x = M + index * 4.03
        add_rect(slide, x, 5.72, 3.87, 0.98, fill=CARD, line=LINE_L, radius=0.07)
        add_circle(slide, x + 0.18, 5.87, 0.1, fill=color)
        add_text(slide, title, x + 0.36, 5.82, 1.3, 0.2, size=7.6, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.18, 6.1, 3.5, 0.45, size=7.2, color=TEXT_2, line_spacing=1.35)
    add_notes(
        slide,
        "解析链路的产物不是临时文件，而是可复用的事实。目录、抽取、分块、入库都缓存；"
        "检索在 Chroma 不可用时自动降级到关键词，结果依然保留章节和页码。"
        "这也是后续引用契约的基础。",
    )


# ---------------------------------------------------------------------------
# Slide 13 - Agent runtime
# ---------------------------------------------------------------------------


def slide_agents(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "AGENT RUNTIME  /  12",
        "同一套 Agent 内核，两种执行壳",
        subtitle="LangGraph 负责一次性 fan-out；持久化 staged runner 负责章节级检查点与续跑。",
        page=13,
    )

    px, py, pw, ph = M, 1.55, 7.3, 4.95
    add_rect(slide, px, py, pw, ph, fill=CARD, line=LINE_L, radius=0.07)
    add_text(slide, "LangGraph 执行图", px + 0.24, py + 0.2, 2.0, 0.24, size=11, color=TEXT, bold=True, line_spacing=1)
    add_text(
        slide,
        "StateGraph · Send fan-out / fan-in",
        px + pw - 2.35,
        py + 0.24,
        2.1,
        0.18,
        size=6.6,
        color=TEXT_3,
        align=PP_ALIGN.RIGHT,
        line_spacing=1,
    )

    center_x = px + pw / 2
    node_w = 2.0
    node_h = 0.44

    def graph_node(x, y, title, detail, color, fill="F8F8F5"):
        add_rect(slide, x, y, node_w, node_h, fill=fill, line=color, line_width=0.9, radius=0.3)
        add_text(slide, title, x + 0.16, y + 0.05, 0.85, 0.18, size=8.0, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.86, y + 0.08, 1.0, 0.17, size=6.1, color=TEXT_3, align=PP_ALIGN.RIGHT, line_spacing=1)

    plan_y = py + 0.55
    research_y = py + 1.2
    writer_y = py + 1.9
    review_y = py + 2.68
    revise_y = py + 3.32
    assemble_y = py + 4.05
    graph_node(center_x - node_w / 2, plan_y, "Plan", "确定性", AMBER)
    graph_node(center_x - node_w / 2, research_y, "Research", "逐节检索", BLUE)

    writer_w = 1.55
    writer_xs = [px + 0.975, center_x - writer_w / 2, px + pw - 0.975 - writer_w]
    for index, wx in enumerate(writer_xs):
        add_rect(slide, wx, writer_y, writer_w, 0.48, fill="EFFAF8", line=TEAL, line_width=0.8, radius=0.18)
        add_text(
            slide,
            "Writer",
            wx + 0.1,
            writer_y + 0.04,
            writer_w - 0.2,
            0.17,
            size=7.2,
            color=TEXT,
            bold=True,
            align=PP_ALIGN.CENTER,
            line_spacing=1,
        )
        add_text(
            slide,
            ["6.2", "6.3", "6.4 …"][index],
            wx + 0.1,
            writer_y + 0.24,
            writer_w - 0.2,
            0.15,
            size=5.8,
            color=TEAL,
            align=PP_ALIGN.CENTER,
            line_spacing=1,
        )

    graph_node(center_x - node_w / 2, review_y, "Reviewer", "问题清单", TEAL)
    graph_node(center_x - node_w / 2, revise_y, "Reviser", "条件触发", VIOLET)
    graph_node(center_x - node_w / 2, assemble_y, "Assemble", "确定性", AMBER)

    # Vertical connectors.
    add_line(slide, center_x, plan_y + node_h, center_x, research_y, color="A8B2BE", width=1.0, arrow=True)
    add_line(slide, center_x, research_y + node_h, center_x, writer_y - 0.14, color="A8B2BE", width=1.0)
    bus_y = writer_y - 0.14
    add_line(slide, writer_xs[0] + writer_w / 2, bus_y, writer_xs[2] + writer_w / 2, bus_y, color="A8B2BE", width=0.9)
    for wx in writer_xs:
        add_line(slide, wx + writer_w / 2, bus_y, wx + writer_w / 2, writer_y, color="A8B2BE", width=0.9, arrow=True)

    writer_bottom = writer_y + 0.48
    bus_y2 = review_y - 0.15
    for wx in writer_xs:
        add_line(slide, wx + writer_w / 2, writer_bottom, wx + writer_w / 2, bus_y2, color="A8B2BE", width=0.8)
    add_line(slide, writer_xs[0] + writer_w / 2, bus_y2, writer_xs[2] + writer_w / 2, bus_y2, color="A8B2BE", width=0.9)
    add_line(slide, center_x, bus_y2, center_x, review_y, color="A8B2BE", width=1.0, arrow=True)
    add_line(slide, center_x, review_y + node_h, center_x, revise_y, color=VIOLET, width=1.0, dash=True, arrow=True)
    add_text(
        slide,
        "needs_revision",
        center_x + 0.1,
        review_y + node_h + 0.08,
        1.1,
        0.16,
        size=5.6,
        color=VIOLET,
        line_spacing=1,
    )
    add_line(slide, center_x, revise_y + node_h, center_x, assemble_y, color="A8B2BE", width=1.0, arrow=True)

    # Pass bypass.
    bypass_x = writer_xs[2] + writer_w + 0.18
    add_line(slide, center_x + node_w / 2, review_y + 0.1, bypass_x, review_y + 0.1, color="A8B2BE", width=0.8)
    add_line(slide, bypass_x, review_y + 0.1, bypass_x, assemble_y + 0.12, color="A8B2BE", width=0.8, dash=True)
    add_line(slide, bypass_x, assemble_y + 0.12, center_x + node_w / 2, assemble_y + 0.12, color="A8B2BE", width=0.8, arrow=True)
    add_text(slide, "pass", bypass_x + 0.05, review_y + 0.82, 0.4, 0.15, size=5.6, color=TEXT_3, line_spacing=1)

    # Staged runtime.
    rx, ry, rw, rh = 8.28, 1.55, 4.33, 4.95
    add_rect(slide, rx, ry, rw, rh, fill=INK, line=None, radius=0.08)
    add_text(
        slide,
        "PERSISTENT RUN  /  STAGED",
        rx + 0.24,
        ry + 0.22,
        2.8,
        0.2,
        size=6.8,
        color=TEAL_DARK,
        bold=True,
        spacing=70,
        line_spacing=1,
    )
    add_text(
        slide,
        "章节是持久化单位，\n不是一次 API 调用。",
        rx + 0.24,
        ry + 0.55,
        rw - 0.48,
        0.85,
        size=17,
        color="F5F3EE",
        bold=True,
        line_spacing=1.35,
    )
    tables = ["bid_runs", "bid_outline_sections", "bid_section_states"]
    for index, table in enumerate(tables):
        y = ry + 1.55 + index * 0.31
        add_text(slide, "▦", rx + 0.24, y, 0.2, 0.18, size=6.8, color=TEAL_DARK, line_spacing=1)
        add_text(slide, table, rx + 0.48, y - 0.01, 3.0, 0.18, size=7.4, color="D7E1EA", font=FONT_MONO, line_spacing=1)
    add_bullets(
        slide,
        [
            "lease + heartbeat + 活跃唯一索引，防重复生成",
            "write / review / revise 后写检查点",
            "resume 只补 pending / failed / stale 章节",
            "单节编辑或重写；新稿成功前保留旧稿",
            "取消在章节边界生效",
        ],
        rx + 0.24,
        ry + 2.6,
        rw - 0.48,
        1.65,
        size=7.6,
        color="B5C2D0",
        bullet_color=TEAL_DARK,
        line_spacing=1.35,
        gap=4,
    )
    add_text(
        slide,
        "profile 不进 Redis / Celery payload",
        rx + 0.24,
        ry + rh - 0.95,
        rw - 0.48,
        0.18,
        size=6.2,
        color="A8B7C7",
        bold=True,
        align=PP_ALIGN.CENTER,
        line_spacing=1,
    )
    add_rect(slide, rx + 0.24, ry + rh - 0.66, rw - 0.48, 0.36, fill="10273A", line="244357", radius=0.3)
    add_text(
        slide,
        "queued → generating → paused/edited → assembled",
        rx + 0.24,
        ry + rh - 0.66,
        rw - 0.48,
        0.36,
        size=6.2,
        color=TEAL_DARK,
        bold=True,
        align=PP_ALIGN.CENTER,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )
    add_notes(
        slide,
        "Agent 内核是同一套 Writer / Reviewer / Reviser。CLI 和一次性 API 用 LangGraph 做 fan-out；"
        "持久化 bid-runs 用 staged runner，把每个章节的 write/review/revise 结果写入 PostgreSQL。"
        "因此中断后可以续跑，单节可以重写，profile 不进入 Redis/Celery payload。",
    )


# ---------------------------------------------------------------------------
# Slide 14 - Data and storage
# ---------------------------------------------------------------------------


def slide_data(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "DATA & STORAGE  /  13",
        "四种状态，各有唯一事实源",
        subtitle="把“事实、进度、对象、索引”分开存储，失败时可重建，审计时可解释。",
        page=14,
    )
    cards = [
        (
            "FACT SOURCE",
            "PostgreSQL",
            "事实源",
            "users · projects · documents · bid_runs · outline · section states · audit_events",
            "事务一致 · Alembic 迁移 · 备份恢复",
            "SYSTEM OF RECORD",
            "不可替代",
            TEAL,
        ),
        (
            "EPHEMERAL",
            "Redis",
            "瞬时状态",
            "Celery broker + job hash；TTL 24h，实时进度轮询。",
            "可丢失 · 不承载 run 事实",
            "JOB STATE",
            "可丢失",
            AMBER,
        ),
        (
            "AUTHORITATIVE",
            "MinIO",
            "对象权威",
            "inbox / parsed / bids / private profiles；本地 upload 只是工作缓存。",
            "远程优先 · 缺失回退本地 · 私有前缀隔离",
            "OBJECT STORE",
            "权威对象",
            VIOLET,
        ),
        (
            "REBUILDABLE",
            "Chroma",
            "语义索引",
            "bidding_documents collection；以 chunk 为最小检索单位。",
            "可从 chunks.jsonl 重建 · 不写入事实",
            "VECTOR INDEX",
            "可重建",
            BLUE,
        ),
    ]
    card_w = 2.82
    gap = 0.17
    for index, (tag, title, subtitle, detail, note, badge, status, color) in enumerate(cards):
        x = M + index * (card_w + gap)
        y = 1.6
        add_rect(slide, x, y, card_w, 3.42, fill=CARD, line=LINE_L, radius=0.07)
        add_rect(slide, x, y, card_w, 0.06, fill=color, line=None, radius=0.03)
        add_text(slide, tag, x + 0.18, y + 0.2, 1.8, 0.17, size=5.8, color=color, bold=True, spacing=60, line_spacing=1)
        add_text(slide, title, x + 0.18, y + 0.46, card_w - 0.36, 0.32, size=16, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, subtitle, x + 0.18, y + 0.84, card_w - 0.36, 0.24, size=8.4, color=color, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.18, y + 1.2, card_w - 0.36, 0.92, size=7.6, color=TEXT_2, line_spacing=1.45)
        add_line(slide, x + 0.18, y + 2.2, x + card_w - 0.18, y + 2.2, color=LINE_L, width=0.7)
        add_text(slide, note, x + 0.18, y + 2.34, card_w - 0.36, 0.4, size=6.8, color=TEXT_3, line_spacing=1.35)
        add_pill(slide, badge, x + 0.18, y + 2.95, 1.28, 0.22, fill=color, color=color, size=5.5, opacity=12)
        add_text(
            slide,
            status,
            x + 1.55,
            y + 2.96,
            0.95,
            0.2,
            size=6.2,
            color=color,
            bold=True,
            align=PP_ALIGN.RIGHT,
            line_spacing=1,
        )

    # Object map.
    ox, oy, ow, oh = M, 5.28, SW - 2 * M, 1.42
    add_rect(slide, ox, oy, ow, oh, fill=CARD, line=LINE_L, radius=0.07)
    add_text(slide, "OBJECT MAP", ox + 0.22, oy + 0.18, 1.3, 0.18, size=6.6, color=TEXT_3, bold=True, spacing=70, line_spacing=1)
    add_text(
        slide,
        "bids/<doc_id>/<run_id>/",
        ox + 0.22,
        oy + 0.43,
        2.0,
        0.2,
        size=7.6,
        color=TEXT,
        font=FONT_MONO,
        bold=True,
        line_spacing=1,
    )
    add_text(
        slide,
        "bid.md · compliance.json · consistency.json · figures/*.png",
        ox + 2.12,
        oy + 0.43,
        4.3,
        0.2,
        size=7.0,
        color=TEXT_2,
        font=FONT_MONO,
        line_spacing=1,
    )
    add_text(
        slide,
        "private/profiles/<doc_id>/<run_id>.json",
        ox + 0.22,
        oy + 0.78,
        3.2,
        0.2,
        size=7.2,
        color=RED,
        font=FONT_MONO,
        bold=True,
        line_spacing=1,
    )
    add_pill(slide, "PRIVATE", ox + 3.45, oy + 0.76, 0.68, 0.22, fill=RED, color="FFFFFF", size=5.5)
    add_text(
        slide,
        "绝不出现在 artifact allowlist、MCP 输出、Redis/Celery payload、审计 detail 或下载 URL。",
        ox + 4.28,
        oy + 0.78,
        7.4,
        0.2,
        size=7.1,
        color=RED,
        line_spacing=1,
    )
    add_text(
        slide,
        "run_id ≠ job_id：job payload 只带 ID 与 generation_token，不带 profile。",
        ox + 0.22,
        oy + 1.12,
        7.0,
        0.18,
        size=6.8,
        color=TEXT_3,
        line_spacing=1,
    )
    add_notes(
        slide,
        "四种存储不要混用：PostgreSQL 是事实源，Redis 只保存瞬时任务，MinIO 是对象权威，Chroma 是可重建索引。"
        "最关键的隐私边界是 profile：只在私有前缀，下载 allowlist、MCP、Redis/Celery 和审计都不可见。",
    )


# ---------------------------------------------------------------------------
# Slide 15 - Security and audit
# ---------------------------------------------------------------------------


def slide_security(prs: Presentation, bg: Path) -> None:
    slide = content_slide(
        prs,
        "SECURITY & AUDIT  /  14",
        "默认拒绝，边界可验证",
        subtitle="身份、项目、数据、审计四层防线；生产环境不安全配置会直接 fail-fast。",
        page=15,
        dark=True,
        bg=bg,
    )
    layers = [
        ("01", "身份", "Argon2 密码 · 可撤销会话 · 5 类业务角色 + service。", TEAL_DARK),
        ("02", "隔离", "X-Project-Id · 全局 ∩ 项目角色 · 跨项目统一 404。", BLUE_DARK),
        ("03", "隐私", "profile 私有 key · artifact allowlist · 审计只记元数据。", VIOLET_DARK),
        ("04", "审计", "append-only 哈希链 · request-id · verify + CSV。", AMBER_DARK),
    ]
    for index, (number, title, detail, color) in enumerate(layers):
        y = 1.62 + index * 1.07
        add_rect(slide, M, y, 6.35, 0.92, fill="0D1A2A", line="25364A", radius=0.06, opacity=82)
        add_circle(slide, M + 0.2, y + 0.22, 0.38, fill=color, opacity=12)
        add_text(slide, number, M + 0.2, y + 0.22, 0.38, 0.38, size=7.2, color=color, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
        add_text(slide, title, M + 0.72, y + 0.18, 0.7, 0.24, size=11.5, color="F4F2EC", bold=True, line_spacing=1)
        add_text(slide, detail, M + 1.5, y + 0.2, 4.55, 0.5, size=7.8, color="A5B4C4", line_spacing=1.35)

    # Audit chain card.
    ax, ay, aw, ah = 7.55, 1.55, 5.05, 4.35
    add_rect(slide, ax, ay, aw, ah, fill="0C1827", line="263A4D", radius=0.07, opacity=88)
    add_text(slide, "AUDIT CHAIN", ax + 0.24, ay + 0.22, 1.6, 0.2, size=6.8, color=TEAL_DARK, bold=True, spacing=80, line_spacing=1)
    add_text(slide, "追加写，不可改。", ax + 0.24, ay + 0.5, 2.2, 0.28, size=15, color="F5F3EE", bold=True, line_spacing=1)
    block_w, block_gap = 1.27, 0.15
    for index in range(3):
        bx = ax + 0.24 + index * (block_w + block_gap)
        by = ay + 1.15
        add_rect(slide, bx, by, block_w, 1.08, fill="102235", line="2F4C62", radius=0.05)
        add_text(slide, f"event {index + 1:03d}", bx + 0.1, by + 0.1, 0.8, 0.16, size=5.6, color=TEAL_DARK, bold=True, line_spacing=1)
        add_text(slide, "prev_hash", bx + 0.1, by + 0.34, 0.75, 0.14, size=4.8, color="6D8499", line_spacing=1)
        add_text(slide, f"0x{['91a2', '4f7c', 'be31'][index]}…", bx + 0.1, by + 0.49, 0.8, 0.16, size=5.4, color="B8C7D4", font=FONT_MONO, line_spacing=1)
        add_text(slide, "hash", bx + 0.1, by + 0.72, 0.75, 0.14, size=4.8, color="6D8499", line_spacing=1)
        add_text(slide, f"0x{['7c1e', 'd3b0', '52a8'][index]}…", bx + 0.1, by + 0.87, 0.8, 0.16, size=5.4, color="E5EEF5", font=FONT_MONO, line_spacing=1)
        if index < 2:
            add_line(slide, bx + block_w + 0.02, by + 0.54, bx + block_w + block_gap - 0.02, by + 0.54, color=TEAL_DARK, width=1.0, arrow=True, opacity=60)
    add_rect(slide, ax + 0.24, ay + 2.55, aw - 0.48, 0.46, fill="08131F", line="243B4F", radius=0.05)
    add_text(
        slide,
        "sha256(prev_hash + canonical_json)",
        ax + 0.36,
        ay + 2.55,
        aw - 0.72,
        0.46,
        size=7.0,
        color=TEAL_DARK,
        font=FONT_MONO,
        anchor=MSO_ANCHOR.MIDDLE,
        line_spacing=1,
    )
    add_circle(slide, ax + aw - 0.96, ay + 3.18, 0.56, fill=GREEN, opacity=16)
    add_text(slide, "✓", ax + aw - 0.96, ay + 3.18, 0.56, 0.56, size=17, color=GREEN_DARK, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_text(slide, "verify 通过", ax + aw - 1.58, ay + 3.32, 0.58, 0.2, size=6.0, color=GREEN_DARK, bold=True, align=PP_ALIGN.RIGHT, line_spacing=1)
    add_text(slide, "链式结构示意 · actor / project / action / request_id", ax + 0.24, ay + 3.64, aw - 0.48, 0.18, size=5.8, color="6F8499", line_spacing=1)
    add_text(slide, "DB trigger 拒绝 UPDATE / DELETE / TRUNCATE", ax + 0.24, ay + 3.85, aw - 0.48, 0.2, size=6.2, color="8398AC", line_spacing=1)
    add_text(slide, "仅元数据：不记录密码、token、profile 与正文", ax + 0.24, ay + 4.04, aw - 0.48, 0.2, size=6.2, color="8398AC", line_spacing=1)

    # Production hardening strip.
    add_rect(slide, M, 5.98, SW - 2 * M, 0.65, fill="0C1827", line="263A4D", radius=0.06, opacity=85)
    items = ["TLS 1.2 / 1.3", "HSTS + CSP", "登录限流", "非 root + 只读根文件系统"]
    for index, item in enumerate(items):
        x = M + 0.2 + index * 3.0
        add_circle(slide, x, 6.22, 0.08, fill=TEAL_DARK)
        add_text(slide, item, x + 0.16, 6.13, 2.55, 0.2, size=7.2, color="C3CFDB", line_spacing=1)
    add_notes(
        slide,
        "安全不是一张登录页。四层分别是身份、项目隔离、数据隐私、审计。审计采用 SHA-256 链式哈希，数据库触发器禁止修改和删除；"
        "所有写操作都有显式事件或请求兜底。生产环境会拒绝关闭认证、开放 CORS、开发凭据和自动迁移等不安全配置。",
    )


# ---------------------------------------------------------------------------
# Slide 16 - Deployment and operations
# ---------------------------------------------------------------------------


def slide_deploy(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "DELIVERY & OPS  /  15",
        "单机 Compose 起步，按队列拆分负载",
        subtitle="只在 Nginx 暴露 80/443；API 与 worker 非 root、只读根文件系统；数据服务留在内网。",
        page=16,
    )

    # Topology panel.
    tx, ty, tw, th = M, 1.55, 7.55, 3.92
    add_rect(slide, tx, ty, tw, th, fill=CARD, line=LINE_L, radius=0.07)
    add_text(slide, "PRODUCTION TOPOLOGY", tx + 0.24, ty + 0.2, 2.2, 0.18, size=6.6, color=TEXT_3, bold=True, spacing=70, line_spacing=1)

    add_rect(slide, tx + 0.23, ty + 0.58, 0.95, 0.44, fill="F1F0EA", line="D9DEE5", radius=0.3)
    add_text(slide, "浏览器 / Electron", tx + 0.23, ty + 0.58, 0.95, 0.44, size=5.4, color=TEXT_2, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_line(slide, tx + 1.2, ty + 0.8, tx + 1.38, ty + 0.8, color=TEXT_3, width=1.0, arrow=True)

    edge_x, edge_y, edge_w, edge_h = tx + 1.4, ty + 0.5, 1.3, 0.62
    add_rect(slide, edge_x, edge_y, edge_w, edge_h, fill=INK, line=None, radius=0.05)
    add_text(slide, "Nginx", edge_x + 0.12, edge_y + 0.08, edge_w - 0.24, 0.2, size=8.0, color="F5F3EE", bold=True, line_spacing=1)
    add_text(slide, "80 / 443 · TLS", edge_x + 0.12, edge_y + 0.3, edge_w - 0.24, 0.18, size=5.6, color=TEAL_DARK, line_spacing=1)
    add_line(slide, edge_x + edge_w, ty + 0.8, edge_x + edge_w + 0.34, ty + 0.8, color=TEXT_3, width=1.0, arrow=True)

    app_x, app_y, app_w, app_h = edge_x + edge_w + 0.32, ty + 0.38, 2.5, 2.3
    add_rect(slide, app_x, app_y, app_w, app_h, fill="F8F8F5", line="DDE2E8", radius=0.05)
    add_text(slide, "应用与任务", app_x + 0.15, app_y + 0.12, 1.2, 0.2, size=6.4, color=TEXT_3, bold=True, spacing=50, line_spacing=1)
    app_boxes = [
        ("FastAPI", "认证 · ACL · run · 流式产物", TEAL),
        ("worker-parse", "MinerU / PyMuPDF · 抽取", AMBER),
        ("worker-bid", "章节生成 · 审查 · 组装", AMBER),
    ]
    for index, (name, detail, color) in enumerate(app_boxes):
        y = app_y + 0.44 + index * 0.56
        add_rect(slide, app_x + 0.14, y, app_w - 0.28, 0.46, fill=CARD, line=LINE_L, radius=0.05)
        add_circle(slide, app_x + 0.26, y + 0.17, 0.1, fill=color)
        add_text(slide, name, app_x + 0.44, y + 0.06, 1.25, 0.18, size=6.8, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, detail, app_x + 0.44, y + 0.24, 1.95, 0.16, size=5.5, color=TEXT_3, line_spacing=1)

    data_x, data_y, data_w, data_h = app_x + app_w + 0.27, ty + 0.5, 1.35, 2.06
    add_rect(slide, data_x, data_y, data_w, data_h, fill=INK, line=None, radius=0.05)
    add_text(slide, "数据服务", data_x + 0.13, data_y + 0.12, 1.1, 0.2, size=6.2, color="A9B7C7", bold=True, spacing=40, line_spacing=1)
    data_names = ["PostgreSQL", "Redis", "MinIO", "Chroma"]
    for index, name in enumerate(data_names):
        y = data_y + 0.44 + index * 0.37
        add_circle(slide, data_x + 0.16, y + 0.05, 0.08, fill=TEAL_DARK if index == 0 else "6E8298")
        add_text(slide, name, data_x + 0.26, y, data_w - 0.31, 0.18, size=5.7, color="DDE5EC", line_spacing=1)
    add_line(slide, app_x + app_w + 0.02, ty + 1.3, data_x - 0.02, ty + 1.3, color=TEAL_DARK, width=1.0, arrow=True, opacity=55)

    add_text(
        slide,
        "只有 Nginx 发布主机端口  ·  API / worker 非 root + 只读根文件系统  ·  migrate 成功后启动",
        tx + 0.24,
        ty + th - 0.48,
        tw - 0.48,
        0.22,
        size=6.2,
        color=TEXT_3,
        line_spacing=1,
    )

    # Right cards.
    cards = [
        ("发布", "多阶段 Dockerfile · migrate gate · CI 覆盖后端 / 前端 / 镜像 / 安全扫描", TEAL),
        ("可观测", "/api/live · /api/ready · /api/health；worker 健康检查与 degraded 状态", BLUE),
        ("恢复", "pg_dump + MinIO mirror + SHA256SUMS；restore.sh 与 14 天保留", VIOLET),
    ]
    for index, (title, detail, color) in enumerate(cards):
        x, y = 8.55, 1.55 + index * 1.31
        add_rect(slide, x, y, 4.05, 1.15, fill=CARD, line=LINE_L, radius=0.07)
        add_rect(slide, x, y, 0.06, 1.15, fill=color, line=None, radius=0.03)
        add_text(slide, title, x + 0.2, y + 0.16, 1.2, 0.24, size=10.5, color=TEXT, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.2, y + 0.48, 3.65, 0.5, size=8.0, color=TEXT_2, line_spacing=1.4)

    # Acceptance strip.
    add_rect(slide, M, 5.68, 5.9, 0.82, fill=CARD, line="CBE2D8", radius=0.06)
    add_circle(slide, M + 0.2, 5.96, 0.26, fill=GREEN, opacity=12)
    add_text(slide, "✓", M + 0.2, 5.96, 0.26, 0.26, size=8, color=GREEN, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_text(slide, "本地 Docker Desktop 验收", M + 0.58, 5.78, 2.7, 0.2, size=7.0, color=GREEN, bold=True, line_spacing=1)
    add_text(slide, "63/63 功能 · 27/27 备份恢复项", M + 0.58, 6.03, 3.8, 0.2, size=7.2, color=TEXT_2, line_spacing=1)
    add_text(slide, "这不是最终生产签字", M + 0.58, 6.23, 3.0, 0.16, size=5.8, color=TEXT_3, line_spacing=1)

    add_rect(slide, 6.8, 5.68, 5.8, 0.82, fill=CARD, line="E6D8BC", radius=0.06)
    add_circle(slide, 7.0, 5.96, 0.26, fill=AMBER, opacity=12)
    add_text(slide, "!", 7.0, 5.96, 0.26, 0.26, size=8, color=AMBER, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
    add_text(slide, "上线前仍需完成", 7.38, 5.78, 2.8, 0.2, size=7.0, color=AMBER, bold=True, line_spacing=1)
    add_text(slide, "干净 Linux VM · 真实 DNS/CA · 完整 LLM 正向链路", 7.38, 6.03, 4.9, 0.2, size=7.2, color=TEXT_2, line_spacing=1)
    add_text(slide, "以及真实数据演练与监控告警", 7.38, 6.23, 4.0, 0.16, size=5.8, color=TEXT_3, line_spacing=1)
    add_notes(
        slide,
        "部署基线是单机 Docker Compose：只有 Nginx 暴露 80/443，API 与两个 worker 在内部网络，parse 与 bid 队列拆开。"
        "生产特征包括迁移门、健康检查、备份恢复脚本和 CI。"
        "验收数据来自本地 Docker Desktop 演练，干净 Linux VM、真实 DNS/CA 和完整 LLM 正向链路仍未完成。",
    )


# ---------------------------------------------------------------------------
# Slide 17 - Roadmap
# ---------------------------------------------------------------------------


def slide_roadmap(prs: Presentation) -> None:
    slide = content_slide(
        prs,
        "ROADMAP  /  16",
        "从“能生成”，走向“可审批、可复用、可规模化”",
        subtitle="P0 已形成可上内网的安全基座；后续四个阶段围绕可信度、协作、交付与运维展开。",
        page=17,
    )
    phases = [
        (
            "P0",
            "可安全上内网",
            "已交付",
            GREEN,
            ["认证 / RBAC", "项目 ACL", "审计哈希链", "Compose / CI / 备份"],
            True,
        ),
        (
            "P1",
            "生成可信度",
            "下一步",
            AMBER,
            ["引用契约", "需求覆盖矩阵", "成本与参数溯源", "评测回归"],
            False,
        ),
        (
            "P2",
            "团队协作",
            "规划中",
            TEXT_3,
            ["审批流", "版本 diff / 恢复", "评论 / 指派", "SSE"],
            False,
        ),
        (
            "P3",
            "交付与复用",
            "规划中",
            TEXT_3,
            ["高保真 DOCX / PDF", "模板", "资质库", "多格式导入"],
            False,
        ),
        (
            "P4",
            "规模化运维",
            "持续",
            TEXT_3,
            ["监控告警", "Celery 可靠性", "性能 / 容灾", "E2E / 压测"],
            False,
        ),
    ]
    card_w = 2.22
    gap = 0.17
    for index, (phase, title, status, color, bullets, done) in enumerate(phases):
        x = M + index * (card_w + gap)
        y = 1.65
        fill = INK if done else CARD
        line = "2B4054" if done else LINE_L
        add_rect(slide, x, y, card_w, 3.2, fill=fill, line=line, radius=0.07)
        add_text(
            slide,
            phase,
            x + 0.18,
            y + 0.18,
            0.5,
            0.24,
            size=9,
            color=TEAL_DARK if done else color,
            bold=True,
            line_spacing=1,
        )
        add_pill(
            slide,
            status,
            x + card_w - 0.86,
            y + 0.16,
            0.68,
            0.22,
            fill=color,
            color="FFFFFF" if done else "FFFFFF",
            size=5.5,
        )
        add_text(
            slide,
            title,
            x + 0.18,
            y + 0.55,
            card_w - 0.36,
            0.55,
            size=13,
            color="F5F3EE" if done else TEXT,
            bold=True,
            line_spacing=1.25,
        )
        for bi, bullet in enumerate(bullets):
            by = y + 1.3 + bi * 0.38
            add_circle(slide, x + 0.2, by + 0.06, 0.07, fill=TEAL_DARK if done else color)
            add_text(
                slide,
                bullet,
                x + 0.35,
                by,
                card_w - 0.53,
                0.24,
                size=7.4,
                color="B7C5D3" if done else TEXT_2,
                line_spacing=1.2,
            )

    statements = [
        ("质量", "下一步不是更多 Agent，而是证据闭环：引用、需求覆盖与评测。", TEAL),
        ("协作", "核心是审批与版本：审批绑定内容哈希，修改即失效。", AMBER),
        ("交付", "长期价值来自高保真导出、模板、资质库与跨标书复用。", VIOLET),
    ]
    for index, (title, detail, color) in enumerate(statements):
        x = M + index * 4.03
        add_rect(slide, x, 5.28, 3.87, 1.25, fill=CARD, line=LINE_L, radius=0.07)
        add_circle(slide, x + 0.2, 5.48, 0.28, fill=color, opacity=12)
        add_text(slide, title, x + 0.2, 5.48, 0.7, 0.28, size=8.0, color=color, bold=True, anchor=MSO_ANCHOR.MIDDLE, line_spacing=1)
        add_text(slide, detail, x + 0.62, 5.45, 3.05, 0.62, size=8.4, color=TEXT_2, line_spacing=1.45)
    add_text(
        slide,
        "P0 已交付  ·  P1–P4 以本文件与代码为准  ·  不把“规划中”包装成“已实现”",
        M,
        6.72,
        SW - 2 * M,
        0.22,
        size=7.2,
        color=TEXT_3,
        align=PP_ALIGN.CENTER,
        line_spacing=1,
    )
    add_notes(
        slide,
        "路线图刻意区分已交付与规划中。P0 是安全与部署基座；P1 是生成可信度，重点是引用契约和需求覆盖；"
        "P2 是审批与版本；P3 是交付物与复用；P4 是规模化运维。三个判断是：质量靠证据闭环、协作靠审批版本、长期价值靠交付与复用。",
    )


# ---------------------------------------------------------------------------
# Slide 18 - Closing
# ---------------------------------------------------------------------------


def slide_closing(prs: Presentation, bg: Path) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_dark_background(slide, bg, overlay_opacity=0)
    add_kicker(slide, "CLOSING  /  17", y=0.55, dark=True)
    add_text(
        slide,
        "AI 提供产能，规则守住底线，\n人保留决定权。",
        M,
        1.18,
        8.2,
        1.9,
        size=34,
        color="F7F5EF",
        bold=True,
        line_spacing=1.3,
    )
    add_text(
        slide,
        "Much Agents 的目标，不是替代标书团队，而是把团队从重复劳动里解放出来，\n把时间还给判断、取舍与客户沟通。",
        M,
        3.4,
        8.0,
        0.9,
        size=12.5,
        color="AEBACA",
        line_spacing=1.55,
    )

    takeaways = [
        ("01", "产品", "一条可检查、可接管的生产线", TEAL_DARK),
        ("02", "架构", "确定性骨架包住受控 Agent", AMBER_DARK),
        ("03", "边界", "证据、权限、审计都可验证", VIOLET_DARK),
    ]
    for index, (number, title, detail, color) in enumerate(takeaways):
        x = M + index * 3.92
        y = 4.68
        add_rect(slide, x, y, 3.76, 1.16, fill="0D1A2A", line="2A3C51", radius=0.07, opacity=78)
        add_text(slide, number, x + 0.2, y + 0.17, 0.36, 0.2, size=7.0, color=color, bold=True, line_spacing=1)
        add_text(slide, title, x + 0.65, y + 0.14, 1.0, 0.24, size=8.0, color=color, bold=True, line_spacing=1)
        add_text(slide, detail, x + 0.2, y + 0.52, 3.3, 0.38, size=10.5, color="EDEFF1", bold=True, line_spacing=1.25)

    add_text(
        slide,
        "Much Agents  ·  多 Agent 标书编写系统",
        M,
        6.35,
        5.4,
        0.22,
        size=8.4,
        color="8FA2B5",
        bold=True,
        line_spacing=1,
    )
    source_box = add_text(
        slide,
        "github.com/lj105183000/much-agents",
        M,
        6.62,
        4.8,
        0.22,
        size=8.4,
        color=TEAL_DARK,
        bold=True,
        line_spacing=1,
    )
    source_run = source_box.text_frame.paragraphs[0].runs[0]
    source_run.hyperlink.address = "https://github.com/lj105183000/much-agents"
    add_text(
        slide,
        "AGPL-3.0-or-later  ·  数据截至 2026-10-25  ·  以仓库代码与文档为准",
        SW - M - 5.8,
        6.62,
        5.8,
        0.22,
        size=7.4,
        color="6F8298",
        align=PP_ALIGN.RIGHT,
        line_spacing=1,
    )
    add_text(
        slide,
        "18",
        SW - M - 0.7,
        7.15,
        0.7,
        0.25,
        size=8.5,
        color="5D6B7E",
        bold=True,
        align=PP_ALIGN.RIGHT,
        line_spacing=1,
    )
    add_notes(
        slide,
        "收尾回到三个词：产品、架构、边界。Much Agents 的价值不是无人值守，而是把产能、确定性规则和人的判断放在正确的位置。"
        "源码与许可证链接在页面上；所有能力边界以仓库当前代码和文档为准。",
    )


# ---------------------------------------------------------------------------
# Main (extended as slides are added)
# ---------------------------------------------------------------------------


def build() -> Path:
    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)
    prs.core_properties.title = "Much Agents 产品设计与技术架构"
    prs.core_properties.subject = "多 Agent 标书编写系统"
    prs.core_properties.author = "lj105183000"
    prs.core_properties.comments = "Generated by docs/presentation/build_deck.py"

    with tempfile.TemporaryDirectory() as tmp:
        bg = Path(tmp) / "dark-bg.png"
        make_dark_background(bg)

        slide_cover(prs, bg)
        slide_summary(prs)
        slide_problem(prs)
        slide_product_map(prs)
        slide_roles(prs)
        slide_journey(prs)
        slide_workbench(prs, bg)
        slide_generation(prs)
        slide_quality(prs)
        slide_figures(prs, bg)
        slide_architecture(prs)
        slide_parsing(prs)
        slide_agents(prs)
        slide_data(prs)
        slide_security(prs, bg)
        slide_deploy(prs)
        slide_roadmap(prs)
        slide_closing(prs, bg)

    prs.save(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(path)
