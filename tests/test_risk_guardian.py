import pytest
from unittest.mock import MagicMock
from core_brain.risk_guardian import detect_unhedged_positions, run_guardian_tick
from core_brain.quotes import Inventory

def test_detect_unhedged_positions(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    
    # 10 UP, 0 DOWN = unhedged
    mock_inv = Inventory()
    mock_inv.up_shares = 10.0
    mock_inv.down_shares = 0.0
    
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    unhedged = detect_unhedged_positions(registry)
    assert len(unhedged) == 1
    assert unhedged[0]["condition_id"] == "0x123"
    assert unhedged[0]["naked_shares"] == 10.0
    assert unhedged[0]["heavy_side"] == "UP"

def test_detect_unhedged_positions_balanced(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    
    # Balanced = 0 unhedged
    mock_inv = Inventory()
    mock_inv.up_shares = 10.0
    mock_inv.down_shares = 10.0
    
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    unhedged = detect_unhedged_positions(registry)
    assert len(unhedged) == 0

def test_run_guardian_tick_invokes_saver_when_unhedged(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    mock_inv = Inventory()
    mock_inv.up_shares = 5.0
    mock_inv.down_shares = 0.0
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    mock_saver = MagicMock(return_value=[{"action": "exited"}])
    res = run_guardian_tick(MagicMock(), registry, MagicMock(), single_buy_saver_fn=mock_saver)
    assert len(res) == 1
    assert mock_saver.called


# ── T2 — OrphanTriggerBoard & build_watches (#699) ─────────────────────────


class _FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_trigger_board_safe_band_100_frames_no_wake_or_io():
    import threading
    from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

    wake = threading.Event()
    clock = _FakeClock()
    board = OrphanTriggerBoard(wake, clock=clock)
    gen = board.next_generation()
    board.publish(
        {
            "tok_up": Watch(
                condition_id="0x123",
                kind="stop",
                level=0.45,
                source="strategy",
                token_id="tok_up",
            ),
            "tok_dn": Watch(
                condition_id="0x123",
                kind="hedge",
                level=0.47,
                source="stranded_hedge",
                token_id="tok_dn",
            ),
        },
        generation=gen,
    )

    client = MagicMock()
    registry = MagicMock()
    for i in range(100):
        clock.now += 0.01
        board.observe({"tok_up": (0.46 + (i % 3) * 0.01, 0.52), "tok_dn": (0.44, 0.48)})

    assert not wake.is_set()
    assert board.pop_triggers() == []
    assert client.mock_calls == []
    assert registry.mock_calls == []


def test_trigger_board_stop_edge_cases():
    import threading
    from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

    wake = threading.Event()
    clock = _FakeClock()
    board = OrphanTriggerBoard(wake, clock=clock)
    board.publish(
        {"tok_up": Watch(condition_id="0x123", kind="stop", level=0.45, source="strategy", token_id="tok_up")},
        generation=board.next_generation(),
    )

    # None bid never fires
    board.observe({"tok_up": (None, 0.50)})
    assert not wake.is_set()

    # One tick above level (0.46 > 0.45) does not fire
    board.observe({"tok_up": (0.46, 0.50)})
    assert not wake.is_set()

    # Bid equal to level (0.45 <= 0.45) fires
    board.observe({"tok_up": (0.45, 0.50)})
    assert wake.is_set()
    triggers = board.pop_triggers()
    assert len(triggers) == 1
    assert triggers[0]["condition_id"] == "0x123"
    assert triggers[0]["token_id"] == "tok_up"
    assert triggers[0]["kind"] == "stop"
    assert triggers[0]["source"] == "strategy"
    assert triggers[0]["level"] == 0.45
    assert triggers[0]["price"] == 0.45


def test_trigger_board_hedge_edge_cases():
    import threading
    from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

    wake = threading.Event()
    clock = _FakeClock()
    board = OrphanTriggerBoard(wake, clock=clock)
    board.publish(
        {"tok_dn": Watch(condition_id="0x123", kind="hedge", level=0.47, source="stranded_hedge", token_id="tok_dn")},
        generation=board.next_generation(),
    )

    # None ask and 0 ask never fire
    board.observe({"tok_dn": (0.40, None)})
    board.observe({"tok_dn": (0.40, 0.0)})
    assert not wake.is_set()

    # Ask equal to level (0.47 == 0.47) does not fire (strict < max_pair_cost)
    board.observe({"tok_dn": (0.40, 0.47)})
    assert not wake.is_set()

    # Ask below level (0.46 < 0.47) fires
    board.observe({"tok_dn": (0.40, 0.46)})
    assert wake.is_set()
    triggers = board.pop_triggers()
    assert len(triggers) == 1
    assert triggers[0]["kind"] == "hedge"
    assert triggers[0]["price"] == 0.46


def test_trigger_board_mute_coalesces_burst_and_expires_at_2s():
    import threading
    from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

    wake = threading.Event()
    clock = _FakeClock(1000.0)
    board = OrphanTriggerBoard(wake, clock=clock)
    board.publish(
        {"tok_up": Watch(condition_id="0x123", kind="stop", level=0.45, source="strategy", token_id="tok_up")},
        generation=board.next_generation(),
    )

    # 50 crossing frames at t=1000.0 -> wake set once, 1 trigger recorded
    for _ in range(50):
        board.observe({"tok_up": (0.44, 0.50)})
    assert wake.is_set()
    assert len(board.pop_triggers()) == 1

    wake.clear()
    # At +1.9s -> still muted
    clock.now = 1001.9
    for _ in range(10):
        board.observe({"tok_up": (0.43, 0.50)})
    assert not wake.is_set()
    assert board.pop_triggers() == []

    # At +2.0s -> mute expired, fires again
    clock.now = 1002.0
    board.observe({"tok_up": (0.43, 0.50)})
    assert wake.is_set()
    assert len(board.pop_triggers()) == 1


def test_trigger_board_publish_clears_dropped_mute_and_ignores_stale_generation():
    import threading
    from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

    wake = threading.Event()
    clock = _FakeClock(1000.0)
    board = OrphanTriggerBoard(wake, clock=clock)
    watch = Watch(condition_id="0x123", kind="stop", level=0.45, source="strategy", token_id="tok_up")

    gen1 = board.next_generation()
    gen2 = board.next_generation()
    board.publish({"tok_up": watch}, generation=gen2)

    # Older generation is ignored
    board.publish({}, generation=gen1)
    assert "tok_up" in board.watches()

    # Trigger mute on 0x123
    board.observe({"tok_up": (0.45, 0.50)})
    assert wake.is_set()
    assert len(board.pop_triggers()) == 1
    assert board.pop_triggers() == []
    wake.clear()

    # Publish without 0x123 clears its mute; re-publishing 0x123 before 2.0s allows immediate fire
    board.publish({}, generation=board.next_generation())
    clock.now = 1000.5
    board.publish({"tok_up": watch}, generation=board.next_generation())
    board.observe({"tok_up": (0.45, 0.50)})
    assert wake.is_set()


def test_build_watches_stop_level_and_average_fill_shift(monkeypatch):
    from types import SimpleNamespace
    from core_brain.risk_guardian import build_watches
    from core_brain.single_leg_lifecycle import stop_trigger

    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up", "ts": 10.0},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn", "ts": 10.0},
    ]

    inv = Inventory(up_shares=10.0, down_shares=0.0, up_cost=5.20, down_cost=0.0)
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: inv,
    )

    cfg = SimpleNamespace(
        bot_risk_stop_mode="ABSOLUTE_DROP",
        bot_risk_stop_adverse_drop_usd=0.05,
        bot_risk_stop_max_drawdown_pct=10.0,
        bot_risk_circuit_max_drawdown_pct=75.0,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_stranded_completion_on=False,
    )

    watches = build_watches(registry, cfg)
    expected_level, expected_source = stop_trigger(0.52, "ABSOLUTE_DROP", 0.05, 10.0, 75.0)
    assert set(watches) == {"tok_up"}
    assert watches["tok_up"].level == expected_level
    assert watches["tok_up"].source == expected_source
    assert watches["tok_up"].kind == "stop"

    # Second fill changes average from 0.52 to 0.48 (20 shares, $9.60 total cost)
    inv.up_shares = 20.0
    inv.up_cost = 9.60
    watches2 = build_watches(registry, cfg)
    expected_level_2, _ = stop_trigger(0.48, "ABSOLUTE_DROP", 0.05, 10.0, 75.0)
    assert watches2["tok_up"].level == expected_level_2
    assert watches2["tok_up"].level != expected_level


def test_build_watches_exclusions_and_optional_hedge_watch(monkeypatch):
    from types import SimpleNamespace
    from core_brain.risk_guardian import build_watches

    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up", "ts": 10.0},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn", "ts": 10.0},
    ]

    # 1. Balanced inventory -> no watch
    inv_balanced = Inventory(up_shares=10.0, down_shares=10.0, up_cost=4.80, down_cost=4.80)
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: inv_balanced,
    )
    cfg = SimpleNamespace(
        bot_risk_stop_mode="ABSOLUTE_DROP",
        bot_risk_stop_adverse_drop_usd=0.05,
        bot_risk_stop_max_drawdown_pct=10.0,
        bot_risk_circuit_max_drawdown_pct=75.0,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_stranded_completion_on=True,
    )
    assert build_watches(registry, cfg) == {}

    # 2. Unpriced held fill (up_cost == 0) -> no watch
    inv_unpriced = Inventory(up_shares=10.0, down_shares=0.0, up_cost=0.0, down_cost=0.0)
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: inv_unpriced,
    )
    assert build_watches(registry, cfg) == {}

    # 3. Priced held fill with stranded_completion_enabled=True -> both stop and hedge watches
    inv_unhedged = Inventory(up_shares=10.0, down_shares=0.0, up_cost=5.20, down_cost=0.0)
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: inv_unhedged,
    )
    watches_with_hedge = build_watches(registry, cfg)
    assert set(watches_with_hedge) == {"tok_up", "tok_dn"}
    assert watches_with_hedge["tok_dn"].kind == "hedge"
    assert watches_with_hedge["tok_dn"].level == 0.47

    # 4. With stranded_completion_enabled=False -> stop watch only
    cfg.bot_risk_stranded_completion_on = False
    watches_no_hedge = build_watches(registry, cfg)
    assert set(watches_no_hedge) == {"tok_up"}


def test_run_guardian_tick_publishes_empty_when_nothing_unhedged(monkeypatch):
    import threading
    from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

    wake = threading.Event()
    board = OrphanTriggerBoard(wake)
    board.publish(
        {"tok_up": Watch(condition_id="0x123", kind="stop", level=0.45, source="strategy", token_id="tok_up")},
        generation=board.next_generation(),
    )

    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = []
    client = MagicMock()

    res = run_guardian_tick(client, registry, MagicMock(), trigger_board=board)
    assert res == []
    assert board.watches() == {}
    assert client.mock_calls == []



# ── #646 N5 — the guardian pass needs a venue view ─────────────────────────

def _unhedged_registry(monkeypatch, *, up=5.0, down=0.0):
    registry = MagicMock()
    registry.get_all_quotes.side_effect = AssertionError(
        "hot path must not full-scan quotes")
    registry.get_latest_tokens_by_condition_side.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    mock_inv = Inventory()
    mock_inv.up_shares = up
    mock_inv.down_shares = down
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    return registry


def test_the_default_saver_path_is_handed_the_funder(monkeypatch):
    """Without it the pass sizes exits off fills a close already retired."""
    import core_brain.single_buy_saver as sbs

    seen: dict = {}

    def fake_saver(client, registry, cfg, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sbs, "manage_single_leg_positions", fake_saver)
    registry = _unhedged_registry(monkeypatch)

    run_guardian_tick(MagicMock(), registry, MagicMock(), live=True,
                      funder="0xfunder")

    assert seen["funder"] == "0xfunder"


def test_the_funder_falls_back_to_the_same_env_var_the_loop_uses(monkeypatch):
    """A guardian started without the parameter still gets a view."""
    import core_brain.single_buy_saver as sbs

    seen: dict = {}

    def fake_saver(client, registry, cfg, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sbs, "manage_single_leg_positions", fake_saver)
    monkeypatch.setenv("POLY_FUNDER", "0xfromenv")
    registry = _unhedged_registry(monkeypatch)

    run_guardian_tick(MagicMock(), registry, MagicMock(), live=True)

    assert seen["funder"] == "0xfromenv"


def test_no_funder_anywhere_passes_none_rather_than_guessing(monkeypatch):
    import core_brain.single_buy_saver as sbs

    seen: dict = {}

    def fake_saver(client, registry, cfg, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sbs, "manage_single_leg_positions", fake_saver)
    monkeypatch.delenv("POLY_FUNDER", raising=False)
    registry = _unhedged_registry(monkeypatch)

    run_guardian_tick(MagicMock(), registry, MagicMock(), live=True)

    assert seen["funder"] is None
