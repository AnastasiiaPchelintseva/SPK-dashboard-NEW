# Project memory

## Product intent & audience

- Project: «Дашборд СПК» (SPK-dashboard-NEW) — dashboard for SPC (Статистический процесс контроля / Statistical Process Control) data.
- Likely audience: quality/engineering/manufacturing users reviewing SPC metrics.

## Brand personality & voice

- User communicates in Russian; assistant responses in Russian.

## Visual design & UX direction

- Main entry point: `dashboard.html` (single-page dashboard).
- No specific aesthetic details inferred yet.

## Decisions, constraints & rationale

- Project files from repository `SPK-dashboard-NEW` are present in the working directory, but `.git` is missing — directory is not a git repo.
- Environment has no network access to `github.com`; cannot clone or re-init git history.
- Tech stack: Python service under `spk/`, HTML templates, database, 1C source files, Excel source files.

## Open questions & assumptions

- User expected to see a different project or specific files/folders? Unresolved.
- User may want git history restored — not possible in current environment.
- Exact role of 1C and Excel files (data sources vs. exports) unconfirmed.
- Dashboard structure, metrics displayed, and UI framework not yet reviewed.