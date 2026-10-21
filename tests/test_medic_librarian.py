"""Phase 32 medic librarian tests (offline; synthetic fixture, seed tree, fakes).

Test ids follow phases/phase-32-aia-medic-librarian.md (R, B, S, Q, A, L, I).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from cec_vivisystem import listener
from cec_vivisystem.confirmation import InMemoryConfirmationStore, create_confirmation
from cec_vivisystem.life_notes import InMemoryLifeNotesStore
from cec_vivisystem.medic_librarian import (
    DEFAULT_MEDIC_CHANNEL_ID,
    MedicResponder,
    handle_medic_message,
    resolve_medic_channel_id,
)
from cec_vivisystem.medic_librarian.__main__ import main as medic_cli
from cec_vivisystem.medic_librarian.answer import (
    ANSWER_SCHEMA,
    FakeMedicLlm,
    MedicAnswer,
    XaiMedicLlm,
    ask_models,
    check_answer,
    load_live_medic_llms,
    validate_answer_payload,
)
from cec_vivisystem.medic_librarian.extract import (
    ExtractionError,
    pdftotext_runner,
    split_pages,
)
from cec_vivisystem.medic_librarian.organize import build_tree
from cec_vivisystem.medic_librarian.retrieve import Hit, build_context_pack, retrieve
from cec_vivisystem.medic_librarian.tree import (
    TREE_FILE,
    KnowledgeTree,
    KnowledgeTreeCache,
    KnowledgeTreeError,
    load_tree,
    resolve_kb_dir,
    save_tree,
    seed_kb_dir,
)
from cec_vivisystem.models import (
    InboundMessage,
    IntentType,
    ListenerOutcome,
    ParseResult,
)
from cec_vivisystem.redaction import redact_sensitive

HKT = ZoneInfo("Asia/Hong_Kong")
FIXED_NOW = datetime(2030, 1, 1, 9, 0, tzinfo=HKT)
REPO = Path(__file__).resolve().parents[1]
SYNTHETIC = Path(__file__).parent / "fixtures" / "medic_synthetic_layout.txt"
FAKE_POLICY_NUMBER = "1234567890"
SCRATCH_TEXT = REPO.parent / "cec-vivisystem-scratch" / "jeff-aia-benefit-summary.txt"
MEDIC = "C_MEDIC"
PLANS = "C_FAMILY"
LIFE = "C_LIFE"
LONG_DIGITS = re.compile(r"\d{8,}")


def _synthetic_tree() -> KnowledgeTree:
    return build_tree([("s", SYNTHETIC.read_text(encoding="utf-8"))], now=FIXED_NOW).tree


@pytest.fixture(scope="module")
def synthetic() -> KnowledgeTree:
    return _synthetic_tree()


@pytest.fixture(scope="module")
def seed() -> KnowledgeTree:
    return load_tree(seed_kb_dir() / TREE_FILE)


def _message(text: str, *, channel: str = MEDIC, thread_ts: str | None = None) -> InboundMessage:
    return InboundMessage(text=text, channel_id=channel, user_id="U_PARENT",
                          slack_event_id="Ev1", correlation_id="corr-1",
                          ts="1700000000.000100", thread_ts=thread_ts)


def _raw(text: str, *, channel: str = MEDIC, **extra: object) -> dict:
    event = {"type": "message", "channel": channel, "user": "U_PARENT", "text": text,
             "ts": "1700000000.000100", "client_msg_id": "msg-1"}
    event.update(extra)
    return event


def _answer(text: str, citations: list[str], status: str = "answered") -> MedicAnswer:
    return MedicAnswer(status=status, answer=text, citations=citations, model="fake-medic")


def _node_text(tree: KnowledgeTree) -> str:
    """Every node and document text field, minus the content hash."""
    data = tree.to_dict()
    for doc in data["documents"]:
        doc.pop("text_sha256")
    return json.dumps(data, ensure_ascii=False)


# --- R: redaction ---------------------------------------------------------------------


def test_r1_long_digit_runs_including_underscore_neighbours():
    assert redact_sensitive("ref 0000000000") == ("ref [ID-REDACTED]", 1)
    assert redact_sensitive("0000000000_001_Benefit") == ("[ID-REDACTED]_001_Benefit", 1)


def test_r2_hkid_email_and_phones():
    safe, count = redact_sensitive(
        "id A123456(7) mail kid@example.com tel +852 9123 4567 or 9123-4567"
    )
    assert "A123456" not in safe and "example.com" not in safe and "9123" not in safe
    assert "[HKID-REDACTED]" in safe and "[EMAIL-REDACTED]" in safe
    assert safe.count("[PHONE-REDACTED]") == 2
    assert count == 4


def test_r3_labelled_ids_keep_label():
    safe, count = redact_sensitive("Policy No.: GP-12345 and 保單號碼：AB12345")
    assert safe == "Policy No.: [ID-REDACTED] and 保單號碼：[ID-REDACTED]"
    assert count == 2


def test_r4_amounts_counts_and_dates_survive():
    text = "$1,560,000 HKD/MOP 350 30 visits 04/01/2026 7 days 100% up to $12,000 Policy Year"
    assert redact_sensitive(text) == (text, 0)


def test_r5_idempotent_and_garbage_safe():
    once, first = redact_sensitive("member number 12345678 tel 2345 6789")
    twice, second = redact_sensitive(once)
    assert first == 2 and second == 0 and once == twice
    assert redact_sensitive("") == ("", 0)
    assert redact_sensitive(None) == ("", 0)  # type: ignore[arg-type]


# --- B: build -------------------------------------------------------------------------


def test_b1_expected_categories(synthetic):
    categories = {c.id for c in synthetic.categories}
    assert {"hospitalisation", "outpatient", "dental", "maternity", "assistance",
            "limits", "notes", "general"} <= categories
    assert synthetic.documents[0].label_en == "Benefit Summary"
    assert all(n.page >= 1 for n in synthetic.nodes)


def test_b2_no_raw_policy_number_in_json_or_markdown(synthetic, tmp_path):
    written = save_tree(synthetic, tmp_path)
    assert [p.name for p in written] == ["tree.json", "tree.md"]
    for path in written:
        text = path.read_text(encoding="utf-8")
        assert FAKE_POLICY_NUMBER not in text
        assert "2345 6789" not in text
    assert not LONG_DIGITS.search(_node_text(synthetic))
    assert synthetic.redaction_count >= 2
    effective = synthetic.node("general.benefit-effective-period")
    assert effective is not None and "[ID-REDACTED]" in effective.bullets[0]


def test_b3_network_columns(synthetic):
    home = synthetic.node("hospitalisation.home-care")
    assert home.in_network == ["100%"] and home.out_of_network == ["N/A 不適用"]
    room = synthetic.node("hospitalisation.daily-room-board")
    assert room.both_networks == ["100% 支付", "(Unlimited days 日數不設上限)"]
    pre_post = synthetic.node("hospitalisation.pre-and-post-hospitalisation-consultation")
    assert pre_post.in_network == ["N/A 不適用"] and pre_post.out_of_network == ["$11,110"]
    assert pre_post.title_zh == "入院前及出院後門診諮詢服務"
    physio = synthetic.node("outpatient.physiotherapy-phy")
    assert physio.in_network == ["$0 co-pay $0 自付費"]
    assert physio.out_of_network == [
        "100% up to $5,550 per policy year", "100%支付,每保單年度最高$5,550"]
    assert synthetic.node("hospitalisation.mole-removal").both_networks == ["$22,220"]


def test_b4_merged_na_cell_extends_and_is_flagged(synthetic):
    dental = synthetic.node("hospitalisation.emergency-dental-treatment")
    assert dental.in_network == ["N/A 不適用"] and dental.out_of_network == ["$3,330"]
    assert dental.inferred
    medicine = synthetic.node("outpatient.prescribed-medicine-pma")
    assert medicine.in_network == ["N/A 不適用"] and medicine.inferred
    # A row with both-network values stops the extension.
    assert synthetic.node("hospitalisation.mole-removal").in_network == []
    assert any("merged cell" in w for w in synthetic.warnings)


def test_b5_group_value_hoisted_to_heading(synthetic):
    heading = synthetic.node("dental.dental-benefit")
    assert heading.out_of_network == ["80% up to $990 per policy year"]
    assert any("group" in text for text in heading.inferred)
    cleaning = synthetic.node("dental.teeth-cleaning-oral-examination")
    assert cleaning.parent_id == heading.id
    assert cleaning.conditions == ["(Max. 1 visit per policy year 每保單年度最高限額 1 次)"]


def test_b6_footnotes_and_remarks(synthetic):
    assert synthetic.node("outpatient.physiotherapy-phy").notes == [3]
    assert synthetic.node("hospitalisation.home-care").notes == [2]
    assert synthetic.node("hospitalisation.hospitalisation-benefits").notes == [1]
    note = synthetic.node("notes.note-2")
    assert note.note_number == 2 and note.page == 3
    assert note.bullets[1] == "此保障必須由網絡醫生建議及批准。"
    currency = synthetic.node("general.currency-of-all-amounts")
    assert currency.pinned and "HKD" in currency.bullets[0]


def test_b7_titles_codes_and_alternatives(synthetic):
    gp = synthetic.node("outpatient.general-physicians-consultation-telemedicine-service-gen")
    assert gp.codes == ["GP", "TMGP"]
    assert gp.title_en.startswith("General Physician’s Consultation / Telemedicine Service")
    assert gp.title_zh == "普通科門診諮詢 / 視像診症服務 - 普通科門診諮詢"
    assert gp.in_network == ["$0 co-pay $0 自付費"] and gp.out_of_network == ["100% 支付"]
    major = synthetic.node("hospitalisation.operating-room-major-operation")
    assert major.parent_id == "hospitalisation.operating-room"
    limit = synthetic.node("limits.overall-maximum-per-policy-year-for-outpatient-benefits")
    assert limit.both_networks == ["$8,880"] and limit.section == "Outpatient Benefits"


def test_b8_stable_ids_round_trip_and_markdown(synthetic, tmp_path):
    again = _synthetic_tree()
    assert [n.id for n in again.nodes] == [n.id for n in synthetic.nodes]
    assert len({n.id for n in synthetic.nodes}) == len(synthetic.nodes)
    save_tree(synthetic, tmp_path)
    loaded = load_tree(tmp_path / "tree.json")
    assert loaded.to_dict() == synthetic.to_dict()
    markdown = (tmp_path / "tree.md").read_text(encoding="utf-8")
    assert "## Dental 牙科" in markdown and "Build warnings" in markdown


def test_b9_prose_page_becomes_policy_chunks():
    prose = (
        "Group Medical Policy 保單\n\nSection 4 General Exclusions. This policy does not "
        "cover cosmetic treatment.\n\nThe insured person must give notice within 30 days.\f"
        "Definitions. Hospital means a legally registered hospital.\f"
    )
    tree = build_tree([("p", prose)], now=FIXED_NOW).tree
    assert tree.documents[0].doc_id == "policy"
    pages = {n.page for n in tree.nodes}
    assert pages == {1, 2}
    assert {n.category for n in tree.nodes} <= {"policy", "exclusions", "definitions", "claims"}
    assert any(n.category == "exclusions" and n.page == 1 for n in tree.nodes)
    assert any(n.category == "definitions" and n.page == 2 for n in tree.nodes)


def test_b10_cli_build_with_injected_runner_and_dedupe(tmp_path, capsys):
    folder = tmp_path / "pdfs"
    folder.mkdir()
    for name in ("a.pdf", "b.pdf", "notes.txt"):
        (folder / name).write_bytes(b"%PDF-fake")
    calls = []

    def runner(path):
        calls.append(path.name)
        return SYNTHETIC.read_text(encoding="utf-8")

    out = tmp_path / "kb"
    code = medic_cli(["build", "--pdf", str(folder), "--out", str(out)],
                     pdf_runner=runner, now=FIXED_NOW)
    assert code == 0 and calls == ["a.pdf", "b.pdf"]
    printed = capsys.readouterr().out
    assert "identical copies skipped: 1" in printed
    assert FAKE_POLICY_NUMBER not in printed
    tree = load_tree(out / "tree.json")
    assert len(tree.documents) == 1
    assert not list(out.glob("*.pdf")) and not list(out.glob("*.txt"))


def test_b10_missing_pdftotext_is_a_clear_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(ExtractionError, match="brew install poppler"):
        pdftotext_runner(tmp_path / "x.pdf")
    code = medic_cli(["build", "--pdf", str(tmp_path / "x.pdf"), "--out", str(tmp_path / "kb")])
    assert code == 2
    assert "pdftotext not found" in capsys.readouterr().err
    assert not (tmp_path / "kb").exists()
    assert medic_cli(["build", "--out", str(tmp_path / "kb")]) == 2


def test_b11_garbage_and_empty_text(tmp_path, capsys):
    empty = build_tree([("e", "")], now=FIXED_NOW)
    assert empty.tree.nodes == [] and any("no text" in w for w in empty.tree.warnings)
    garbage = build_tree([("g", "\x00\x01 ??? \f\f   ")], now=FIXED_NOW)
    assert isinstance(garbage.tree, KnowledgeTree)
    source = tmp_path / "empty.txt"
    source.write_text("   \n\f", encoding="utf-8")
    code = medic_cli(["build", "--from-text", str(source), "--out", str(tmp_path / "kb")],
                     now=FIXED_NOW)
    assert code == 1 and not (tmp_path / "kb" / "tree.json").exists()
    assert split_pages("a\fb\f") == ["a", "b"]


@pytest.mark.skipif(not SCRATCH_TEXT.exists(), reason="workshop scratch text not present")
def test_b12_live_scratch_text_builds_without_policy_number():
    tree = build_tree([("s", SCRATCH_TEXT.read_text(encoding="utf-8"))], now=FIXED_NOW).tree
    categories = {c.id for c in tree.categories}
    assert {"hospitalisation", "outpatient", "dental", "maternity", "assistance",
            "limits", "notes", "general"} <= categories
    assert not LONG_DIGITS.search(_node_text(tree))
    assert tree.redaction_count >= 1


# --- S: seed and resolution -----------------------------------------------------------


def test_s1_seed_loads_and_is_sanitized(seed):
    assert seed.schema == "cec-medic-kb/v1" and seed.kb_id == "jeff-aia-medic"
    for node_id in ("outpatient.physiotherapy-phy", "dental.dental-benefit",
                    "hospitalisation.daily-room-board", "notes.note-5",
                    "general.currency-of-all-amounts"):
        assert seed.node(node_id) is not None, node_id
    assert not LONG_DIGITS.search(_node_text(seed))
    assert seed.node("outpatient.physiotherapy-phy").page == 2


def test_s2_cache_prefers_data_tree_then_seed(tmp_path, synthetic, capsys):
    data_path = tmp_path / "kb" / TREE_FILE
    cache = KnowledgeTreeCache(data_path, seed_kb_dir() / TREE_FILE)
    assert cache.get() is not None and cache.source == "seed"
    save_tree(synthetic, data_path.parent)
    assert cache.get().documents[0].pages == 3 and cache.source == "data"
    data_path.write_text("{not json", encoding="utf-8")
    tree = cache.get()
    assert cache.source == "seed" and tree.node("outpatient.physiotherapy-phy") is not None
    assert "medic_kb_load_failed" in capsys.readouterr().out
    assert cache.get() is tree and "medic_kb_load_failed" not in capsys.readouterr().out
    assert resolve_kb_dir({"CEC_MEDIC_KB_DIR": str(tmp_path / "x")}) == tmp_path / "x"
    assert resolve_kb_dir({}).parts[-3:] == ("data", "agent_librarian", "jeff-aia-medic")
    with pytest.raises(KnowledgeTreeError):
        KnowledgeTree.from_dict({"schema": "other"})


def test_s2_install_seed_cli(tmp_path):
    out = tmp_path / "kb"
    assert medic_cli(["install-seed", "--out", str(out)]) == 0
    assert load_tree(out / TREE_FILE).kb_id == "jeff-aia-medic"
    assert medic_cli(["install-seed", "--out", str(out)]) == 2
    assert medic_cli(["install-seed", "--out", str(out), "--force"]) == 0


# --- Q: retrieval ---------------------------------------------------------------------


@pytest.mark.parametrize(("question", "expected"), [
    ("洗牙有冇得 claim？", "dental.teeth-cleaning-oral-examination"),
    ("How much physiotherapy can I claim?", "outpatient.physiotherapy-phy"),
    ("生仔可以賠幾多", "maternity.normal-delivery"),
    ("What is the GP co-pay?", "outpatient.general-physicians-consultation-telemedicine-service-gen"),
    ("打流感針", "outpatient.vaccination-vac"),
    ("物理治療上限幾多", "outpatient.physiotherapy-phy"),
])
def test_q1_expected_top_node(seed, question, expected):
    hits = retrieve(seed, question)
    assert hits and hits[0].node.id == expected


@pytest.mark.parametrize("question", ["weather tomorrow", "明天天氣點樣", "hello", "好唔好食"])
def test_q2_unrelated_questions_have_no_hits(seed, question):
    assert retrieve(seed, question) == []


def test_q3_context_pack_adds_parents_limits_notes_and_currency(seed):
    hits = retrieve(seed, "How much physiotherapy can I claim?")
    context, redactions = build_context_pack(seed, hits)
    ids = [entry["id"] for entry in context]
    assert ids[0] == "outpatient.physiotherapy-phy"
    assert "outpatient.outpatient-benefits" in ids
    assert "notes.note-5" in ids and "general.currency-of-all-amounts" in ids
    assert any(i.startswith("limits.") for i in ids)
    assert redactions == 0
    assert {"id", "document", "page", "category", "title", "path", "text"} == set(context[0])
    small, _ = build_context_pack(seed, hits, max_chars=400)
    assert small[0]["id"] == "outpatient.physiotherapy-phy" and len(small) < len(ids)


def test_q3_context_pack_redacts_again(synthetic):
    node = synthetic.node("notes.note-1")
    node.bullets = ["Call 2345 6789 for help."]
    context, redactions = build_context_pack(synthetic, [Hit(node, 9.0)])
    assert redactions == 1 and "2345" not in json.dumps(context)
    node.bullets = []  # module fixture is shared


# --- A: answers -----------------------------------------------------------------------


def test_a1_answer_cites_page_and_category(seed, capsys):
    llm = FakeMedicLlm(_answer(
        "Out-of-network physiotherapy pays 100% up to $12,000 per policy year. "
        "In-network has a $0 co-pay.", ["outpatient.physiotherapy-phy"]))
    result = handle_medic_message(_message("How much physiotherapy can I claim?"),
                                  tree=seed, llm=llm)
    assert result.outcome == ListenerOutcome.REPLIED
    assert "$12,000" in result.reply_text
    assert "Source: Benefit Summary p.2 · Outpatient › Physiotherapy (PHY) 物理治療" in result.reply_text
    assert "AIA's policy terms decide each claim" in result.reply_text
    assert llm.calls and llm.calls[0]["language"] == "en"
    out = capsys.readouterr().out
    assert "How much physiotherapy" not in out
    assert "medic_message_handled" in out


def test_a2_no_hit_replies_unknown_without_model(seed):
    llm = FakeMedicLlm(_answer("x", ["outpatient.physiotherapy-phy"]))
    result = handle_medic_message(_message("What is the weather tomorrow?"), tree=seed, llm=llm)
    assert "I cannot find this in the Benefit Summary" in result.reply_text
    assert "I do not know" in result.reply_text
    assert llm.calls == []
    zh = handle_medic_message(_message("聽日天氣點樣？"), tree=seed, llm=llm)
    assert zh.reply_text.startswith("福利概要入面搵唔到") and llm.calls == []


def test_a3_model_not_in_document_is_unknown(seed):
    llm = FakeMedicLlm(_answer("", [], status="not_in_document"))
    result = handle_medic_message(_message("Is physiotherapy at home covered?"), tree=seed, llm=llm)
    assert "I do not know the answer from this document" in result.reply_text
    assert len(llm.calls) == 1


def test_a4_unknown_citation_falls_back_to_document_lines(seed, capsys):
    llm = FakeMedicLlm(_answer("Physio is covered.", ["made.up-node"]))
    result = handle_medic_message(_message("physiotherapy limit"), tree=seed, llm=llm)
    assert result.reply_text.startswith("I cannot write an answer now.")
    assert "Physiotherapy (PHY)" in result.reply_text and "100% up to $12,000" in result.reply_text
    out = capsys.readouterr().out
    assert "unknown_citation" in out and "medic_extractive_fallback" in out


def test_a5_invented_figure_is_rejected(seed, capsys):
    llm = FakeMedicLlm(_answer("Physiotherapy pays up to $99,999 a year.",
                               ["outpatient.physiotherapy-phy"]))
    result = handle_medic_message(_message("physiotherapy limit"), tree=seed, llm=llm)
    assert "$99,999" not in result.reply_text
    assert "unverified_figure" in capsys.readouterr().out
    context = [{"id": "a", "title": "T", "text": "100% up to $12,000; 182 days"}]
    assert check_answer(_answer("100% up to $12,000 for 182 days, 1 visit.", ["a"]), context) is None
    assert check_answer(_answer("Pays 90%.", ["a"]), context) == "unverified_figure"
    assert check_answer(_answer("ok", []), context) == "no_citation"
    assert check_answer(_answer("", ["a"]), context) == "empty_answer"


def test_a6_fallback_model_then_extractive(seed, capsys):
    primary = FakeMedicLlm(fail_with=TimeoutError("slow"), model="m1")
    fallback = FakeMedicLlm(_answer("Covered at 100% 支付.", ["outpatient.renal-dialysis-ord"]),
                            model="m2")
    result = handle_medic_message(_message("renal dialysis"), tree=seed, llm=primary,
                                  llm_fallback=fallback)
    assert "Covered at 100% 支付." in result.reply_text and len(fallback.calls) == 1
    assert "medic_answer_failover" in capsys.readouterr().out
    both_down = FakeMedicLlm(fail_with=RuntimeError("503"), model="m3")
    outage = handle_medic_message(_message("renal dialysis"), tree=seed, llm=both_down,
                                  llm_fallback=FakeMedicLlm(fail_with=RuntimeError("x"), model="m4"))
    assert outage.reply_text.startswith("I cannot write an answer now.")
    assert "Renal Dialysis" in outage.reply_text
    no_model = handle_medic_message(_message("renal dialysis"), tree=seed)
    assert no_model.reply_text.startswith("I cannot write an answer now.")
    same = FakeMedicLlm(fail_with=RuntimeError("x"), model="same")
    assert ask_models("q", context=[], language="en", llm=same, llm_fallback=same) == (
        None, "llm_failed")
    assert len(same.calls) == 1


def test_a7_policy_number_never_reaches_the_model_or_logs(seed, capsys):
    llm = FakeMedicLlm(_answer("Dental pays 80% up to $7,800 per policy year.",
                               ["dental.dental-benefit"]))
    result = handle_medic_message(
        _message("My policy number is 0000098765, how much dental can I claim?"),
        tree=seed, llm=llm)
    sent = llm.calls[0]["question"]
    assert "0000098765" not in sent and "[ID-REDACTED]" in sent
    assert "0000098765" not in json.dumps(llm.calls[0]["context"], ensure_ascii=False)
    assert "0000098765" not in result.reply_text
    out = capsys.readouterr().out
    assert "0000098765" not in out and "redaction_count=1" in re.sub(r"\x1b\[[0-9;]*m", "", out)


def test_a8_language_and_help(seed):
    llm = FakeMedicLlm(_answer("洗牙屬牙科福利，非網絡 80% 支付，每保單年度最高 $7,800。",
                               ["dental.teeth-cleaning-oral-examination", "dental.dental-benefit"]))
    zh = handle_medic_message(_message("洗牙有冇得 claim？"), tree=seed, llm=llm)
    assert llm.calls[0]["language"] == "zh"
    assert "來源：福利概要 第 3 頁 · 牙科 › 洗牙及口腔檢查" in zh.reply_text
    assert "實際賠償以友邦保單條款為準" in zh.reply_text
    for word in ("help", "指令", " HELP? "):
        reply = handle_medic_message(_message(word), tree=seed, llm=llm).reply_text
        assert "AIA medical plan" in reply and "友邦醫療計劃" in reply
        assert "do not change the calendar" in reply
    assert len(llm.calls) == 1


def test_a9_simplified_answer_and_id_echo_are_blocked(seed):
    simplified = FakeMedicLlm(_answer("洗牙属于牙科福利。", ["dental.teeth-cleaning-oral-examination"]))
    reply = handle_medic_message(_message("洗牙有冇得 claim？"), tree=seed, llm=simplified).reply_text
    assert "属" not in reply and reply.startswith("暫時未能整理答案")
    echo = FakeMedicLlm(_answer("Your plan 12345678901 covers 100% 支付.",
                                ["outpatient.renal-dialysis-ord"]))
    reply = handle_medic_message(_message("renal dialysis"), tree=seed, llm=echo).reply_text
    assert "12345678901" not in reply


def test_a10_missing_tree_and_crash_reply_briefly(seed, capsys):
    missing = handle_medic_message(_message("physiotherapy"), tree=None)
    assert missing.reply_text.startswith("The plan document is not available now.")

    def broken(tree, question):
        raise RuntimeError("secret internals")

    crashed = handle_medic_message(_message("物理治療"), tree=seed, retrieve_fn=broken)
    assert crashed.outcome == ListenerOutcome.REPLIED
    assert crashed.reply_text == "唔好意思，暫時答唔到，請稍後再試。"
    assert "Traceback" not in crashed.reply_text and "secret internals" not in crashed.reply_text
    assert "medic_message_failed" in capsys.readouterr().out


def test_a11_live_client_request_shape_and_validation():
    sent = {}

    class FakeCompletions:
        def create(self, **kwargs):
            sent.update(kwargs)
            content = json.dumps({"status": "answered", "answer": "100% 支付",
                                  "citations": ["outpatient.renal-dialysis-ord"]})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7))

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    llm = XaiMedicLlm(api_key="fake", model="grok-test", client_factory=lambda: client)
    result = llm.answer("[ID-REDACTED] renal?", context=[{"id": "a"}], language="zh")
    assert result.citations == ["outpatient.renal-dialysis-ord"] and result.prompt_tokens == 11
    assert sent["temperature"] == 0 and sent["model"] == "grok-test"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["response_format"]["json_schema"]["schema"] == ANSWER_SCHEMA
    payload = json.loads(sent["messages"][1]["content"])
    assert payload["reply_language"] == "zh-Hant-HK" and payload["context"] == [{"id": "a"}]
    assert "Answer only from context" in sent["messages"][0]["content"]
    for bad in ({}, {"status": "maybe", "answer": "", "citations": []},
                {"status": "answered", "answer": 3, "citations": []}):
        with pytest.raises((ValueError, TypeError)):
            validate_answer_payload(bad)
    primary, fallback = load_live_medic_llms({"XAI_API_KEY": "k", "LLM_MODEL": "grok-4.5"})
    assert primary.model == "grok-4.20-0309-non-reasoning" and fallback.model == "grok-4.5"
    assert primary._max_retries == 0
    assert load_live_medic_llms({}) == (None, None)


def test_a13_extractive_reply_borrows_shared_group_amount(seed):
    reply = handle_medic_message(_message("How much can I claim for a filling?"),
                                 tree=seed).reply_text
    assert "Dental › Fillings 補牙" in reply
    assert ("Dental Benefit 牙科醫療福利: In-network 網絡: N/A 不適用; "
            "Out-of-network 非網絡: 80% up to $7,800") in reply


def test_a12_search_cli_previews_without_model(capsys):
    assert medic_cli(["search", "物理治療上限幾多"]) == 0
    out = capsys.readouterr().out
    assert "outpatient.physiotherapy-phy" in out
    assert medic_cli(["search", "weather tomorrow"]) == 0
    assert "no match" in capsys.readouterr().out


# --- L: listener routing --------------------------------------------------------------


class _RecordingParse:
    def __init__(self):
        self.calls = []

    def __call__(self, text, **kwargs):
        self.calls.append(text)
        return ParseResult(intent_type=IntentType.UNKNOWN, title=None, start=None, end=None,
                           all_day=False, location=None, raw_text=text)


def _responder(seed, llm=None):
    calls = []

    def respond(message, *, correlation_id=None):
        calls.append(message.text)
        return handle_medic_message(message, tree=seed, llm=llm, correlation_id=correlation_id)

    return respond, calls


def test_l1_medic_channel_never_parses_or_confirms(seed):
    parse = _RecordingParse()
    store = InMemoryConfirmationStore()
    respond, calls = _responder(seed)
    result = listener.process_slack_message_event(
        _raw("明天下午3點去游泳"), allowed_channel_ids={PLANS, MEDIC}, parse=parse,
        now=FIXED_NOW, confirmation_store=store, medic_channel_id=MEDIC,
        medic_responder=respond,
    )
    assert calls == ["明天下午3點去游泳"] and parse.calls == []
    assert store.list_pending() == []
    assert result.outcome == ListenerOutcome.REPLIED and result.parse_result is None


def test_l2_thread_yes_in_medic_channel_resolves_nothing(seed):
    store = InMemoryConfirmationStore()
    pending = create_confirmation(
        ParseResult(intent_type=IntentType.CREATE_EVENT, title="Swim",
                    start=datetime(2030, 1, 2, 15, 0, tzinfo=HKT), end=None, all_day=False,
                    location=None, raw_text="swim"),
        store=store, now=FIXED_NOW, channel_id=MEDIC, thread_ts="1700000000.000100",
        source_message_id="m0",
    )
    respond, calls = _responder(seed)
    listener.process_slack_message_event(
        _raw("yes", thread_ts="1700000000.000100"), allowed_channel_ids={PLANS},
        now=FIXED_NOW, confirmation_store=store, medic_channel_id=MEDIC,
        medic_responder=respond,
    )
    assert calls == ["yes"]
    assert store.get(pending.confirmation_id).status.value == "pending"


def test_l3_plans_and_life_notes_unchanged(seed):
    respond, calls = _responder(seed)
    store = InMemoryConfirmationStore()
    plans = listener.process_slack_message_event(
        _raw("星期六下午3點帶 Cedric 去游泳", channel=PLANS), allowed_channel_ids={PLANS},
        now=FIXED_NOW, confirmation_store=store, medic_channel_id=MEDIC,
        medic_responder=respond,
    )
    assert plans.parse_result.intent_type == IntentType.CREATE_EVENT
    assert "Reply yes" in plans.reply_text and len(store.list_pending()) == 1
    notes = InMemoryLifeNotesStore()
    life = listener.process_slack_message_event(
        _raw("今日好開心。", channel=LIFE), allowed_channel_ids={PLANS},
        life_notes_channel_id=LIFE, life_notes_store=notes, now=FIXED_NOW,
        medic_channel_id=MEDIC, medic_responder=respond,
    )
    assert life.reply_text == listener.LIFE_NOTE_ACK and calls == []


def test_l4_config_default_disable_and_conflict(capsys):
    base = {"SLACK_BOT_TOKEN": "xoxb-test", "SLACK_APP_TOKEN": "xapp-test",
            "SLACK_FAMILY_PLANS_CHANNEL_ID": PLANS, "SLACK_LIFE_NOTES_CHANNEL_ID": LIFE}
    assert listener.load_slack_config(base).medic_channel_id == DEFAULT_MEDIC_CHANNEL_ID
    assert DEFAULT_MEDIC_CHANNEL_ID == "C0CB179V0AE"
    assert listener.load_slack_config({**base, "CEC_MEDIC_CHANNEL_ID": " C_X "}).medic_channel_id == "C_X"
    assert listener.load_slack_config({**base, "CEC_MEDIC_CHANNEL_ID": "off"}).medic_channel_id is None
    assert resolve_medic_channel_id({"CEC_MEDIC_CHANNEL_ID": ""}) == DEFAULT_MEDIC_CHANNEL_ID
    assert listener.load_slack_config({**base, "CEC_MEDIC_CHANNEL_ID": PLANS}).medic_channel_id is None
    assert "medic_channel_conflict" in capsys.readouterr().out


def test_l5_ignored_events_and_responder_failure(seed):
    respond, calls = _responder(seed)
    kwargs = {"allowed_channel_ids": {PLANS}, "medic_channel_id": MEDIC, "medic_responder": respond}
    assert listener.process_slack_message_event(
        _raw("hi", bot_id="B1"), **kwargs).ignore_reason == "bot_message"
    assert listener.process_slack_message_event(_raw("   "), **kwargs).ignore_reason == "empty_text"
    assert listener.process_slack_message_event(
        _raw("hi", subtype="message_changed"), **kwargs).outcome == ListenerOutcome.IGNORED
    assert listener.process_slack_message_event(
        _raw("physio"), allowed_channel_ids={PLANS}, medic_channel_id=MEDIC,
    ).ignore_reason == "medic_not_configured"
    assert calls == []

    def explode(message, *, correlation_id=None):
        raise RuntimeError("boom")

    failed = listener.process_slack_message_event(
        _raw("physio"), allowed_channel_ids={PLANS}, medic_channel_id=MEDIC,
        medic_responder=explode)
    assert failed.reply_text == listener.MEDIC_APOLOGY and "boom" not in failed.reply_text


def test_l6_runtime_replies_in_thread_for_medic(tmp_path, monkeypatch, seed):
    import slack_bolt
    import slack_bolt.adapter.socket_mode

    from tests.test_listener import _FakeSocketClient

    class FakeApp:
        def __init__(self, **kwargs):
            self.callback = None

        def event(self, name):
            def register(callback):
                self.callback = callback
                return callback
            return register

    instances = []

    class FakeHandler:
        def __init__(self, app, token, **kwargs):
            self.app = app
            self.client = _FakeSocketClient(connected=True)
            self.client.on_error_listeners = []
            self.client.on_close_listeners = []
            instances.append(self)

        def connect(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(slack_bolt, "App", FakeApp)
    monkeypatch.setattr(slack_bolt.adapter.socket_mode, "SocketModeHandler", FakeHandler)
    for name in ("life_notes_data_dir", "confirmation_data_dir", "calendar_audit_data_dir",
                 "important_dates_data_dir", "parse_miss_dir"):
        monkeypatch.setattr(listener, name, lambda name=name: tmp_path / name)
    monkeypatch.setattr(listener, "health_heartbeat_path", lambda: tmp_path / "hb.json")
    monkeypatch.setattr(listener, "load_live_llm_parsers", lambda: (None, None))
    monkeypatch.setattr(listener, "load_google_calendar_config", lambda: (_ for _ in ()).throw(
        listener.CalendarWriterConfigError("not configured")))
    llm = FakeMedicLlm(_answer("Renal dialysis is paid at 100% 支付 out-of-network.",
                               ["outpatient.renal-dialysis-ord"]))
    cache = KnowledgeTreeCache(tmp_path / "none" / TREE_FILE, seed_kb_dir() / TREE_FILE)
    monkeypatch.setattr(listener, "load_medic_responder",
                        lambda: MedicResponder(cache=cache, llm=llm))
    replies = []

    def tick(seconds):
        instances[-1].app.callback(
            {"type": "message", "channel": MEDIC, "user": "parent", "text": "renal dialysis?",
             "ts": "5.0", "thread_ts": "4.0"},
            lambda **reply: replies.append(reply),
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(listener.time, "sleep", tick)
    listener.run_socket_mode(listener.SlackConfig(
        "fake-bot", "fake-app", frozenset({PLANS}), LIFE, medic_channel_id=MEDIC))
    assert len(replies) == 1 and replies[0]["thread_ts"] == "4.0"
    assert "Source: Benefit Summary p.2" in replies[0]["text"]

    def broken_loader():
        raise RuntimeError("bad tree dir")

    replies.clear()
    monkeypatch.setattr(listener, "load_medic_responder", broken_loader)
    listener.run_socket_mode(listener.SlackConfig(
        "fake-bot", "fake-app", frozenset({PLANS}), LIFE, medic_channel_id=MEDIC))
    assert replies == []  # medic message ignored; the listener still started
    assert not (tmp_path / "confirmation_data_dir").exists() or not any(
        (tmp_path / "confirmation_data_dir").iterdir())


# --- I: isolation ---------------------------------------------------------------------


def test_i1_medic_import_loads_no_calendar_modules():
    code = (
        "import sys\n"
        "import cec_vivisystem.medic_librarian, cec_vivisystem.medic_librarian.__main__\n"
        "banned = {'calendar_writer', 'confirmation', 'calendar_reader', 'calendar_delete',\n"
        "          'series', 'overlap', 'listener', 'important_dates', 'life_notes'}\n"
        "loaded = {m.split('.')[1] for m in sys.modules if m.startswith('cec_vivisystem.')}\n"
        "print(','.join(sorted(loaded & banned)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            check=True, cwd=REPO)
    assert result.stdout.strip() == ""


def test_i2_medic_source_never_references_calendar_writes():
    banned = ("write_calendar", "resolve_confirmation", "create_confirmation",
              "cec_vivisystem.confirmation", "cec_vivisystem.calendar_", "CalendarClient",
              "life_notes", "important_dates")
    package = REPO / "src" / "cec_vivisystem" / "medic_librarian"
    for path in [*package.glob("*.py"), REPO / "src" / "cec_vivisystem" / "redaction.py"]:
        text = path.read_text(encoding="utf-8")
        for word in banned:
            assert word not in text, (path.name, word)


def test_i3_no_pdf_tracked_and_librarian_folder_ignored():
    assert not list((REPO / "knowledge").rglob("*.pdf"))
    assert not list((REPO / "knowledge").rglob("*.txt"))
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "agent_librarian/" in ignore and "data/*" in ignore
    tracked = subprocess.run(["git", "ls-files", "*.pdf", "agent_librarian"], cwd=REPO,
                             capture_output=True, text=True, check=False)
    assert tracked.stdout.strip() == ""
