# #649 todo — User Account WebSocket stream

- [ ] U1 (M): offline core — `core_brain/user_stream.py` creds refusal, dataclasses,
  `parse_frame`, callback registry + `tests/test_user_stream.py` offline cases.
  `Depends on:` none.
- [ ] U2 (M): lifecycle — auth frame, text heartbeat, backoff, stop, `--listen` probe.
  `Depends on:` U1.
- [ ] U3 (S): `user_ws_*` cycle-stream emission via injected `emit_fn`.
  `Depends on:` U1, U2.
- [ ] Regression: `tests/test_live_marks.py` + `tests/test_cycle_stream.py` stay green.
- [ ] Hands-on verify: dashboard stream card shows CONNECTED + AUTH_OK; `--listen` probe
  streams order/trade frames (operator-run).
