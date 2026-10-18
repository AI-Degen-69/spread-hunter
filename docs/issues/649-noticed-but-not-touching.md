# 649 — noticed but not touching

| Observation | Owner | When |
| --- | --- | --- |
| `core_brain/signer.py` cited by issue does not exist; creds live in `core_brain/venue.py:api_creds_from_env()` | planner (Station II) | when issue text is corrected |
| User-channel `trade`/`order` exact field names unconfirmed against live venue docs; parser skips+counts unknowns so only router map would change | builder (Station III) | this-round-only (adjust router if venue differs) |
| Partial-fill -> single-buy-rescue dispatch is Phase-3 wiring, needs registry first | planner (Station II) | when-X (Phase-3 issue) |
