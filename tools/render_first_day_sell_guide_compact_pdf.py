from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT_DIR / "outputs" / "pdf" / "北交所新股首日卖出指南_简洁版_20260830.pdf"

NAVY = colors.HexColor("#0B2A4A")
BLUE = colors.HexColor("#2C77B8")
LIGHT_BLUE = colors.HexColor("#EAF3FB")
GREEN = colors.HexColor("#21835E")
LIGHT_GREEN = colors.HexColor("#EAF6F1")
ORANGE = colors.HexColor("#E88B00")
LIGHT_ORANGE = colors.HexColor("#FFF4DF")
RED = colors.HexColor("#D64232")
LIGHT_RED = colors.HexColor("#FFF0ED")
GRID = colors.HexColor("#C9D6E3")
ROW_ALT = colors.HexColor("#F4F7FA")
MUTED = colors.HexColor("#687787")
TEXT = colors.HexColor("#1D2A35")
WHITE = colors.white

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT = 15 * mm
RIGHT = 15 * mm
TOP = 14 * mm
BOTTOM = 16 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT - RIGHT


def _register_fonts() -> None:
    regular = Path("C:/Windows/Fonts/msyh.ttc")
    bold = Path("C:/Windows/Fonts/msyhbd.ttc")
    if not regular.exists() or not bold.exists():
        raise FileNotFoundError("Microsoft YaHei font files are required")
    pdfmetrics.registerFont(TTFont("MSYH", str(regular)))
    pdfmetrics.registerFont(TTFont("MSYH-Bold", str(bold)))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "Eyebrow",
            parent=base["Normal"],
            fontName="MSYH-Bold",
            fontSize=8.2,
            leading=10,
            textColor=BLUE,
            spaceAfter=4,
        ),
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName="MSYH-Bold",
            fontSize=23,
            leading=28,
            alignment=TA_LEFT,
            textColor=NAVY,
            spaceAfter=2,
        ),
        "page_title": ParagraphStyle(
            "PageTitle",
            parent=base["Heading1"],
            fontName="MSYH-Bold",
            fontSize=18.5,
            leading=23,
            textColor=NAVY,
            spaceAfter=5,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName="MSYH",
            fontSize=9.2,
            leading=12,
            textColor=MUTED,
            spaceAfter=8,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base["Heading2"],
            fontName="MSYH-Bold",
            fontSize=11.3,
            leading=14,
            textColor=BLUE,
            spaceBefore=6,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=7.8,
            leading=10.5,
            textColor=TEXT,
        ),
        "body_bold": ParagraphStyle(
            "BodyBold",
            parent=base["BodyText"],
            fontName="MSYH-Bold",
            fontSize=8.0,
            leading=10.5,
            textColor=NAVY,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=6.7,
            leading=8.7,
            textColor=MUTED,
        ),
        "table": ParagraphStyle(
            "TableBody",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=7.0,
            leading=8.8,
            textColor=TEXT,
        ),
        "table_small": ParagraphStyle(
            "TableSmall",
            parent=base["BodyText"],
            fontName="MSYH",
            fontSize=6.4,
            leading=8.0,
            textColor=TEXT,
        ),
        "table_head": ParagraphStyle(
            "TableHead",
            parent=base["Normal"],
            fontName="MSYH-Bold",
            fontSize=7.0,
            leading=8.5,
            alignment=TA_CENTER,
            textColor=WHITE,
        ),
        "stat": ParagraphStyle(
            "Stat",
            parent=base["Normal"],
            fontName="MSYH-Bold",
            fontSize=15,
            leading=18,
            alignment=TA_CENTER,
            textColor=NAVY,
        ),
        "stat_label": ParagraphStyle(
            "StatLabel",
            parent=base["Normal"],
            fontName="MSYH",
            fontSize=6.7,
            leading=8,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def _table(
    rows: list[list[Any]],
    widths: list[float],
    styles: dict[str, ParagraphStyle],
    *,
    small: bool = False,
    header_color: colors.Color = NAVY,
    paddings: tuple[float, float] = (3.0, 2.5),
) -> Table:
    body_style = styles["table_small"] if small else styles["table"]
    converted: list[list[Any]] = []
    for row_index, row in enumerate(rows):
        converted.append(
            [
                value
                if hasattr(value, "wrap")
                else _p(str(value), styles["table_head"] if row_index == 0 else body_style)
                for value in row
            ]
        )
    table = Table(converted, colWidths=widths, repeatRows=1, hAlign="LEFT")
    horizontal, vertical = paddings
    commands: list[tuple[Any, ...]] = [
        ("BACKGROUND", (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), horizontal),
        ("RIGHTPADDING", (0, 0), (-1, -1), horizontal),
        ("TOPPADDING", (0, 0), (-1, -1), vertical),
        ("BOTTOMPADDING", (0, 0), (-1, -1), vertical),
    ]
    for row_index in range(2, len(rows), 2):
        commands.append(("BACKGROUND", (0, row_index), (-1, row_index), ROW_ALT))
    table.setStyle(TableStyle(commands))
    return table


def _card(
    title: str,
    text: str,
    styles: dict[str, ParagraphStyle],
    *,
    width: float = CONTENT_WIDTH,
    kind: str = "blue",
    compact: bool = False,
) -> Table:
    palette = {
        "blue": (BLUE, LIGHT_BLUE),
        "green": (GREEN, LIGHT_GREEN),
        "orange": (ORANGE, LIGHT_ORANGE),
        "red": (RED, LIGHT_RED),
    }
    accent, background = palette[kind]
    body_style = styles["small"] if compact else styles["body"]
    contents = [
        _p(title, styles["body_bold"]),
        Spacer(1, 1.5),
        _p(text, body_style),
    ]
    table = Table([[contents]], colWidths=[width])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), 0.55, accent),
                ("LINEBEFORE", (0, 0), (0, -1), 3.5, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _two_cards(left: Table, right: Table) -> Table:
    gap = 5 * mm
    width = (CONTENT_WIDTH - gap) / 2
    table = Table([[left, "", right]], colWidths=[width, gap, width], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _stat_strip(styles: dict[str, ParagraphStyle]) -> Table:
    values = [
        ("53", "分钟线样本"),
        ("11", "近期普通样本"),
        ("4 / 4", "9:35高换手后涨10%"),
        ("11:30", "普通样本清仓"),
    ]
    cells = [[[_p(value, styles["stat"]), _p(label, styles["stat_label"])] for value, label in values]]
    table = Table(cells, colWidths=[CONTENT_WIDTH / 4] * 4, rowHeights=[42])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), ROW_ALT),
                ("GRID", (0, 0), (-1, -1), 0.4, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def _footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, PAGE_HEIGHT - 7 * mm, PAGE_WIDTH, 7 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(BLUE)
    canvas.setLineWidth(0.5)
    canvas.line(LEFT, 11.5 * mm, PAGE_WIDTH - RIGHT, 11.5 * mm)
    canvas.setFont("MSYH", 6.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(LEFT, 6.7 * mm, "北交所新股首日卖出指南 | 简洁版 | 2026-08-30")
    canvas.drawRightString(PAGE_WIDTH - RIGHT, 6.7 * mm, f"{doc.page} / 3")
    canvas.restoreState()


def _page_one(styles: dict[str, ParagraphStyle]) -> list[Any]:
    decision_rows = [
        ["时点", "价格/路径状态", "换手提示", "动作"],
        ["盘前", "估值双线、获配手数、外力是否盘前可复核", "不设阈值", "预设常态仓与事件尾仓；估值只定预期"],
        ["开盘", "明显越过估值边界", "不设阈值", "可分仓先兑现约30%；1手默认看到9:35"],
        ["9:35", "同时低于开盘价和累计VWAP", "任何换手都不能覆盖双低", "卖出剩余仓位"],
        ["9:35", "非双低，仍守开盘价或双高", "累计约26%以上标记高热度", "等下一节点；双高进入移动兑现"],
        ["9:45", "前序仓位仍在，弱势未修复", "累计约37%以上才算强确认", "低位反抽卖；高热度也只等下一节点"],
        ["10:00", "双低/双高/中性重新判断", "累计约48%以上，信号开始衰减", "双低卖；双高最多看到11:00"],
        ["10:30", "观察是否仍在扩张", "改看10:00-10:30新增约11%以上", "仅弱确认，不因累计高换手续命"],
        ["11:00", "未创新高，或跌回开盘价/VWAP下方", "新增约6.5%以上且突破才算接力", "未突破卖；接力票也只看到11:30"],
        ["11:30", "普通样本", "累计高换手更像充分交换", "原则清仓；不以换手高为留仓理由"],
    ]
    return [
        _p("操作手册 / v3 简洁版", styles["eyebrow"]),
        _p("北交所新股首日卖出指南", styles["title"]),
        _p("价格定卖点，换手定信心，估值定预期，外力定例外", styles["subtitle"]),
        _stat_strip(styles),
        Spacer(1, 8),
        _card(
            "先记住这一句",
            "常态票双低早卖；早盘累计换手确认热度，10:30后新增换手确认接力；11:30原则清仓。换手不能覆盖价格卖点，外力只给小尾仓。",
            styles,
        ),
        _p("首日决策路径", styles["section"]),
        _table(decision_rows, [36, 155, 132, CONTENT_WIDTH - 323], styles, small=True),
        Spacer(1, 8),
        _two_cards(
            _card(
                "硬退出",
                "双低、11:00未突破或转弱、11:30到点。三类信号优先级高于估值、累计换手和后悔心理。",
                styles,
                width=(CONTENT_WIDTH - 5 * mm) / 2,
                kind="blue",
                compact=True,
            ),
            _card(
                "允许等待",
                "非双低且早盘累计换手进高档，或11:00出现新增放量并突破。等待只到下一节点，不自动延长到下午。",
                styles,
                width=(CONTENT_WIDTH - 5 * mm) / 2,
                kind="green",
                compact=True,
            ),
        ),
    ]


def _page_two(styles: dict[str, ParagraphStyle]) -> list[Any]:
    state_rows = [
        ["盘面状态", "定义", "换手解释", "常态动作"],
        ["双低", "现价低于开盘价且低于累计VWAP", "高换手偏筹码松动，不能解释为承接", "卖出"],
        ["一高一低", "只守住一条参考线", "早盘进高档可标记高热度，但仍是中性", "只等下一节点"],
        ["双高", "现价高于开盘价且高于累计VWAP", "早盘高换手确认热度；晚段看新增换手", "观察并移动兑现"],
    ]
    turnover_rows = [
        ["节点", "累计高档", "新增高档", "样本含义"],
        ["9:35", "25.8%", "同累计", "价格幸存者：高档4/4后涨10%以上，中位39.8%；非高档0/4，中位3.7%"],
        ["9:45", "36.9%", "10.7%", "高档4/5后涨10%以上，中位39.7%；非高档0/3，中位1.1%"],
        ["10:00", "48.0%", "10.9%", "仍可确认热度，但旧样本不单调，累计信号开始衰减"],
        ["10:30", "59.1%", "10.8%", "累计值已饱和；本段新增高档也只算弱确认"],
        ["11:00", "67.1%", "6.5%", "只认新增高档加价格突破；华大海天随后再涨29.3%"],
        ["11:30", "71.3%", "5.0%", "近期累计高档组下午中位仅0.2%，0/4再涨10%"],
    ]
    regret_rows = [
        ["样本", "早卖", "主后悔中位", "10%以上大后悔", "解释"],
        ["旧样本", "18/40", "-3.5%", "4", "多数早卖有利，少数错过很大"],
        ["近期普通", "3/11", "-4.9%", "0", "更支持双低早卖"],
        ["近期外力", "1/2", "+77.6%", "1", "只能盘前单列，不能外推"],
    ]
    huada_rows = [
        ["时点", "价/状态", "换手", "系统动作"],
        ["9:35", "26.18；守开盘、低于VWAP", "累计28.5%：高档", "一高一低，继续观察"],
        ["9:45", "26.01；仍守开盘", "累计45.9%：高档", "不误卖"],
        ["10:00", "25.45；仍非双低", "累计56.9%", "等待价格修复"],
        ["11:00", "29.77；双高并突破", "本段新增12.1%", "确认重新放量接力"],
        ["11:30", "34.39；高点回撤", "累计87.2%", "按常态规则清仓"],
    ]
    return [
        _p("判定卡 01", styles["eyebrow"]),
        _p("量价、换手、估值与后悔", styles["page_title"]),
        _p("先看价格位置，再用换手判断热度是否还活着；估值和后悔只负责解释，不负责推翻卖点。", styles["subtitle"]),
        _p("1. 价格与VWAP的三状态", styles["section"]),
        _table(state_rows, [62, 150, 172, CONTENT_WIDTH - 384], styles, small=True),
        _p("2. 节点换手时钟", styles["section"]),
        _table(turnover_rows, [38, 56, 56, CONTENT_WIDTH - 150], styles, small=True, header_color=BLUE),
        Spacer(1, 5),
        _card(
            "心理估值的服从顺序",
            "双低时，即使价格低于估值下沿/中枢也卖；已高于估值上沿且转弱，兑现置信度更高；只有盘前定义且仍持续的事件外力，允许保留不超过30%的尾仓。",
            styles,
            kind="orange",
            compact=True,
        ),
        _p("3. 路径后悔：只评价真实持仓", styles["section"]),
        _table(regret_rows, [68, 48, 70, 76, CONTENT_WIDTH - 262], styles, small=True),
        _p("4. 华大海天：价格路径与换手时钟同向", styles["section"]),
        _table(huada_rows, [42, 145, 105, CONTENT_WIDTH - 292], styles, small=True, header_color=BLUE),
        Spacer(1, 5),
        _card(
            "资金与力竭组合",
            "+50%是软锚，不是硬底；约5亿元是近期晚拉到峰值前的全市场累计成交量级，不是固定操盘预算。累计成交4-6亿元、累计换手60%-85%、短时急拉创新高后1-3分钟回撤5%-10%时，按标价完成和买盘撤退处理，优先兑现。",
            styles,
            kind="red",
            compact=True,
        ),
    ]


def _page_three(styles: dict[str, ParagraphStyle]) -> list[Any]:
    checklist_rows = [
        ["时段", "检查", "动作"],
        ["盘前", "估值双线、获配手数、外力是否可复核", "预设常态仓和事件尾仓；不盘中补外力标签"],
        ["开盘", "价格是否明显越过估值边界", "可分仓先兑现约30%；1手默认看到9:35"],
        ["9:35", "开盘价、累计VWAP、累计换手是否达26%", "双低卖；非双低高热度等下一节点；双高移动兑现"],
        ["9:45", "前序仓位是否仍在；累计换手是否达37%", "弱势反抽卖；高换手只增强等待信心"],
        ["10:00", "是否双低/双高；累计换手是否达48%", "双低卖；双高最多看到11:00；中性只给一个节点"],
        ["10:30", "10:00后新增换手是否达11%；价格是否扩张", "累计换手不再续命；新增放量仅弱确认"],
        ["11:00", "是否突破早盘高点；本段新增是否达6.5%", "未突破或转弱即卖；突破加新增放量才继续"],
        ["11:30", "是否属于盘前外力分支", "普通样本清仓；累计高换手不构成留仓理由"],
        ["13:30-14:30", "外力是否持续；是否突破上午高点", "仅事件尾仓逐节点兑现；14:30原则清仓"],
        ["收盘", "换手是否接近90%；是否跌破VWAP", "原则不隔夜；外力也不给隔夜通行证"],
    ]
    return [
        _p("执行清单", styles["eyebrow"]),
        _p("从盘前到收盘，只做真实可执行动作", styles["page_title"]),
        _p("先判断仓位是否仍在，再评估当前节点；已经卖出的票只进入后悔统计，不重复制造卖点。", styles["subtitle"]),
        _table(checklist_rows, [74, 205, CONTENT_WIDTH - 279], styles, small=True),
        _p("一手与多手的执行差异", styles["section"]),
        _two_cards(
            _card(
                "只有1手",
                "默认观察到9:35，再按双低/中性/双高执行。无法承受约8%早盘回撤时，可风险优先在开盘卖出，但要接受错过主拉的可能。",
                styles,
                width=(CONTENT_WIDTH - 5 * mm) / 2,
                compact=True,
            ),
            _card(
                "2手及以上",
                "估值明显越界时可在开盘兑现约30%；剩余仓位必须逐节点执行。已卖部分不再被后续换手或价格重新召回。",
                styles,
                width=(CONTENT_WIDTH - 5 * mm) / 2,
                kind="green",
                compact=True,
            ),
        ),
        Spacer(1, 8),
        _card(
            "外力分支",
            "维琪科技说明外力可能推翻普通样本的早卖后悔分布；双英集团说明外力不保证下午还有第二段。外力必须盘前定义，上午仍高于开盘价和VWAP才保留小尾仓，14:30原则清仓。",
            styles,
            kind="orange",
            compact=True,
        ),
        Spacer(1, 8),
        _card(
            "最后的纪律",
            "先看是否仍持仓，再看当前节点；先看双低/双高，再看换手时钟；先处理常态路径，再处理外力例外。任何等待都只到下一个节点，不等于持有到收盘。",
            styles,
            kind="red",
        ),
        Spacer(1, 10),
        _p(
            "数据基础：截至2026-08-29的53只北交所新股首日分钟线；旧样本40只、近期普通11只、外力观察2只。节点价使用分钟收盘，换手阈值为旧样本节点67%分位。本文用于打新获配后的首日卖出观察，不构成投资建议，也不是自动下单规则。",
            styles["small"],
        ),
    ]


def build_pdf(output_path: Path) -> None:
    _register_fonts()
    styles = _styles()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=LEFT,
        rightMargin=RIGHT,
        topMargin=TOP,
        bottomMargin=BOTTOM,
        title="北交所新股首日卖出指南 - 简洁版",
        author="BJ_IPO research",
        subject="北交所打新获配后的上市首日卖出观察",
    )
    story: list[Any] = []
    story.extend(_page_one(styles))
    story.append(PageBreak())
    story.extend(_page_two(styles))
    story.append(PageBreak())
    story.extend(_page_three(styles))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成北交所新股首日卖出指南三页简洁版PDF")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output)
    build_pdf(output)
    print(output)


if __name__ == "__main__":
    main()
