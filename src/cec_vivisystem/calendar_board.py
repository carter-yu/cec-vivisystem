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


def render_month_board_png(
    model: MonthBoardModel,
    *,
    font_path: Path | str | None = None,
) -> bytes:
    """Render RGB PNG bytes; font loading is the sole filesystem boundary."""
    path = resolve_font_path(font_path)
    try:
        header = ImageFont.truetype(str(path), 40)
        subtitle = ImageFont.truetype(str(path), 20)
        weekday = ImageFont.truetype(str(path), 16)
        day_font = ImageFont.truetype(str(path), 24)
        body = ImageFont.truetype(str(path), 11)
    except (OSError, ValueError) as exc:
        raise CalendarBoardFontError("Could not load calendar board font") from exc
    paper, rule, ink, muted = "#F7F3EE", "#E5DFD6", "#2C2622", "#6B5E55"
    image = Image.new("RGB", (1680, 1260), paper)
    draw = ImageDraw.Draw(image)
    draw.text(
        (48, 70), f"{model.month.year}年{model.month.month}月", font=header, fill=ink
    )
    draw.text(
        (1632, 90), "家庭日曆 · 07:00 更新", font=subtitle, fill=muted, anchor="ra"
    )
    draw.line((48, 144, 1632, 144), fill=rule)
    width, height = 1584 / 7, 1008 / len(model.weeks)
    labels = "一二三四五六日"
    for col in range(7):
        dow = (model.week_start + col) % 7
        x = 48 + col * width
        draw.rectangle(
            (x, 145, x + width, 204), fill="#EEF1F4" if dow in (5, 6) else paper
        )
        draw.text(
            (x + width / 2, 166), labels[dow], font=weekday, fill=muted, anchor="ma"
        )
    for row, week in enumerate(model.weeks):
        for col, cell in enumerate(week):
            x, y = 48 + col * width, 204 + row * height
            fill = "#EEF1F4" if cell.day.weekday() in (5, 6) else paper
            if not cell.in_month:
                fill = "#F0EBE4"
            if cell.is_today:
                fill = "#FFE8D6"
            draw.rectangle((x, y, x + width, y + height), fill=fill, outline=rule)
            if cell.is_today:
                draw.rectangle((x + 1, y + 1, x + 5, y + height - 1), fill="#E8A87C")
            draw.text(
                (x + width - 12, y + 10),
                str(cell.day.day),
                font=day_font,
                fill=ink if cell.in_month else "#B5AEA6",
                anchor="ra",
                stroke_width=1 if cell.is_today else 0,
            )
            for index, line in enumerate(cell.lines):
                ly = y + 48 + index * 24
                if line.all_day:
                    draw.rounded_rectangle(
                        (x + 10, ly, x + width - 10, ly + 24), radius=5, fill="#E5DFD6"
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
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
