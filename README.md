# cec-vivisystem

A living family vivisystem for Carter, Elaine, and Cedric.

Built according to the principles in Kevin Kelly’s *Out of Control*:
bottom-up growth, decentralization, hive mind, and resilience through simple interacting components.

This is not a single monolithic AI agent.  
It is a small swarm of focused, replaceable parts that together serve the family.

## Language Policy
- **User interaction**: Cantonese + English
- **Code, documentation, design, comments, commits**: English only

## Current Status
See [PROGRESS.md](PROGRESS.md) — Phase 0–18 done. `#family-plans`: create → yes/no → Google create; same confirmation is created **once** (second yes → already added). Overlapping events add a bilingual **撞期** warning but still require yes. Parser aliases: 游水→游泳, MS Wong→Miss Wong 堂, 梓梵→Cedric, 椰子糖/糖糖/Lady Coco→Coco. Phase 10 titles: 公園, playgroup, 游水班→游泳, 體能班, 手作, 商場, 生日會, 打針. Phase 11: 聽朝 → tomorrow morning. Phase 17: 今晚 / 今日 create, `2:30`, 加活動, 物理治療, 洗耳仔. List queries (`今日有乜？`, `聽日有乜嘢活動`, `今個星期有乜`, `今個月有乜`, date range) **always reply**. Important dates: `4月12日 梓梵生日` / `4月21日 椰子糖生日` store immediately; `重要日子` / `有咩生日` lists them; `help` shows how. A 07:00 HKT morning recap posts today’s events; a 10:00 job notes important dates in the next 7 days and pings when the Google Testing refresh token expires in 3/2/1 days. Pytest uses a fake Google client, fake Slack poster, and Fake LLM. Live Mini may set `XAI_API_KEY` so a create-looking miss becomes a proposal (yes still required). No LLM on known phrases.

## Phases
- [Phase 0 – Environment & Foundations](phases/phase-0-environment.md) (done)
- [Phase 1 – Natural Language Parser](phases/phase-1-parser.md) (done)
- [Phase 2 – Slack Listener](phases/phase-2-listener.md) (done)
- [Phase 3 – Rule Parser Expansion](phases/phase-3-parser-expansion.md) (done)
- [Phase 4 – Confirmation Path](phases/phase-4-confirmation.md) (done — offline core)
- [Phase 4b – Slack confirmation](phases/phase-4b-slack-confirmation.md) (done — `#family-plans` proposal + thread yes/no)
- [Phase 5A – LifeNotesKeeper](phases/phase-5a-life-notes-keeper.md) (done — Option A raw capture)
- [Phase 5B – Slack wiring for LifeNotesKeeper](phases/phase-5b-life-notes-slack.md) (done — `#family-life-notes` dispatch)
- [Phase 6 – Calendar Writer](phases/phase-6-calendar-writer.md) (done — create-only, accepted confirmations)
- [Phase 7 – Calendar Reader](phases/phase-7-calendar-reader.md) (done — list/summary for a day)
- [Phase 8 – Overlap / same-person warn](phases/phase-8-overlap-warn.md) (done — warn on create proposal; no hard-block)
- [Phase 9 – Parser family aliases](phases/phase-9-parser-aliases.md) (done — 梓梵 / 游水 / MS Wong)
- [Phase 10 – Parser family titles](phases/phase-10-parser-titles.md) (done — 公園 / playgroup / 游水班 / 體能班 / 手作 / 商場 / 生日會 / 打針)
- [Phase 11 – Parser 聽朝](phases/phase-11-ting-chiu.md) (done — 聽朝 → tomorrow morning; 梓梵 → Cedric)
- [Phase 12 – Morning today-recap](phases/phase-12-morning-recap.md) (done — 07:00 today-list + list must-reply)
- [Phase 13 – Conflict-before-create + idempotent Writer](phases/phase-13-conflict-and-idempotent-write.md) (done — bilingual 撞期 warn; one create per confirmation)
- [Phase 14 – Period recap](phases/phase-14-period-recap.md) (done — 今日 / week / month / date range; day-grouped recap)
- [Phase 15 – Important dates](phases/phase-15-important-dates.md) (done — add/view + 10:00 next-7-days; not Calendar Writer)
- [Phase 16 – Google token reminder](phases/phase-16-google-token-reminder.md) (done — 10:00 Slack ping 3/2/1 days before Testing refresh expiry)
- [Phase 17 – Parser 今晚 / 今日 create + pet Coco](phases/phase-17-parser-tonight-pet.md) (done — live 2026-09-14 create phrases)
- [Phase 18 – Hybrid parse fallback](phases/phase-18-hybrid-parse-fallback.md) (done — LLM only when rules miss a create-looking line; miss store for keyword promotion)

## Quick Start
See [phases/phase-0-environment.md](phases/phase-0-environment.md) and [phases/phase-1-parser.md](phases/phase-1-parser.md)

```bash
uv sync
uv run pytest
uv run ruff check .
uv run python -c "from cec_vivisystem.hello import main; main()"
uv run python -c "from cec_vivisystem.parser import main; main()"
# Live Slack Socket Mode (requires local .env — never commit secrets):
# uv run python -c "from cec_vivisystem.listener import main; main()"
# Morning today-recap (one post per HKT date; fake poster in pytest):
# uv run python -c "from cec_vivisystem.morning_recap import main; main()"
# Important-dates 10:00 review (one post per occurrence in the next 7 days)
# + Google token 3/2/1-day expiry ping:
# uv run python -c "from cec_vivisystem.important_dates import main; main()"
# Token ping only:
# uv run python -c "from cec_vivisystem.google_token_reminder import main; main()"
# Parse-miss keyword counts (promote into rules):
# uv run python -c "from cec_vivisystem.parse_misses import main; main()"
# Logs: stdout + logs/{component}-YYYY-MM-DD.log (archive/purge on start; never commit)
```

### Operator: 07:00 Asia/Hong_Kong (Mini)

launchd **plist on the Mini is operator stretch**. The command to schedule:

```bash
# WorkingDirectory = this repo (so .env loads). Homebrew uv on PATH.
uv run python -c "from cec_vivisystem.morning_recap import main; main()"
```

Example launchd `StartCalendarInterval`: Hour `7`, Minute `0`, with the Mini’s time zone `Asia/Hong_Kong`. `ProgramArguments` should use the Homebrew `uv` path (see `my-notes/fix-launchd-uv-path.md` locally). Empty days still post. A second run the same calendar date is skipped.

### Operator: 10:00 Asia/Hong_Kong (Mini)

launchd **plist on the Mini is operator stretch**. The command to schedule:

```bash
uv run python -c "from cec_vivisystem.important_dates import main; main()"
```

Example launchd `StartCalendarInterval`: Hour `10`, Minute `0`. Posts only when a stored important date falls in the next 7 HKT days and that occurrence is not yet posted. Empty windows do not post. The same command also posts `Google calendar will be expired in N days. Please refresh` when remaining Testing-token days are 3, 2, or 1 (`GOOGLE_REFRESH_TOKEN_ISSUED_AT` in `.env`; one post per HKT date).

## Core Documents
- [Philosophy](docs/philosophy.md)
- [Ground Rules](docs/ground-rules.md) — binding for all phases (1–14; teaching comments for LLM learning; no LLM by default)
- [Architecture](docs/architecture.md) — includes cross-cutting quality bars
- [Resilience](docs/resilience.md)
- [Unit testing standard](docs/unit-testing.md) — every component / phase
- [Logging & data retention](docs/logging-and-retention.md) — every component / phase

Phase docs may narrow **scope**; they may **not** waive unit tests or logging/retention.
