# Noticed-but-not-touching — #478 (toast notifications)

Captured during Station III build; the operator decides whether any row becomes an Issue.
Station VI resolves rows.

| ID | Candidate | Discovering station | Evidence | State | Linked issue |
|----|-----------|---------------------|----------|-------|--------------|
| N1 | `npx tsc --noEmit server.ts` fails on unmodified `server.ts:251` (import.meta needs module es2020+) | III | `server.ts:251`, CONSTRAINTS.md #478 | published | #506 |
