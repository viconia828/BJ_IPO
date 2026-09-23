from __future__ import annotations

import argparse
import json
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
DEFAULT_OUTPUT = ROOT_DIR / "outputs" / "pdf" / "北交所新股首日卖出指南_简洁版_v5_2_20260923.pdf"
VERSION_DATE = "2026-09-23"
PAGE_COUNT = 4
AUDIT_PATH = ROOT_DIR / "outputs/guide_v5_audit_20260923/audit.json"
AUDIT = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
assert AUDIT["n"] == 60 and AUDIT["as_of"] == VERSION_DATE
REPAIR = json.loads((ROOT_DIR / "outputs/high_turnover_double_low_20260923/analysis.json").read_text(encoding="utf-8"))
assert REPAIR["as_of"] == VERSION_DATE
AUCTION = json.loads((ROOT_DIR / "outputs/opening_auction_wait_20260923/analysis.json").read_text(encoding="utf-8"))
assert AUCTION["groups"]["verified_auction"]["summary"]["n"] == 60

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
            fontSize=8.3,
            leading=11.5,
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
            fontSize=7.1,
            leading=9.5,
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
            fontSize=7.3,
            leading=10.0,
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
        ("60", "全部统计已重算"),
        ("40+18+2", "旧样本 / 近期普通 / 外力"),
        ("第3页", "换手与临停研究"),
        ("13:10", "仍持尾仓的午后确认"),
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
    canvas.drawString(LEFT, 6.7 * mm, f"北交所新股首日卖出指南 | v5.2 简洁版 | {VERSION_DATE}")
    canvas.drawRightString(PAGE_WIDTH - RIGHT, 6.7 * mm, f"{doc.page} / {PAGE_COUNT}")
    canvas.restoreState()


def _page_one(styles: dict[str, ParagraphStyle]) -> list[Any]:
    t = AUDIT["thresholds"]
    cum = lambda n: f"{t[n]['all60']['cum']:.1f}%"
    decision_rows = [
        ["时点", "价格 / 路径状态", "换手提示", "默认动作"],
        ["盘前", "估值区间、可卖股数、可承受回撤", "阈值只作观察", "选定保守路径；需要尾仓时预先定比例与退出点"],
        ["开盘", "盘前定价及真实竞价换手", "一手仓位参考下表分档", "选择开盘后兑现或等9:35；多手可分批"],
        ["9:35", "同时低于开盘价和累计VWAP", "≥30%高换手另列修复观察", "默认退出；预设少量观察仓的例外见第2页"],
        ["9:35-9:45", "非双低，仍守一条或两条参考线", f"高档参考：{cum('09:35')} / {cum('09:45')}", "等下一节点；转双低退出，双高逐步兑现"],
        ["10:00", "再判双低 / 中性 / 双高", f"累计高档参考{cum('10:00')}", "双低退出；其余只继续到下一观察点"],
        ["10:30", "是否推进、修复或准备冲停", "累计65%-70%仅观察资格", "冲停前看累计；冲停后看价格对新增量的反应"],
        ["11:00", "10点后未超越早盘高点，或跌破任一参考线", "10:30-11:00新增高档约6.7个百分点", "保守路径退出；突破且双高才继续观察"],
        ["11:30", "仍在的仓位是否双高、近期是否持续修复", "累计高档参考73.8%；不能单独留仓", "默认兑现；预设尾仓至多30%，午后必须再确认"],
        ["13:05-13:10", "仍持尾仓：冲停失败后有无量价修复", "参见第3页：新增2个百分点仅探索值", "无修复退出；有推进只等下一节点"],
    ]
    auction_rows = [["竞价换手", "样本", "等待后价更高*", "价差中位*", "一手仓位参考"]]
    actions = ["不能仅因竞价低就判弱", "偏弱，优先考虑开盘后兑现", "中性，结合其他盘前信息", "可考虑等到9:35", "可考虑等到9:35，仍需防回撤"]
    for (label, group), action in zip(AUCTION["groups"]["verified_auction"]["bands"].items(), actions):
        outcome=group["09:35"]
        auction_rows.append([label.replace("<","&lt;").replace(">=","≥"), str(group['n']), f"{outcome['positive_n']}/{group['n']}", f"{outcome['reference_return']['median']:+.2f}%", action])
    return [
        _p("操作手册 / v5.2 简洁版", styles["eyebrow"]),
        _p("北交所新股首日卖出指南", styles["title"]),
        _p("价格决定兑现，累计换手看准备，新增量价决定是否继续等", styles["subtitle"]),
        _stat_strip(styles), Spacer(1, 8),
        _card("先记住这一句", "双低先兑现；异常高换手可单列一个节点的修复观察，修复双高才继续等。11:30以兑现为默认，小尾仓仍须靠午后量价确认。", styles),
        _p("首日决策路径", styles["section"]),
        _table(decision_rows, [51, 154, 139, CONTENT_WIDTH - 344], styles, small=True),
        Spacer(1, 7),
        _two_cards(
            _card("世纪值得单列", "9:35换手34.36%，超过此前58只的95%分位；价格虽双低，9:39已首次修复双高。异常成交与价格修复应分两步观察，不能只记为旧规则漏掉。", styles, width=(CONTENT_WIDTH-5*mm)/2, compact=True),
            _card("尾仓是风险选择", "仅评估仍持有的可卖股份。至多30%及默认11:30兑现是风险管理约定，未经最优回测；不能为了抓单个大涨样本倒改早卖记录。", styles, width=(CONTENT_WIDTH-5*mm)/2, kind="green", compact=True),
        ),
        Spacer(1, 8),
        _p("只有一手：开盘先看独立竞价换手", styles["section"]),
        _table(auction_rows, [67, 40, 95, 78, CONTENT_WIDTH-280], styles, small=True, header_color=BLUE),
        Spacer(1, 4),
        _p("* 相对开盘价，等到9:35的参考价差；彩客当时临停，顺延至9:42复牌首分钟开价。未计费用与滑点。≥5%合计11/14等待更高、中位+6.78%，其中3/14等待中曾跌破开盘价5%；低于3%并非更弱，不能设成“低于5%全卖”。", styles["small"]),
        Spacer(1, 4),
        _p("竞价量取自60只上市日iFinD独立09:25快照，均核对成交价与量额。世纪真实竞价换手7.48%，凯达8.49%；世纪原09:30首条11.43%含连续成交。最终竞价量在撮合后才确定，之后决定卖出不能保证仍按竞价开盘价成交。", styles["small"]),
    ]


def _page_two(styles: dict[str, ParagraphStyle]) -> list[Any]:
    state_rows = [
        ["状态", "定义", "换手解释与动作"],
        ["双低", "现价低于开盘价且低于累计VWAP", "默认退出；9:35异常高换手另见下方观察分支"],
        ["中性", "只守住一条参考线或处于边界", "换手只确认热度；等待下个节点的价格修复"],
        ["双高", "现价高于开盘价且高于累计VWAP", "允许观察与分批兑现；不等于能持有到收盘"],
    ]
    meanings = ["开盘热度；包含集合竞价", "观察早盘换手是否延续", "开始看价格能否突破", "不是65%-70%的冲停观察线", "新增换手必须结合价格推进", "午后是否续涨，仍须单独确认"]
    turnover_rows = [["节点", "累计高档", "区间新增高档", "如何理解"]]
    for (node, cut), meaning in zip(AUDIT["thresholds"].items(), meanings):
        x = cut["all60"]
        turnover_rows.append([node, f"{x['cum']:.1f}%", "同累计" if node == "09:35" else f"{x['delta']:.1f}个百分点", meaning])
    groups = AUDIT["summaries"]
    regret_rows = [["原价规则回放", "9:35/9:45早卖", "延后卖出价差中位", "价差≥10%"]]
    for key, label in [("old", "旧40只"), ("recent_normal", "近期普通18只")]:
        g = groups[key]
        regret_rows.append([label, f"{g['early_n']}/{g['n']}", f"{g['regret']['median']:+.1f}%", f"{g['regret_ge10']}/{g['regret_comparable_n']}"])
    regret_rows.append(["原外力标签2只", "1/2", "延后节点临停，剔除", "不计算"])
    checks = AUDIT["recent_frozen_cutoff_checks"]
    counts = lambda n,k: f"{checks[n][k]['peak_ge10']}/{checks[n][k]['n']}"
    repair_rows = [["9:35双低且换手≥30%", "9:35换手", "9:45较9:35", "9:45双高"]]
    for r in REPAIR["rows"]:
        if r["turnover_0935"] >= 30:
            repair_rows.append([r["name"], f"{r['turnover_0935']:.2f}%", f"{r['return_0945']:+.2f}%", "是" if r["double_high_0945"] else "否"])
    return [
        _p("判定卡 01 / 新增异常换手观察", styles["eyebrow"]),
        _p("量价、换手与早卖的代价", styles["page_title"]),
        _p("价格状态与换手数值分开看；样本中出现过的机会，不等于真实可兑现的收益。", styles["subtitle"]),
        _p("1. 价格与VWAP的三状态", styles["section"]),
        _table(state_rows, [44, 208, CONTENT_WIDTH - 252], styles, small=True),
        _p("2. 节点换手时钟：最新60只的上三分之一分界", styles["section"]),
        _table(turnover_rows, [43, 68, 110, CONTENT_WIDTH - 221], styles, small=True, header_color=BLUE),
        Spacer(1, 4),
        _p("按2/3分位计算，均为当前样本描述。新增＝本节点累计减前一节点累计，区间长短不同，不是每分钟速度；9:35包含集合竞价。累计换手可反复计同一股份，不能解释为独立持有人已换完一遍。", styles["small"]),
        _p("3. 早盘高换手有参考价值，不保证持续上涨", styles["section"]),
        _card("用旧40只门槛观察后续18只普通样本", f"仅统计按原价规则仍持仓的样本：9:35高档（≥25.8%）随后至午间再涨10%的为{counts('09:35','high')}，非高档{counts('09:35','not_high')}；9:45高档（≥36.9%）为{counts('09:45','high')}，非高档{counts('09:45','not_high')}。凯达早盘也属于高档，说明热度不能保证午后持续。", styles, kind="green", compact=True),
        Spacer(1, 4),
        _p("这里沿用旧40只门槛，与上表60只分位数不同；结果为节点后最高价涨幅，未扣成本、不代表可成交收益。11:30若假设仍持有全部近期普通样本，旧高档组下午再涨10%为1/8，中位最大涨幅0.9%，唯一大涨例为世纪。", styles["small"]),
        _p("4. 早卖仍有防守价值，已不再是“近期零大后悔”", styles["section"]),
        _table(regret_rows, [98, 104, 172, CONTENT_WIDTH - 374], styles, small=True),
        Spacer(1, 4),
        _p("延后价差比较早卖分钟价与推迟至10:00及以后首次触发原卖出规则的分钟价，负值表示早卖更有利。维琪的延后节点处于临停，旧版+77.6%依赖停前价，本版剔除。以上不回测本版尾仓方案。", styles["small"]),
        _p("5. 高换手但双低：单列一个节点的修复观察", styles["section"]),
        _table(repair_rows, [163, 106, 116, CONTENT_WIDTH-385], styles, small=True, header_color=BLUE),
        Spacer(1, 5),
        _card("待验证分支：先兑现主仓，少量观察到9:45", "9:35双低且累计换手约30%以上，可按预设方案留至多30%观察仓；9:45仍未双高就兑现，修复双高才继续至下一节点。世纪9:35至9:45再增换手16.48个百分点，9:39首次双高，9:45仍双高。", styles, kind="orange", compact=True),
        Spacer(1, 4),
        _p("30%接近旧40只80%分位29.56%，是探索线。历史仅科马、维琪2例，1例修复；世纪是新增第3例。若放宽到旧高档25.76%，5例仅2例修复，爱得、金戈仍跌。超过旧90%分位33.08%的双低票仅世纪，暂无历史同类验证；仓位比例属风险约定，不是最优回测。", styles["small"]),
    ]


def _page_turnover_halt(styles: dict[str, ParagraphStyle]) -> list[Any]:
    # Keep the turnover/halt study on one self-contained page.
    s = dict(styles)
    s["body"] = ParagraphStyle("HaltBody", parent=styles["body"], fontSize=8.5, leading=12)
    s["body_bold"] = ParagraphStyle("HaltBold", parent=styles["body_bold"], fontSize=8.5, leading=12)
    s["small"] = ParagraphStyle("HaltSmall", parent=styles["small"], fontSize=7.2, leading=10)
    s["table"] = ParagraphStyle("HaltTable", parent=styles["table"], fontSize=8, leading=11)
    s["table_head"] = ParagraphStyle("HaltHead", parent=styles["table_head"], fontSize=7.6, leading=10)
    stage_rows = [
        ["观察节点", "参考值 / 状态", "如何使用"],
        ["10:30准备", "累计换手约65%-70%以上", "仅进入观察范围；不因达到70%就等临停或留仓"],
        ["拉停之前", "低于60%提高警惕；80%以上为加分项", "看触停前累计量，不能把停后回落的成交当准备量"],
        ["复牌之后", "新增成交能否推高价格、修复回撤", "缩量抬价不直接判弱；放量走低不解释为承接强"],
    ]
    sample_rows = [
        ["股票", "10:30换手", "二停前换手*", "收盘较二停价"],
        ["振宏股份", "68.66%", "86.79%", "+30.58%"],
        ["维琪科技", "80.14%", "89.81%", "+115.24%"],
        ["世纪数码", "71.55%", "87.95%", "+35.47%"],
        ["千岸科技", "68.95%", "55.91%", "-27.46%"],
        ["双英集团", "72.62%", "43.39%", "-25.92%"],
    ]
    afternoon_rows = [
        ["13:10观察", "午后新增换手", "较午间价格", "盘面解释"],
        ["世纪数码", "5.59个百分点", "+17.90%", "上午未接近一停；午后量价同步突破"],
        ["凯达重工", "1.06个百分点", "-4.66%", "上午冲停失败；午后继续走弱"],
        ["振宏股份", "2.29个百分点", "+2.57%", "上午也曾失败，但午后量价修复"],
    ]
    return [
        _p("判定卡 02 / 换手与临停研究", s["eyebrow"]),
        _p("换手与临停：先看准备，再看推进", s["page_title"]),
        _p("本页只评估仍持有的可卖尾仓；历史结果不追认已卖仓位，也不作为重新买入信号。", s["subtitle"]),
        _card("核心区别", "先充分换手再拉停，与先拉停、回落时才放量不同。累计换手看准备，新增量价看推进；有冲高机会不等于适合拿到收盘。", s),
        _p("1. 三个观察节点", s["section"]),
        _table(stage_rows, [66, 176, CONTENT_WIDTH - 242], s, paddings=(5, 4)),
        _p("2. 二停前换手，比固定10:30更有区分度", s["section"]),
        _table(sample_rows, [100, 104, 126, CONTENT_WIDTH - 330], s, paddings=(5, 3.5)),
        Spacer(1, 4),
        _p("* 不含触停所在分钟；二停按开盘价+60%识别。振宏10:30不足70%仍成功；双英09:51已二停，不能把10:30的72.62%当作拉停前换手。", s["small"]),
        _p("3. 上午冲停失败，午后再做一次确认", s["section"]),
        _card("13:10观察条件：上午已失败 + 午后无修复", "上午曾到开盘价+27%，至少10个交易分钟仍未到+30%，且从高点回撤至少5%；到13:10仍未临停，午后新增换手不足2个百分点、现价不高于午间收价：降低继续等临停的意愿，作为减仓或退出观察。", s, kind="orange"),
        Spacer(1, 5),
        _table(afternoon_rows, [72, 100, 90, CONTENT_WIDTH - 262], s, header_color=BLUE, paddings=(5, 3.5)),
        Spacer(1, 5),
        _p("一次失败不等于全天失败：振宏午后出现修复。上述13:10条件仅匹配历史族兴、创达2例，之后至收盘分别-6.02%、+0.33%；凯达为-12.47%。", s["small"]),
        Spacer(1, 6),
        _card("只等下一节点，不把首日强势外推为隔夜理由", "世纪14:00后半小时仅新增换手1.72个百分点，价格仍涨21.75%；高累计后的缩量要结合价格看。世纪、振宏、维琪次日均较首日收盘跌约30%。", s, kind="red", compact=True),
        Spacer(1, 7),
        _p("证据边界：60只分钟样本（58只历史+最新2只）；充分观察的二停样本仅5例，低换手2例回落、高换手3例大涨。嘉晨14:56触及二停，时间不足单列；二停前60%-80%无可评估样本，早晚拉停与换手高低混杂。以上为观察区间，非独立验证的门槛或未来胜率。换手分母经本地日线或回放校准，临停据分钟价格及成交空档推断。", s["small"]),
    ]


def _page_four(styles: dict[str, ParagraphStyle]) -> list[Any]:
    groups = AUDIT["summaries"]
    recent = groups["recent_normal"]
    all_s = AUDIT["all_summary"]
    next_s = AUDIT["high90_summary"]["next_close"]
    evidence_rows = [["样本", "午后超上午高点≥1%", "次日收盘相对首日"]]
    for key, name in [("old", "旧40只"), ("recent_normal", "近期普通18只"), ("recent_external", "原外力标签2只")]:
        g = groups[key]
        evidence_rows.append([name, f"{g['afternoon_high_1pct']}/{g['n']}", f"中位{g['next_close']['median']:+.1f}%（有数据{g['next_close']['n']}只）"])
    checklist = [
        ["检查节点", "实际执行"],
        ["盘前", "只管理当日可卖股份；估值用于设预期，事件标签必须事前记录。"],
        ["9:35-9:45", "双低默认退出；若换手约30%以上且预设观察仓，只给到9:45，未修复双高则兑现。"],
        ["上午后续节点", "仓位还在才继续判断。修复后也只等下一节点；双高看新增量与突破，不自动延长至下午。"],
        ["11:30", "以兑现为默认；若盘前选尾仓方案，需仍双高、近期有修复，比例至多30%。高累计本身不够。"],
        ["13:05-13:10", "先看量价是否同步修复。上午冲停失败且仍无修复，按第3页条件降低继续等的意愿。"],
        ["复牌及午后", "只等下一节点；放量却走低、突破后迅速回撤，优先减仓。缩量抬价不单独判弱。"],
        ["14:30及收盘前", "保守计划在14:30兑现余仓；原则不隔夜。强势或外力标签均不豁免。"],
    ]
    return [
        _p("执行清单 / 结论校正", styles["eyebrow"]),
        _p("默认兑现，少量等待，承认取舍", styles["page_title"]),
        _p("尾仓的作用是保留少量观察空间；不是把每只强势票都拿到收盘。", styles["subtitle"]),
        _table(checklist, [82, CONTENT_WIDTH - 82], styles, small=True),
        _p("1. 下午少见大机会，不等于没有", styles["section"]),
        _table(evidence_rows, [112, 156, CONTENT_WIDTH - 268], styles, small=True, header_color=BLUE),
        Spacer(1, 4),
        _p(f"近期普通18只仅世纪满足午后创新高定义；保守退出仍有依据，但“普通票下午无机会”应删除。全样本次日有数据{all_s['next_close']['n']}只，中位{all_s['next_close']['median']:+.1f}%；首日换手≥90%的{next_s['n']}只有次日数据，中位{next_s['median']:+.1f}%。这是相关描述，不是高换手造成下跌的证明。", styles["small"]),
        _p("2. 金额与估值：删除不能证实的硬锚", styles["section"]),
        _card("没有统一的5亿元见顶线，也没有发行价+50%的保底", f"近期普通8次大幅晚拉，峰值累计成交额中位{recent['large_late_peak_amount']['median']:.2f}亿元，中间50%为{recent['large_late_peak_amount']['p25']:.2f}-{recent['large_late_peak_amount']['p75']:.2f}亿元，只有{recent['large_late_4to6']}/8落在4-6亿元。全60只中{all_s['close_below_issue50']}只收盘低于发行价+50%。金额只是成交量级，不能推算资金余额、主力意图或保底价。", styles, kind="orange", compact=True),
        Spacer(1, 4),
        _p("大幅晚拉沿用原复盘口径：事后最大低点至后续高点涨幅≥20%，跨至少两根分钟线，峰值不早于9:40。8例中5例峰值后3分钟内收价回撤≥10%，提示兑现风险；不能在盘中预知哪个高点是最终峰值。", styles["small"]),
        _p("3. 一手与多手：按可卖数量执行", styles["section"]),
        _two_cards(
            _card("单笔小仓位", "按第1页竞价分档，先选开盘后兑现或等9:35。≥5%支持多等一个节点；3%-4%偏弱；4%-5%中性；低于3%不直接判弱。不为了凑尾仓比例延长风险暴露。", styles, width=(CONTENT_WIDTH-5*mm)/2, compact=True),
            _card("可以分批", "可先兑现一部分，再逐节点管理余仓。估值超上沿且转弱，更倾向兑现；低于估值不能替双低解围。卖完后只做复盘，不追认持仓。", styles, width=(CONTENT_WIDTH-5*mm)/2, kind="green", compact=True),
        ),
        Spacer(1, 6),
        _p("情绪尚不能设成硬开关：只取此前已上市新股“首日收盘较开盘”的赚钱效应。竞价≥5%组，按此前5只中位数为正/非正分组，等待更高为5/7和6/7；改看此前3只变成7/7和4/7，10只为6/8和5/6。窗口敏感，暂只作背景观察。", styles["small"]),
        Spacer(1, 8),
        _p("数据与限制：本地2026-01-16至09-23共60只分钟样本，新增60只iFinD独立竞价快照；旧40只截至7月14日，其后普通18只、原外力标签2只。42只用日线、18只用本地回放校准流通分母，未验证盘前股本可得性；后续价差仍按原分钟缓存。凯达原缓存尾盘缺0.80%，iFinD完整分钟已另存，缺量未摊入早盘。竞价3/4/5/6%为本次待验证观察线，其余探索条件不变。", styles["small"]),
        Spacer(1, 5),
        _p('来源：本地换手临停研究、v5全量复核、早盘修复研究、《集合竞价换手与一手仓位的开盘选择》；<link href="https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/help-center/manual.html" color="#2C77B8">iFinD快照接口</link>。仅适用于获配等当日可卖持仓，当日买入不能同日卖出。制度依据：<link href="https://www.bse.cn/jygl_list/200028217.html" color="#2C77B8">北交所交易规则第3.1.4、4.2.5条</link>。', styles["small"]),
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
        title="北交所新股首日卖出指南 - v5.2简洁版（2026-09-23）",
        author="BJ_IPO research",
        subject="北交所打新获配后的上市首日卖出观察",
    )
    story: list[Any] = []
    story.extend(_page_one(styles))
    story.append(PageBreak())
    story.extend(_page_two(styles))
    story.append(PageBreak())
    story.extend(_page_turnover_halt(styles))
    story.append(PageBreak())
    story.extend(_page_four(styles))
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成北交所新股首日卖出指南v4四页简洁版PDF")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output)
    build_pdf(output)
    print(output)


if __name__ == "__main__":
    main()
