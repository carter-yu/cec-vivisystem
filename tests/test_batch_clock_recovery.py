"""Live bug 2026-10-29: v6 model returned start null for list lines with HH:MM."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem.models import IntentType
from cec_vivisystem.parse_fallback import _events_payload_to_parse_result

HK = ZoneInfo("Asia/Hong_Kong")
NOW = datetime(2026, 10, 29, 13, 0, tzinfo=HK)
MSG = (
    "加以下events:\n11月6日（五）15:30 認知訓練\n11月10日（二）15:30 言語訓練\n"
    "11月13日（五）14:00 認知訓練\n11月20日（五）14:00 大小肌肉訓練\n11月27日（五）14:00 認知訓練"
)
ROWS = [("2026-11-06", "認知訓練", "FR"), ("2026-11-10", "言語訓練", "TU"),
        ("2026-11-13", "認知訓練", "FR"), ("2026-11-20", "大小肌肉訓練", "FR"),
        ("2026-11-27", "認知訓練", "FR")]


def _payload(msg, rows, source=True):
    lines = msg.splitlines()[1:]
    return {"intent_type": "create_event", "over_cap": False, "confidence": "medium",
            "events": [{"title": t, "start": None, "end": None, "all_day": False, "location": None,
                            "participants": [], "missing_fields": ["time"], "series": None, "date": d,
                            "stated_weekday": w, "source_text": lines[i] if source else None}
                       for i, (d, t, w) in enumerate(rows)]}


@pytest.mark.parametrize("source", [True, False])
def test_start_null_with_clock_on_line_is_recovered(source):
    r = _events_payload_to_parse_result(_payload(MSG, ROWS, source), raw_text=MSG, now=NOW)
    assert r.intent_type == IntentType.CREATE_EVENT
    starts = [i.start for i in r.batch]
    assert starts[0] == datetime(2026, 11, 6, 15, 30, tzinfo=HK)
    assert starts[4] == datetime(2026, 11, 27, 14, 0, tzinfo=HK)
    assert list(r.batch_not_included) == []


@pytest.mark.parametrize("line", ["11月6日（五） 認知訓練", "11月6日（五）下午3:30 認知訓練",
                                  "11月6日（五）15:30-16:30 認知訓練"])
def test_no_or_ambiguous_clock_still_asks(line):
    msg = f"加以下events:\n{line}\n11月10日（二） 言語訓練"
    r = _events_payload_to_parse_result(_payload(msg, ROWS[:2]), raw_text=msg, now=NOW)
    assert r.intent_type == IntentType.NEEDS_CLARIFICATION
    assert r.missing_fields == ["time"]


# Live re-test 2026-10-29 21:09 HKT: still missing_time:5 after PR #11. The model
# variants below (all-day start, start-less with date null-ish fields, Slack
# NBSP / full-width text) must all recover the written clock.
NBSP_MSG = MSG.replace(" ", "\u00a0").replace("15:30", "１５：３０").replace("14:00", "14\u200b:00")


@pytest.mark.parametrize("variant", ["all_day_midnight", "all_day_no_start", "missing_start"])
@pytest.mark.parametrize("msg", [MSG, NBSP_MSG])
def test_model_variants_recover_clock(variant, msg):
    p = _payload(msg, ROWS)
    for e in p["events"]:
        if variant == "all_day_midnight":
            e.update(start=f"{e['date']}T00:00:00+08:00", all_day=True, missing_fields=[])
        elif variant == "all_day_no_start":
            e.update(all_day=True)
        else:
            e.update(missing_fields=["start"])
    r = _events_payload_to_parse_result(p, raw_text=msg, now=NOW)
    assert r.intent_type == IntentType.CREATE_EVENT
    assert [i.start.strftime("%H:%M") for i in r.batch] == [
        "15:30", "15:30", "14:00", "14:00", "14:00"]
    assert all(not i.all_day for i in r.batch)


def test_explicit_all_day_is_kept():
    msg = "加以下events:\n11月6日（五）全日 認知訓練\n11月10日（二）全日 言語訓練"
    p = _payload(msg, ROWS[:2])
    for e in p["events"]:
        e.update(start=f"{e['date']}T00:00:00+08:00", all_day=True, missing_fields=[])
    r = _events_payload_to_parse_result(p, raw_text=msg, now=NOW)
    assert r.intent_type == IntentType.CREATE_EVENT
    assert all(i.all_day for i in r.batch)


def test_clock_recovery_logs_counts_only(monkeypatch):
    from unittest.mock import Mock
    logger = Mock()
    monkeypatch.setattr("cec_vivisystem.parse_fallback.logger", logger)
    _events_payload_to_parse_result(_payload(MSG, ROWS), raw_text=MSG, now=NOW)
    call = next(c for c in logger.info.call_args_list if c.args == ("batch_clock_recovery",))
    assert call.kwargs == {"item_count": 5, "start_from_model": 0,
                           "start_recovered": 5, "start_missing": 0}
