"""Tests for core_brain/user_stream.py (User Account WebSocket stream)."""

import dataclasses
import json
from unittest.mock import MagicMock

import pytest

from core_brain import user_stream


def test_constants():
    assert user_stream.USER_WS_URL == "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    assert user_stream.PING_TEXT == "PING"
    assert user_stream.PING_INTERVAL_SEC == 10.0
    assert user_stream.PONG_MISS_LIMIT == 3
    assert user_stream.RECONNECT_MIN_SEC == 1.0
    assert user_stream.RECONNECT_MAX_SEC == 30.0
    assert user_stream.FINAL_TRADE_STATUSES == {"CONFIRMED", "FAILED"}
    assert user_stream.SERVICE == "user_stream"


def test_missing_credentials_raise_and_name_vars(monkeypatch):
    monkeypatch.delenv("POLY_API_KEY", raising=False)
    monkeypatch.delenv("POLY_API_SECRET", raising=False)
    monkeypatch.delenv("POLY_API_PASSPHRASE", raising=False)
    with pytest.raises(user_stream.UserStreamCredentialsMissing) as exc_info:
        user_stream.get_user_stream_creds()
    msg = str(exc_info.value)
    assert "POLY_API_KEY" in msg
    assert "POLY_API_SECRET" in msg
    assert "POLY_API_PASSPHRASE" in msg

    monkeypatch.setenv("POLY_API_KEY", "key123")
    monkeypatch.setenv("POLY_API_SECRET", "secret456")
    monkeypatch.delenv("POLY_API_PASSPHRASE", raising=False)
    with pytest.raises(user_stream.UserStreamCredentialsMissing) as exc_info2:
        user_stream.get_user_stream_creds()
    msg2 = str(exc_info2.value)
    assert "POLY_API_PASSPHRASE" in msg2
    assert "key123" not in msg2
    assert "secret456" not in msg2


def test_valid_credentials_and_wallet_label(monkeypatch):
    monkeypatch.setenv("POLY_API_KEY", "mock-key")
    monkeypatch.setenv("POLY_API_SECRET", "mock-secret")
    monkeypatch.setenv("POLY_API_PASSPHRASE", "mock-passphrase")
    monkeypatch.setenv("POLY_FUNDER", "0xmockwallet")

    creds, wallet = user_stream.get_user_stream_creds()
    assert creds.api_key == "mock-key"
    assert creds.api_secret == "mock-secret"
    assert creds.api_passphrase == "mock-passphrase"
    assert wallet == "0xmockwallet"

    monkeypatch.delenv("POLY_FUNDER", raising=False)
    _, wallet_default = user_stream.get_user_stream_creds()
    assert wallet_default == "unknown"


def test_dataclasses_frozen_and_types():
    fill = user_stream.MakerOrderFill(
        order_id="ord-fill-1",
        price=0.45,
        size=100.0,
        side="BUY",
    )
    assert fill.order_id == "ord-fill-1"
    assert fill.price == 0.45
    assert fill.size == 100.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        fill.price = 0.50  # type: ignore[misc]

    trade = user_stream.UserTradeEvent(
        trade_id="tr-1",
        order_id="ord-1",
        condition_id="0xabc",
        token_id="tok-1",
        side="BUY",
        price=0.48,
        size=50.0,
        status="CONFIRMED",
        fills=(fill,),
    )
    assert trade.trade_id == "tr-1"
    assert trade.status in user_stream.FINAL_TRADE_STATUSES
    with pytest.raises(dataclasses.FrozenInstanceError):
        trade.status = "FAILED"  # type: ignore[misc]

    order = user_stream.UserOrderEvent(
        order_id="ord-2",
        condition_id="0xdef",
        token_id="tok-2",
        side="SELL",
        price=0.52,
        size=25.0,
        status="LIVE",
    )
    assert order.order_id == "ord-2"
    with pytest.raises(dataclasses.FrozenInstanceError):
        order.size = 30.0  # type: ignore[misc]


def test_parse_frame_trade_and_order():
    trade_json = """{
        "event_type": "trade",
        "id": "tr-100",
        "taker_order_id": "ord-taker-1",
        "market": "0xcondition123",
        "asset_id": "token-xyz",
        "side": "BUY",
        "price": "0.495",
        "size": "150.5",
        "status": "CONFIRMED",
        "maker_orders": [
            {"order_id": "m-ord-1", "price": "0.495", "size": "100", "side": "SELL"},
            {"order_id": "m-ord-2", "price": "0.495", "size": "50.5", "side": "SELL"}
        ]
    }"""
    events, pongs, skipped = user_stream.parse_frame(trade_json)
    assert pongs == 0
    assert skipped == 0
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, user_stream.UserTradeEvent)
    assert ev.trade_id == "tr-100"
    assert ev.order_id == "ord-taker-1"
    assert ev.condition_id == "0xcondition123"
    assert ev.token_id == "token-xyz"
    assert ev.side == "BUY"
    assert ev.price == 0.495
    assert ev.size == 150.5
    assert ev.status == "CONFIRMED"
    assert len(ev.fills) == 2
    assert ev.fills[0].order_id == "m-ord-1"
    assert ev.fills[0].size == 100.0

    order_json = """{
        "event_type": "order",
        "id": "ord-200",
        "market": "0xcond456",
        "asset_id": "token-abc",
        "side": "SELL",
        "price": "0.51",
        "original_size": "200.0",
        "status": "MATCHED"
    }"""
    events2, _, _ = user_stream.parse_frame(order_json)
    assert len(events2) == 1
    ord_ev = events2[0]
    assert isinstance(ord_ev, user_stream.UserOrderEvent)
    assert ord_ev.order_id == "ord-200"
    assert ord_ev.condition_id == "0xcond456"
    assert ord_ev.token_id == "token-abc"
    assert ord_ev.price == 0.51
    assert ord_ev.size == 200.0
    assert ord_ev.status == "MATCHED"


def test_parse_frame_list_and_pongs():
    events, pongs, skipped = user_stream.parse_frame("PONG")
    assert events == []
    assert pongs == 1
    assert skipped == 0

    mixed_json = """[
        {"event_type": "order", "id": "o1", "market": "c1", "asset_id": "t1", "side": "BUY", "price": "0.40", "size": "10", "status": "OPEN"},
        {"event_type": "unknown_event", "foo": "bar"},
        {"event_type": "trade", "id": "t1", "order_id": "o2", "market": "c2", "asset_id": "t2", "side": "SELL", "price": "0.60", "size": "20", "status": "CONFIRMED"}
    ]"""
    events2, _, skipped2 = user_stream.parse_frame(mixed_json)
    assert len(events2) == 2
    assert isinstance(events2[0], user_stream.UserOrderEvent)
    assert isinstance(events2[1], user_stream.UserTradeEvent)
    assert skipped2 == 1


def test_parse_frame_invalid_skips():
    events, pongs, skipped = user_stream.parse_frame("not a json payload")
    assert events == []
    assert pongs == 0
    assert skipped == 1

    bad_price = """{"event_type": "order", "id": "o1", "market": "c1", "asset_id": "t1", "side": "BUY", "price": "invalid", "size": "10", "status": "OPEN"}"""
    events2, _, skipped2 = user_stream.parse_frame(bad_price)
    assert events2 == []
    assert skipped2 == 1


def test_callback_registry_and_unsubscribe():
    client = user_stream.UserStreamClient()

    trade_calls = []
    order_calls = []
    state_calls = []

    def handle_trade(ev):
        trade_calls.append(ev)

    def handle_order(ev):
        order_calls.append(ev)

    def handle_state(state, details):
        state_calls.append((state, details))

    unsub_trade = client.on_trade(handle_trade)
    unsub_order = client.on_order(handle_order)
    unsub_state = client.on_state(handle_state)

    trade_ev = user_stream.UserTradeEvent(
        trade_id="t1", order_id="o1", condition_id="c1", token_id="tok1",
        side="BUY", price=0.45, size=10.0, status="CONFIRMED", fills=()
    )
    order_ev = user_stream.UserOrderEvent(
        order_id="o1", condition_id="c1", token_id="tok1",
        side="BUY", price=0.45, size=10.0, status="LIVE"
    )

    client._dispatch_event(trade_ev)
    client._dispatch_event(order_ev)
    client._dispatch_state("CONNECTED", "ok")

    assert len(trade_calls) == 1
    assert trade_calls[0] == trade_ev
    assert len(order_calls) == 1
    assert order_calls[0] == order_ev
    assert len(state_calls) == 1
    assert state_calls[0] == ("CONNECTED", "ok")

    unsub_trade()
    unsub_order()
    unsub_state()

    client._dispatch_event(trade_ev)
    client._dispatch_event(order_ev)
    client._dispatch_state("DISCONNECTED", "closed")

    assert len(trade_calls) == 1
    assert len(order_calls) == 1
    assert len(state_calls) == 1


def test_callback_isolation_on_error():
    client = user_stream.UserStreamClient()
    good_calls = []

    def bad_callback(ev):
        raise RuntimeError("boom")

    def good_callback(ev):
        good_calls.append(ev)

    client.on_trade(bad_callback)
    client.on_trade(good_callback)

    trade_ev = user_stream.UserTradeEvent(
        trade_id="t1", order_id="o1", condition_id="c1", token_id="tok1",
        side="BUY", price=0.45, size=10.0, status="CONFIRMED", fills=()
    )
    client._dispatch_event(trade_ev)
    assert len(good_calls) == 1


def test_client_start_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("POLY_API_KEY", raising=False)
    client = user_stream.UserStreamClient()
    with pytest.raises(user_stream.UserStreamCredentialsMissing):
        client.start()


def test_on_open_sends_auth_frame_and_emits():
    captured_emits = []

    def fake_emit(cycle, phase, action, *, service, reason="", extra=None, can_rotate=False):
        captured_emits.append({
            "action": action,
            "service": service,
            "reason": reason,
            "extra": extra,
            "can_rotate": can_rotate,
        })

    client = user_stream.UserStreamClient(emit_fn=fake_emit)
    from py_clob_client_v2.clob_types import ApiCreds
    client._creds = ApiCreds(api_key="k1", api_secret="s1", api_passphrase="p1")
    client._wallet = "0xwallet123"

    mock_app = MagicMock()

    client._on_open(mock_app)
    assert client.connected is True
    assert mock_app.send.call_count == 1
    sent_payload = json.loads(mock_app.send.call_args[0][0])
    assert sent_payload["type"] == "user"
    assert sent_payload["auth"] == {"apiKey": "k1", "secret": "s1", "passphrase": "p1"}
    assert "markets" not in sent_payload

    actions = [e["action"] for e in captured_emits]
    assert "user_ws_connected" in actions
    assert "user_ws_auth_sent" in actions
    for e in captured_emits:
        assert e["service"] == "user_stream"
        assert e["can_rotate"] is False

    mock_app.reset_mock()
    captured_emits.clear()
    client._markets = {"0xcond1", "0xcond2"}
    client._on_open(mock_app)
    sent_payload2 = json.loads(mock_app.send.call_args[0][0])
    assert sent_payload2["markets"] == ["0xcond1", "0xcond2"]


def test_on_message_inferred_auth_ok_and_resets_backoff():
    captured_emits = []

    def fake_emit(cycle, phase, action, *, service, reason="", extra=None, can_rotate=False):
        captured_emits.append(action)

    client = user_stream.UserStreamClient(emit_fn=fake_emit)
    client._wallet = "0xwallet999"
    client._backoff = 16.0
    client._connected = True
    assert client.auth_ok is False

    mock_app = MagicMock()

    client._on_message(mock_app, "PONG")
    assert client.auth_ok is True
    assert client._backoff == user_stream.RECONNECT_MIN_SEC
    assert "user_ws_auth_ok" in captured_emits

    captured_emits.clear()
    client._on_message(mock_app, "PONG")
    assert "user_ws_auth_ok" not in captured_emits


def test_on_message_dispatches_events_and_cycle_stream():
    emitted = []

    def fake_emit(cycle, phase, action, *, service, reason="", extra=None, can_rotate=False):
        emitted.append((action, extra))

    client = user_stream.UserStreamClient(emit_fn=fake_emit)
    client._wallet = "0xwallet"
    client._connected = True
    client._auth_ok = True

    trades_received = []
    orders_received = []
    client.on_trade(lambda t: trades_received.append(t))
    client.on_order(lambda o: orders_received.append(o))

    raw_frame = json.dumps([
        {
            "event_type": "trade",
            "id": "t-1",
            "taker_order_id": "o-1",
            "market": "c-1",
            "asset_id": "tok-1",
            "side": "BUY",
            "price": "0.45",
            "size": "50",
            "status": "CONFIRMED",
        },
        {
            "event_type": "order",
            "id": "o-2",
            "market": "c-1",
            "asset_id": "tok-2",
            "side": "SELL",
            "price": "0.55",
            "size": "50",
            "status": "OPEN",
        },
    ])

    mock_app = MagicMock()
    client._on_message(mock_app, raw_frame)

    assert len(trades_received) == 1
    assert trades_received[0].trade_id == "t-1"
    assert len(orders_received) == 1
    assert orders_received[0].order_id == "o-2"

    actions = [item[0] for item in emitted]
    assert "user_ws_trade" in actions
    assert "user_ws_order" in actions

    trade_extra = next(extra for action, extra in emitted if action == "user_ws_trade")
    assert trade_extra["trade_id"] == "t-1"
    assert trade_extra["order_id"] == "o-1"
    assert trade_extra["price"] == 0.45
    assert trade_extra["status"] == "CONFIRMED"


def test_heartbeat_tick_sends_ping_and_disconnects_on_missed_pongs():
    captured_emits = []

    def fake_emit(cycle, phase, action, *, service, reason="", extra=None, can_rotate=False):
        captured_emits.append(action)

    client = user_stream.UserStreamClient(emit_fn=fake_emit)
    mock_app = MagicMock()
    client._app = mock_app
    client._connected = True

    client._heartbeat_tick()
    assert mock_app.send.call_count == 1
    assert mock_app.send.call_args[0][0] == "PING"
    assert client._pongs_missed == 1

    client._heartbeat_tick()
    client._heartbeat_tick()
    assert client._pongs_missed == 3
    assert mock_app.close.call_count == 0

    client._heartbeat_tick()
    assert mock_app.close.call_count == 1
    assert "user_ws_error" in captured_emits


def test_on_close_and_reconnect_backoff_progression():
    captured_emits = []

    def fake_emit(cycle, phase, action, *, service, reason="", extra=None, can_rotate=False):
        captured_emits.append((action, extra))

    client = user_stream.UserStreamClient(emit_fn=fake_emit)
    client._connected = True
    client._auth_ok = True
    mock_app = MagicMock()

    client._on_close(mock_app, code=1006, reason="connection reset")
    assert client.connected is False
    assert client.auth_ok is False
    assert client._close_code == 1006
    assert client._close_reason == "connection reset"

    actions = [item[0] for item in captured_emits]
    assert "user_ws_disconnected" in actions

    assert client._next_backoff() == 1.0
    assert client._next_backoff() == 2.0
    assert client._next_backoff() == 4.0
    assert client._next_backoff() == 8.0
    assert client._next_backoff() == 16.0
    assert client._next_backoff() == 30.0
    assert client._next_backoff() == 30.0


def test_stop_joins_threads_and_emits_stopped():
    captured_emits = []

    def fake_emit(cycle, phase, action, *, service, reason="", extra=None, can_rotate=False):
        captured_emits.append(action)

    client = user_stream.UserStreamClient(emit_fn=fake_emit)
    mock_app = MagicMock()
    client._app = mock_app
    client._connected = True

    client.stop()
    assert client.connected is False
    assert mock_app.close.call_count == 1
    assert "user_ws_stopped" in captured_emits
