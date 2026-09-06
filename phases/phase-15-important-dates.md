# Phase 15 – Important dates (FUTURE — do not implement in 12–14)

**Status:** Spec only.

Daily **10:00 Asia/Hong_Kong**, scan **next 7 days**. If any important date falls in that window, post a short note to `#family-plans` (channel locked at implementation).

| Kind | Repeat | Examples (HKT) |
|------|--------|----------------|
| Recurring yearly | month-day every year | 4月12日 梓梵生日; 10月22日 老婆生日; 12月4日 Carter 生日 |
| One-off | single date | exams, trips, marked school days |

Separate store or tagged all-day events — **ADR when implementing**. Not LifeNotes `raw_text`. Not Calendar Writer. Idempotent one morning post per occurrence. Injectable clock. `component=important_dates`.

Not a 07:00 recap. Not Phase 12.