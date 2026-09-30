"""Phase 26 locked grid/render and independent-delivery cases; synthetic only."""

import re
from datetime import date, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from cec_vivisystem import calendar_board as board
from cec_vivisystem.calendar_writer import FakeCalendarClient
from cec_vivisystem.models import CalendarListedEvent, MorningRecapOutcome
from cec_vivisystem.morning_recap import (
    FakeSlackPoster,
    JsonDirMorningRecapStore,
    maintain_morning_recap_storage,
    run_morning_recap,
)

TZ = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 1, 7, tzinfo=TZ)
DAY = NOW.date()


def event(hour=9, *, day=1, end=None, all_day=False, title="游水"):
    start = datetime(2026, 10, day, hour, tzinfo=TZ)
    return CalendarListedEvent(str(hour), title, start, end, all_day)


def cells(events=(), month=DAY):
    model = board.build_month_cells(month, events, today=DAY)
    return {cell.day: cell for week in model.weeks for cell in week}


def test_sunday_alignment_today_and_adjacent_days():
    model = board.build_month_cells(DAY, [], today=DAY)
    assert model.weeks[0][0].day == date(2026, 9, 27)
    assert model.weeks[0][4].day == DAY
    assert model.weeks[0][4].is_today
    assert sum(c.is_today for w in model.weeks for c in w) == 1


def test_all_day_then_ascending_times_and_overflow():
    events = [event(h) for h in (17, 12, 9, 15, 11)] + [event(0, all_day=True)]
    cell = cells(events)[DAY]
    assert [line.prefix for line in cell.lines] == ["全日", "09:00", "11:00", "12:00"]
    assert cell.overflow == 2


def test_multiday_clips_month_and_excludes_midnight_end():
    ev = CalendarListedEvent(
        "trip",
        "旅程",
        datetime(2026, 9, 28, tzinfo=TZ),
        datetime(2026, 10, 3, tzinfo=TZ),
        True,
    )
    grid = cells([ev])
    assert grid[date(2026, 9, 28)].lines == ()
    assert grid[DAY].lines[0].text == "→ 全日 旅程"
    assert grid[date(2026, 10, 2)].lines[0].text == "→ 全日 旅程"
    assert grid[date(2026, 10, 3)].lines == ()


def test_timed_hkt_continuation_and_untitled():
    ev = CalendarListedEvent(
        "a",
        None,
        datetime.fromisoformat("2026-09-30T15:30:00+00:00"),
        datetime.fromisoformat("2026-10-01T01:00:00+00:00"),
        False,
    )
    assert cells([ev])[DAY].lines[0].text == "→ 23:30 (untitled)"
    assert cells([event(title=None)])[DAY].lines[0].text == "09:00 (untitled)"


@pytest.mark.parametrize("month", [DAY, date(2026, 8, 1), date(2026, 2, 1)])
def test_empty_month_png_and_size(month):
    png = board.render_month_board_png(board.build_month_cells(month, [], today=DAY))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(BytesIO(png))
    assert image.size == (1680, 1260)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (247, 243, 238)


def test_font_fallback_order_and_controlled_missing(monkeypatch, tmp_path):
    assert board.resolve_font_path() == board.REPO_FONT
    assert board.resolve_font_path(tmp_path / "missing") == board.REPO_FONT
    explicit = tmp_path / "explicit.ttf"
    explicit.write_bytes(board.REPO_FONT.read_bytes())
    assert board.resolve_font_path(explicit) == explicit
    monkeypatch.setattr(board, "REPO_FONT", tmp_path / "absent")
    monkeypatch.setattr(board, "SYSTEM_FONTS", (explicit,))
    assert board.resolve_font_path() == explicit
    monkeypatch.setattr(board, "SYSTEM_FONTS", ())
    with pytest.raises(board.CalendarBoardFontError, match="TC font"):
        board.render_month_board_png(board.build_month_cells(DAY, [], today=DAY))


def test_bad_interval_and_week_start_are_controlled():
    with pytest.raises(ValueError, match="end"):
        cells([event(end=datetime(2026, 10, 1, 8, tzinfo=TZ))])
    with pytest.raises(ValueError, match="week_start"):
        board.build_month_cells(DAY, [], today=DAY, week_start=7)


def run(tmp_path, poster=None, client=None, **kwargs):
    return run_morning_recap(
        now=kwargs.pop("now", NOW),
        client=client or FakeCalendarClient(),
        poster=poster or FakeSlackPoster(),
        calendar_id="synthetic",
        channel_id="C_TEST",
        store=JsonDirMorningRecapStore(tmp_path),
        **kwargs,
    )


def test_both_success_markers_skip_and_next_day_refresh(tmp_path, capsys):
    poster, client = FakeSlackPoster(), FakeCalendarClient()
    result = run(tmp_path, poster, client)
    assert result.outcome == result.board_outcome == MorningRecapOutcome.POSTED
    assert result.post_text == "早晨。今日（2026-10-01）日曆冇活動。"
    assert result.board_png_bytes == len(poster.file_calls[0][3])
    assert poster.file_calls[0][:3] == (
        "C_TEST",
        board.BOARD_CAPTION,
        "monthly-board-2026-10-01.png",
    )
    store = JsonDirMorningRecapStore(tmp_path)
    assert store.has_posted(DAY) and store.month_board_store().has_posted(DAY)
    assert len(client.list_calls) == 2
    assert client.list_calls[1][1:] == (
        datetime(2026, 10, 1, tzinfo=TZ),
        datetime(2026, 11, 1, tzinfo=TZ),
    )
    again = run(tmp_path, poster, client)
    assert (
        again.outcome
        == again.board_outcome
        == MorningRecapOutcome.SKIPPED_ALREADY_POSTED
    )
    assert len(poster.calls) == len(poster.file_calls) == 1
    assert len(client.list_calls) == 2
    run(tmp_path, poster, client, now=NOW + timedelta(days=1))
    assert len(poster.file_calls) == 2
    assert client.calls == []
    logs = re.sub(r"\x1b\[[0-9;]*m", "", capsys.readouterr().out)
    for name in (
        "render_started",
        "render_succeeded",
        "upload_started",
        "upload_succeeded",
        "skipped",
    ):
        assert "month_board_" + name in logs
    for field in (
        "month=",
        "event_count=",
        "png_bytes=",
        "duration_ms=",
        "correlation_id=",
    ):
        assert field in logs


def test_upload_failure_preserves_text_and_blocks_retry(tmp_path):
    poster = FakeSlackPoster(fail_file_with=TimeoutError("synthetic"))
    client = FakeCalendarClient()
    first = run(tmp_path, poster, client)
    assert first.outcome == MorningRecapOutcome.POSTED
    assert first.board_outcome == MorningRecapOutcome.FAILED
    store = JsonDirMorningRecapStore(tmp_path)
    assert store.has_posted(DAY)
    assert store.month_board_store().read_delivery((DAY,))["status"] == "pending"
    second = run(tmp_path, poster, client)
    assert second.outcome == MorningRecapOutcome.SKIPPED_ALREADY_POSTED
    assert second.board_error_type == "ReconciliationRequired"
    assert len(poster.calls) == len(poster.file_calls) == 1
    assert client.calls == []


def test_month_list_failure_keeps_today_and_can_retry(tmp_path):
    class MonthFailure(FakeCalendarClient):
        def list_events(self, **kwargs):
            if (kwargs["time_max"] - kwargs["time_min"]).days > 1:
                raise RuntimeError("synthetic month failure")
            return super().list_events(**kwargs)

    poster = FakeSlackPoster()
    first = run(tmp_path, poster, MonthFailure())
    assert first.outcome == MorningRecapOutcome.POSTED
    assert first.board_error_type == "RuntimeError"
    assert poster.file_calls == []
    second = run(tmp_path, poster)
    assert second.outcome == MorningRecapOutcome.SKIPPED_ALREADY_POSTED
    assert second.board_outcome == MorningRecapOutcome.POSTED
    assert len(poster.calls) == 1


def test_text_failure_does_not_prevent_board(tmp_path):
    result = run(tmp_path, FakeSlackPoster(fail_with=TimeoutError()))
    assert result.outcome == MorningRecapOutcome.FAILED
    assert result.board_outcome == MorningRecapOutcome.POSTED


def test_font_failure_preserves_text_without_reserving_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "REPO_FONT", tmp_path / "missing")
    monkeypatch.setattr(board, "SYSTEM_FONTS", ())
    result = run(tmp_path)
    assert result.outcome == MorningRecapOutcome.POSTED
    assert result.board_error_type == "CalendarBoardFontError"
    assert (
        JsonDirMorningRecapStore(tmp_path).month_board_store().read_delivery((DAY,))
        is None
    )


def test_board_retention_pending_and_posted(tmp_path):
    run(tmp_path, FakeSlackPoster(fail_file_with=TimeoutError()))
    store = JsonDirMorningRecapStore(tmp_path).month_board_store()
    assert maintain_morning_recap_storage(store, now=NOW + timedelta(days=31)) == 1
    assert store.read_delivery((DAY,)) is None


def test_six_row_render_keeps_overflow_and_draws_continuation_without_missing_glyph(
    monkeypatch,
):
    from PIL import ImageDraw

    recorded = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        recorded.append((xy, text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    events = [
        CalendarListedEvent(
            str(h),
            "家庭活動" * 40,
            datetime(2026, 7, 31, h, tzinfo=TZ),
            datetime(2026, 8, 2, tzinfo=TZ),
            False,
        )
        for h in range(9, 15)
    ]
    model = board.build_month_cells(date(2026, 8, 1), events, today=date(2026, 8, 1))
    assert len(model.weeks) == 6
    board.render_month_board_png(model)
    labels = [text for _, text in recorded]
    assert "+2 more" in labels
    assert any(text.startswith("09:00 ") and text.endswith("…") for text in labels)
    assert not any("→" in text for text in labels)
    assert not any("家庭活動" * 40 in text for text in labels)
    overflow_y = next(xy[1] for xy, text in recorded if text == "+2 more")
    last_line_y = max(xy[1] for xy, text in recorded if text.startswith("12:00"))
    assert overflow_y > last_line_y + 16
