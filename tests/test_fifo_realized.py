from __future__ import annotations

from decimal import Decimal

from uctsimp.fifo_realized import (
    TradeForFifo,
    _fifo_for_symbol_rows,
    fifo_danove_obchodne_toky,
    group_trades_by_symbol,
)


def test_realized_long_round_trip() -> None:
    t1 = TradeForFifo(1, "A", "2020-01-01", "buy", Decimal(1), Decimal("-10.00"))
    t2 = TradeForFifo(2, "A", "2020-01-02", "sell", Decimal(-1), Decimal("15.00"))
    p, a, c = _fifo_for_symbol_rows([t1, t2])
    assert c == Decimal("5.00")
    assert p == Decimal("5.00")
    assert a == Decimal("0.00")


def test_open_long_no_realized() -> None:
    t1 = TradeForFifo(1, "A", "2020-01-01", "buy", Decimal(1), Decimal("-228.55"))
    p, a, c = _fifo_for_symbol_rows([t1])
    assert c == Decimal("0.00")
    assert p == Decimal("0.00")
    assert a == Decimal("0.00")


def test_short_open_and_cover() -> None:
    t1 = TradeForFifo(1, "A", "2020-01-01", "sell", Decimal(-1), Decimal("5.00"))
    t2 = TradeForFifo(2, "A", "2020-01-02", "buy", Decimal(1), Decimal("-7.00"))
    p, a, c = _fifo_for_symbol_rows([t1, t2])
    assert c == Decimal("-2.00")
    assert a == Decimal("2.00")
    assert p == Decimal("0.00")


def test_two_symbols_aggregated() -> None:
    r1 = [
        TradeForFifo(1, "A", "2020-01-01", "buy", Decimal(1), Decimal("-10.00")),
        TradeForFifo(2, "A", "2020-01-02", "sell", Decimal(-1), Decimal("10.00")),
    ]
    r2 = [TradeForFifo(3, "B", "2020-01-01", "buy", Decimal(1), Decimal("-5.00"))]
    g = {**group_trades_by_symbol(r1), **group_trades_by_symbol(r2)}
    a, b, t = fifo_danove_obchodne_toky(g)
    assert t == Decimal("0.00")
    assert a == Decimal("0.00")
    assert b == Decimal("0.00")
