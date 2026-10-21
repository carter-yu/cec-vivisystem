# Knowledge tree: jeff-aia-medic (sanitized seed)

This directory holds the **redacted** knowledge tree for the `#jeff-aia-medic`
librarian (Phase 32, [ADR 0017](../../docs/decisions/0017-aia-medic-librarian.md)).
It was built offline from the AIA group medical Benefit Summary (4 pages) with
`python -m cec_vivisystem.medic_librarian build`.

- `tree.json` — schema `cec-medic-kb/v1`; read by the listener.
- `tree.md` — the same content for human review (check it against the PDF).

What it contains: the benefit schedule (names, in-network / out-of-network
amounts, conditions, page numbers), the numbered remarks and general notes.
What it never contains: the PDF, the raw extracted text, policy or member
numbers, HKID, email addresses or phone numbers. ID-like tokens are replaced by
`[ID-REDACTED]`-style placeholders before anything is written.

**Never add the PDF or raw text here.** Source PDFs stay in the gitignored
`agent_librarian/` folder on the Mini.

## Which tree the listener uses

1. `CEC_MEDIC_KB_DIR/tree.json` if `CEC_MEDIC_KB_DIR` is set, else
   `data/agent_librarian/jeff-aia-medic/tree.json` (gitignored), if it exists;
2. otherwise this seed.

The listener reloads the data-dir tree when its file changes; no restart needed.

## Rebuild (on the Mini)

```sh
# Option A: use this seed as the data tree
uv run python -m cec_vivisystem.medic_librarian install-seed

# Option B: build from the local PDF(s); identical copies are skipped
brew install poppler            # once, provides pdftotext
uv run python -m cec_vivisystem.medic_librarian build --pdf agent_librarian/Jeff-AIA-Medic/

# Option C: build from text extracted elsewhere with `pdftotext -layout`
uv run python -m cec_vivisystem.medic_librarian build --from-text /path/to/benefit-summary.txt

# Check retrieval without Slack or a model
uv run python -m cec_vivisystem.medic_librarian search "How much physiotherapy can I claim?"
```

When the full policy PDF arrives, pass it as another `--pdf` (or put it in the
same folder). Its pages without a benefit table become `policy` /
`exclusions` / `claims` / `definitions` prose chunks. Read the build warnings
and `tree.md` before relying on it.

To refresh this seed (maintainers only), build with
`--out knowledge/jeff-aia-medic`, review the diff and `tree.md`, and confirm
that no policy number appears before committing.
