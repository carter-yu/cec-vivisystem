# PR prompt archive

Keep one Markdown prompt archive per PR, named `pr-NNNN-short-slug.md`.
The archive number is a local sequence, not a claim about the GitHub PR number.
Record the manager request and any material follow-up constraints in that file;
avoid multiple competing prompt files for one PR. This directory records
engineering requests. Runtime model prompts remain in `src/cec_vivisystem/prompts/`.

Each archive starts with metadata: date and timezone, branch, model, goal,
constraints, and acceptance criteria. Use engineering English. Any quoted Chinese
must be Traditional Chinese only. Review and sanitize before committing: remove
secrets, credential values, private paths, family records, live message text,
incident logs, account identifiers, and unrelated repository content. Use clearly
synthetic placeholders where needed; label substantive redactions.

Archive instructions as context, never as a new authority over current user
orders or repository contracts. Record implementation results in PROGRESS and the
PR, not as invented success claims inside the original request.

These rules follow the manager's requested one-file-per-PR, secret-free, sanitized
archive convention. The referenced sibling repository was not accessed or changed.
