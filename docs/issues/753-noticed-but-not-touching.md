# NOTICED-BUT-NOT-TOUCHING — #753 (retire the dead hold-lever surface)

Station III (Build). Found while deleting the hold surface this issue owns.
Untouched on purpose: each row is outside the plan's file list, so it is a
future-Issue candidate, not a silent side change.

| # | Candidate | Discovering station | Evidence | Status | Resolved by |
| --- | --- | --- | --- | --- | --- |
| N1 | `docs/explanations/prudent_profile_and_strategy_guide.html` still teaches the queue hold as a live tolerance check ("בדיקת סובלנות תור (Queue Hold & Deadband) … ההזמנה נשמרת ולא מבוטלת"). The behaviour it describes was retired by #387. A guide is not a run record, so the "it really happened" defence does not apply. | Station III (T4 sweep) | `docs/explanations/prudent_profile_and_strategy_guide.html:807-809` | open | — |
| N2 | The #492 cockpit arm cards keep their original `desc` strings ("Requote hold below 100 shares, queue hold 150 shares"), which read as current arm configuration now that neither setting exists. Only the HYBRID blueprint rows and the #485 tweak annotations were in this issue's scope. | Station III (T3) | `scripts/tournament_cockpit_server.py:141,442` | open | — |
