"""Phase 33b trigger: 加event only as a real request (Carter 2026-10-30)."""

import pytest

from cec_vivisystem.image_intake import has_image_trigger


@pytest.mark.parametrize("text", [
    "加event", "請加event", "幫我加event", "加 Event", "加EVENT",
    "呢張係學校通告，加event", "piano 加event", "唔好理佢，加event",
    "唔好加event 呀，算啦，加event",
])
def test_trigger_positive(text):
    assert has_image_trigger(text) is True


@pytest.mark.parametrize("text", [
    "唔好加event", "唔使加 event", "唔駛加event", "唔要加event", "不要加event",
    "不用加event", "別加event", "咪加event", "無需加event", "毋須加event",
    "don't 加event", "Don’t加event", "do not 加event", "no 加event", "NO加event",
    "睇下呢張圖", "加活動", "",
])
def test_trigger_negative(text):
    assert has_image_trigger(text) is False
