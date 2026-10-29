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
