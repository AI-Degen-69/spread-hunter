# Backlog — Issue #867 (`redesign-dashboard-layout-and-theme`)

Out-of-scope observations noticed during Station III / Station IV. Only Station VI (`vi-close-pipeline`) resolves rows.

| ID | Candidate | Discovering Station | Evidence (`path:line`) | Owner | When | Status | Resolved-by |
| --- | --- | --- | --- | --- | --- | --- | --- |
| N1 | `@media (max-width: 768px)` still sets `.pill-label { font-size: 8px; }`, which shrinks header pill labels below the 11px desktop minimum on narrow mobile viewports. | Station IV (`web-design-guidelines`) | `dashboard/static/styles.css:1822` | you | Future mobile typography pass | published | #900 |
