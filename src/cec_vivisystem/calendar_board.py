"""Pure month-grid modelling and in-memory PNG rendering (Phase 26).

Only font loading touches the filesystem; no event persistence or provider I/O.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from cec_vivisystem.models import CalendarListedEvent

FAMILY_TZ = ZoneInfo("Asia/Hong_Kong")
BOARD_CAPTION = "今個月日曆一覽"
REPO_FONT = Path(__file__).resolve().parents[2] / "assets/fonts/NotoSansTC-Regular.ttf"
SYSTEM_FONTS = (
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansTC-Regular.ttf"),
    Path("/System/Library/Fonts/PingFang.ttc"),
)


class CalendarBoardFontError(RuntimeError):
    """No usable Traditional Chinese font was available."""


@dataclass(frozen=True, slots=True)
class BoardLine:
    prefix: str
    title: str
    all_day: bool

    @property
    def text(self) -> str:
        return f"{self.prefix} {self.title}"


@dataclass(frozen=True, slots=True)
class BoardCell:
    day: date
    in_month: bool
    is_today: bool
    lines: tuple[BoardLine, ...]
    overflow: int = 0


@dataclass(frozen=True, slots=True)
class MonthBoardModel:
    month: date
    weeks: tuple[tuple[BoardCell, ...], ...]
    week_start: int


@dataclass(frozen=True, slots=True)
class BoardTheme:
    """Visual palette and optional decorations for the month board PNG."""

    name: str
    paper: str
    rule: str
    ink: str
    muted: str
    weekend_wash: str
    adjacent_fill: str
    adjacent_ink: str
    today_fill: str
    today_accent: str
    all_day_pill: str
    accent_burgundy: str = "#6B2D3C"
    accent_forest: str = "#2F4A3C"
    subtitle_en: str | None = None
    draw_crest: bool = False
    map_grid: bool = False


# Phase 26 locked v1 palette (British-neutral parchment).
CLASSIC_THEME = BoardTheme(
    name="classic",
    paper="#F7F3EE",
    rule="#E5DFD6",
    ink="#2C2622",
    muted="#6B5E55",
    weekend_wash="#EEF1F4",
    adjacent_fill="#F0EBE4",
    adjacent_ink="#B5AEA6",
    today_fill="#FFE8D6",
    today_accent="#E8A87C",
    all_day_pill="#E5DFD6",
)

# British-primary with light Game-of-Thrones accents (original crest only).
BRITISH_THEME = BoardTheme(
    name="british",
    paper="#F3E8D4",  # parchment
    rule="#C9B896",  # map-like sepia grid
    ink="#1A2744",  # navy ink
    muted="#5C5346",
    weekend_wash="#E4EAF0",  # cool wash
    adjacent_fill="#E8DFC8",
    adjacent_ink="#A89F8E",
    today_fill="#EFD4B8",  # warm copper wash
    today_accent="#B87333",  # copper/bronze (not bloody red)
    all_day_pill="#E0D4BE",
    accent_burgundy="#6B2D3C",
    accent_forest="#2F4A3C",
    subtitle_en="Winter is coming? · Family plans",
    draw_crest=True,
    map_grid=True,
)

THEMES: dict[str, BoardTheme] = {
    CLASSIC_THEME.name: CLASSIC_THEME,
    BRITISH_THEME.name: BRITISH_THEME,
}


def resolve_theme(theme: BoardTheme | str | None = None) -> BoardTheme:
    if theme is None:
        return CLASSIC_THEME
    if isinstance(theme, BoardTheme):
        return theme
    try:
        return THEMES[theme]
    except KeyError as exc:
        raise ValueError(f"Unknown board theme: {theme!r}") from exc


def _local(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=FAMILY_TZ)
        if value.tzinfo is None
        else value.astimezone(FAMILY_TZ)
    )


def build_month_cells(
    month: date,
    events: Iterable[CalendarListedEvent],
    *,
    today: date,
    week_start: int = calendar.SUNDAY,
) -> MonthBoardModel:
    """Clip occupied HKT days to the month, preserving exclusive event ends."""
    if week_start not in range(7):
        raise ValueError("week_start must be between 0 and 6")
    month = month.replace(day=1)
    normalized = []
    for event in events:
        start = _local(event.start)
        end = (
            _local(event.end)
            if event.end
            else start + timedelta(days=1)
            if event.all_day
            else start + timedelta(hours=1)
        )
        if end <= start:
            raise ValueError("Calendar event end must follow start")
        normalized.append((event, start, end))
    normalized.sort(key=lambda item: (not item[0].all_day, item[1], item[0].event_id))
    weeks = []
    for week in calendar.Calendar(week_start).monthdatescalendar(
        month.year, month.month
    ):
        cells = []
        for day in week:
            in_month = day.month == month.month
            lines = []
            if in_month:
                for event, start, end in normalized:
                    if start.date() <= day <= (end - timedelta(microseconds=1)).date():
                        prefix = "全日" if event.all_day else start.strftime("%H:%M")
                        if day > start.date():
                            prefix = "→ " + prefix
                        title = " ".join((event.summary or "(untitled)").split())
                        lines.append(
                            BoardLine(prefix, title or "(untitled)", event.all_day)
                        )
            cells.append(
                BoardCell(
                    day,
                    in_month,
                    in_month and day == today,
                    tuple(lines[:4]),
                    max(0, len(lines) - 4),
                )
            )
        weeks.append(tuple(cells))
    return MonthBoardModel(month, tuple(weeks), week_start)


def resolve_font_path(explicit: Path | str | None = None) -> Path:
    """Try the injected path, bundled font, then known system TC fonts."""
    candidates = [Path(explicit)] if explicit is not None else []
    candidates += [REPO_FONT, *SYSTEM_FONTS]
    for candidate in candidates:
        try:
            ImageFont.truetype(str(candidate), 16)
            return candidate
        except (OSError, ValueError):
            continue
    raise CalendarBoardFontError(
        "No loadable TC font; provide Noto Sans TC or PingFang TC"
    )


def _truncate(title: str, font: ImageFont.FreeTypeFont, width: float) -> str:
    # Bound pathological provider titles before measuring; times are never cut.
    candidate = title[:160]
    if len(title) <= 160 and font.getlength(candidate) <= width:
        return candidate
    while candidate and font.getlength(candidate + "…") > width:
        candidate = candidate[:-1]
    return candidate + "…"


def _hex_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _blend(a: str, b: str, t: float) -> tuple[int, int, int]:
    ar, ag, ab = _hex_rgb(a)
    br, bg, bb = _hex_rgb(b)
    return (
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


def _draw_original_crest(draw: ImageDraw.ImageDraw, cx: int, cy: int, theme: BoardTheme) -> None:
    """Simplified original wolf/sword/shield line-art (not a copyrighted logo)."""
    navy = theme.ink
    burgundy = theme.accent_burgundy
    forest = theme.accent_forest
    # Shield outline
    shield = [
        (cx - 22, cy - 28),
        (cx + 22, cy - 28),
        (cx + 22, cy - 2),
        (cx + 12, cy + 18),
        (cx, cy + 28),
        (cx - 12, cy + 18),
        (cx - 22, cy - 2),
    ]
    draw.polygon(shield, outline=navy)
    # Inner shield accent
    draw.line(
        [(cx - 16, cy - 22), (cx + 16, cy - 22), (cx + 16, cy - 2), (cx, cy + 18), (cx - 16, cy - 2), (cx - 16, cy - 22)],
        fill=burgundy,
        width=1,
    )
    # Vertical sword (blade + crossguard + pommel) — geometric only
    draw.line([(cx, cy - 34), (cx, cy + 22)], fill=navy, width=2)
    draw.line([(cx - 10, cy - 18), (cx + 10, cy - 18)], fill=forest, width=2)
    draw.ellipse((cx - 3, cy + 20, cx + 3, cy + 26), outline=burgundy)
    # Simplified wolf head (ears + snout) — original stylized mark
    draw.line([(cx - 10, cy - 8), (cx - 6, cy - 18), (cx - 2, cy - 8)], fill=navy, width=1)
    draw.line([(cx + 2, cy - 8), (cx + 6, cy - 18), (cx + 10, cy - 8)], fill=navy, width=1)
    draw.arc((cx - 10, cy - 10, cx + 10, cy + 8), start=200, end=340, fill=navy, width=1)
    draw.line([(cx, cy - 2), (cx + 8, cy + 2)], fill=navy, width=1)


def _draw_map_frame(
    draw: ImageDraw.ImageDraw,
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
    theme: BoardTheme,
) -> None:
    """Cartographic double-rule frame with corner ticks (map-like)."""
    draw.rectangle((left, top, right, bottom), outline=theme.rule, width=1)
    draw.rectangle((left + 3, top + 3, right - 3, bottom - 3), outline=theme.ink, width=1)
    tick = 10
    for x, y, dx, dy in (
        (left, top, 1, 1),
        (right, top, -1, 1),
        (left, bottom, 1, -1),
        (right, bottom, -1, -1),
    ):
        draw.line([(x, y), (x + dx * tick, y)], fill=theme.accent_burgundy, width=2)
        draw.line([(x, y), (x, y + dy * tick)], fill=theme.accent_forest, width=2)


def render_month_board_png(
    model: MonthBoardModel,
    *,
    font_path: Path | str | None = None,
    theme: BoardTheme | str | None = None,
) -> bytes:
    """Render RGB PNG bytes; font loading is the sole filesystem boundary."""
    style = resolve_theme(theme)
    path = resolve_font_path(font_path)
    try:
        header = ImageFont.truetype(str(path), 40)
        subtitle = ImageFont.truetype(str(path), 20)
        en_sub = ImageFont.truetype(str(path), 14)
        weekday = ImageFont.truetype(str(path), 16)
        day_font = ImageFont.truetype(str(path), 24)
        body = ImageFont.truetype(str(path), 11)
    except (OSError, ValueError) as exc:
        raise CalendarBoardFontError("Could not load calendar board font") from exc
    paper, rule, ink, muted = style.paper, style.rule, style.ink, style.muted
    image = Image.new("RGB", (1680, 1260), paper)
    draw = ImageDraw.Draw(image)

    title_x = 48
    if style.draw_crest:
        _draw_original_crest(draw, 70, 78, style)
        title_x = 108
        # Burgundy/forest accent ticks beside crest
        draw.line((98, 52, 98, 104), fill=style.accent_burgundy, width=2)
        draw.line((102, 52, 102, 104), fill=style.accent_forest, width=1)

    draw.text(
        (title_x, 70),
        f"{model.month.year}年{model.month.month}月",
        font=header,
        fill=ink,
    )
    draw.text(
        (1632, 78), "家庭日曆 · 07:00 更新", font=subtitle, fill=muted, anchor="ra"
    )
    if style.subtitle_en:
        draw.text(
            (1632, 104),
            style.subtitle_en,
            font=en_sub,
            fill=style.accent_burgundy,
            anchor="ra",
        )

    # Header rule: burgundy hairline over forest for british; plain for classic
    if style.draw_crest:
        draw.line((48, 142, 1632, 142), fill=style.accent_forest, width=1)
        draw.line((48, 145, 1632, 145), fill=style.accent_burgundy, width=1)
    else:
        draw.line((48, 144, 1632, 144), fill=rule)

    grid_top = 204
    width, height = 1584 / 7, 1008 / len(model.weeks)
    labels = "一二三四五六日"
    for col in range(7):
        dow = (model.week_start + col) % 7
        x = 48 + col * width
        header_fill = style.weekend_wash if dow in (5, 6) else paper
        draw.rectangle((x, 145, x + width, grid_top), fill=header_fill)
        # Weekday label with subtle forest on weekends for british
        label_fill = style.accent_forest if style.draw_crest and dow in (5, 6) else muted
        draw.text(
            (x + width / 2, 166), labels[dow], font=weekday, fill=label_fill, anchor="ma"
        )
    for row, week in enumerate(model.weeks):
        for col, cell in enumerate(week):
            x, y = 48 + col * width, grid_top + row * height
            fill = style.weekend_wash if cell.day.weekday() in (5, 6) else paper
            if not cell.in_month:
                fill = style.adjacent_fill
            if cell.is_today:
                fill = style.today_fill
            draw.rectangle((x, y, x + width, y + height), fill=fill, outline=rule)
            if cell.is_today:
                draw.rectangle(
                    (x + 1, y + 1, x + 5, y + height - 1), fill=style.today_accent
                )
                if style.draw_crest:
                    # Thin copper outer stroke for today
                    draw.rectangle(
                        (x + 1, y + 1, x + width - 1, y + height - 1),
                        outline=style.today_accent,
                    )
            day_fill = ink if cell.in_month else style.adjacent_ink
            day_kwargs = {
                "font": day_font,
                "fill": day_fill,
                "anchor": "ra",
            }
            if cell.is_today:
                day_kwargs["stroke_width"] = 1
                day_kwargs["stroke_fill"] = paper
            draw.text((x + width - 12, y + 10), str(cell.day.day), **day_kwargs)
            for index, line in enumerate(cell.lines):
                ly = y + 48 + index * 24
                if line.all_day:
                    draw.rounded_rectangle(
                        (x + 10, ly, x + width - 10, ly + 24),
                        radius=5,
                        fill=style.all_day_pill,
                    )
                    if style.draw_crest:
                        draw.rounded_rectangle(
                            (x + 10, ly, x + 14, ly + 24),
                            radius=2,
                            fill=style.accent_burgundy,
                        )
                prefix = line.prefix + " "
                text_x = x + 14
                if prefix.startswith("→ "):
                    # The supplied TC subset omits U+2192. Draw the continuation
                    # arrow geometrically, preserving the font asset and avoiding tofu.
                    draw.line(
                        (text_x, ly + 13, text_x + 13, ly + 13), fill=ink, width=1
                    )
                    draw.line(
                        (text_x + 9, ly + 9, text_x + 13, ly + 13, text_x + 9, ly + 17),
                        fill=ink,
                        width=1,
                    )
                    prefix = prefix[2:]
                    text_x += 20
                title = _truncate(
                    line.title,
                    body,
                    min(110, width - 14 - (text_x - x) - body.getlength(prefix)),
                )
                draw.text((text_x, ly), prefix + title, font=body, fill=ink)
            if cell.overflow:
                draw.text(
                    (x + width - 12, y + height - 24),
                    f"+{cell.overflow} more",
                    font=weekday,
                    fill=muted,
                    anchor="ra",
                )
    if style.map_grid:
        # Fine interior grid hairlines over cell borders for a map feel.
        faint = _blend(style.paper, style.rule, 0.75)
        for col in range(8):
            gx = 48 + col * width
            draw.line(
                (gx, 145, gx, grid_top + len(model.weeks) * height),
                fill=style.rule,
                width=1,
            )
        for row in range(len(model.weeks) + 1):
            gy = grid_top + row * height
            draw.line((48, gy, 1632, gy), fill=style.rule, width=1)
        draw.line((48, 145, 1632, 145), fill=style.rule, width=1)
        # Subtle mid-cell ticks (do not cross event text heavily)
        for col in range(7):
            for row in range(len(model.weeks)):
                cx = 48 + col * width + width / 2
                cy = grid_top + row * height + 6
                draw.line((cx - 4, cy, cx + 4, cy), fill=faint, width=1)
        _draw_map_frame(
            draw,
            left=44,
            top=141,
            right=1636,
            bottom=grid_top + len(model.weeks) * height + 4,
            theme=style,
        )
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
