# 719 — noticed but not touching (Station III, 2026-10-20)

#719 swept the two showcases that sat outside `docs/issues/` and made
`tests/test_docs_layout.py` scan every folder under `docs/`, so a subfolder can
no longer hide one. Nothing below was changed by it; each row is outside that
scope.

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | `tests/test_project_name.py` still allows `pathlib.Path("docs/presentations")` in `ALLOW_DIRS`, but that folder no longer exists — deleting its only file emptied it and git tracks no empty directory. | It is a project-name allow-list, not a showcase. A dead entry is harmless (the walk simply never reaches it), and editing a second guard is not this ticket's work. | you | with the next `tests/test_project_name.py` change | open |
