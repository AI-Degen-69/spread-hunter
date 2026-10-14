# Noticed-but-not-touching — Issue #505 (Station III build)

Out-of-scope findings spotted while building held-first rotation order. Rows stay `open`
until Station VI (`vi-close-pipeline`) dispositions them.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | The shadow smoke logged `single-leg lifecycle pass deferred: another executor is already mutating this registry (rotation sweep vs guardian overlap)` on 3 of 5 rotations — the single-leg management pass keeps losing the registry lock to the rotation sweep/guardian overlap and defers. Repeated deferral delays single-leg handling on a schedule nobody chose; worth measuring how often the pass actually runs vs defers over a long rehearsal, and whether the lock scope can shrink | III | `runtime/shadow-smoke505.jsonl` run `shadow-smoke505`, 2026-10-14 09:30-09:31; message text from the shadow lifecycle pass (`core_brain/single_buy_saver.py::manage_single_leg_positions` callers) | open | — |
