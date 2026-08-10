"""The book/tape fetchers moved out of the deleted strategy/main.py (issue
#14). These tests pin the new homes: importable from the market-data module,
and the sweep module re-exports the very same functions -- so the existing
monkeypatch seams (`strategy.sweep.full_book` / `recent_trades`) still control
what the engine fetches.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_fetchers_import_from_the_market_data_module():
    from strategy.markets import full_book, recent_trades

    assert callable(full_book)
    assert callable(recent_trades)


def test_sweep_re_exports_the_same_functions():
    """Identity, not a copy: a monkeypatch on strategy.sweep.full_book must be
    the one the sweep actually calls, and it must be markets' function."""
    from strategy import sweep
    from strategy.markets import full_book, recent_trades

    assert sweep.full_book is full_book
    assert sweep.recent_trades is recent_trades
