"""
FIFO: realizovany vysledok (EUR) z obchodnych riadkov (Buy/Sell) po symboli.
Nekryte nakupy/shorty neprispievaju k realizacii, kym sa pozicia neotvori a neuzavrie.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Final

MONEY: Final = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class TradeForFifo:
    id: int
    symbol: str
    trade_date: str
    transaction_type: str
    quantity: Decimal
    net_amount_eur: Decimal


def _d(x: str | float | int | None) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _row_to_trade(r: sqlite3.Row) -> TradeForFifo:
    id_ = int(r["id"])
    sym = r["symbol"]
    sym = "" if sym in (None, "") else str(sym)
    td = str(r["trade_date"])
    tt = (r["transaction_type"] or "").strip().lower()
    qty = _d(r["quantity"])
    if qty == 0 and tt in ("buy", "sell"):
        qty = Decimal("1") if tt == "buy" else Decimal("-1")
    net = _d(r["net_amount_eur"])
    return TradeForFifo(
        id=id_,
        symbol=sym,
        trade_date=td,
        transaction_type=tt,
        quantity=qty,
        net_amount_eur=net,
    )


def _load_trades(conn: sqlite3.Connection) -> list[TradeForFifo]:
    out: list[TradeForFifo] = []
    cur = conn.execute(
        """
        SELECT id, symbol, trade_date, transaction_type, quantity, net_amount_eur
        FROM transactions
        WHERE category = 'trade'
        ORDER BY trade_date, id
        """
    )
    for r in cur.fetchall():
        out.append(_row_to_trade(r))
    return out


def group_trades_by_symbol(rows: list[TradeForFifo]) -> dict[str, list[TradeForFifo]]:
    g: dict[str, list[TradeForFifo]] = {}
    for tr in rows:
        key = tr.symbol or "(bez symbolu)"
        g.setdefault(key, []).append(tr)
    return g


def _fifo_for_symbol_rows(rows: list[TradeForFifo]) -> tuple[Decimal, Decimal, Decimal]:
    """(prijem z klad. realiz, vydaj = |zap. realiz|, suma realiz)."""
    longs: deque[tuple[Decimal, Decimal]] = deque()  # (mnozstvo, cena_za_1 EUR)
    shorts: deque[tuple[Decimal, Decimal]] = deque()  # (mnozstvo, pripis za 1 pri shorte)

    p_plus = Decimal("0")
    p_abs = Decimal("0")
    cist = Decimal("0")

    for tr in rows:
        net = tr.net_amount_eur
        q = tr.quantity
        t = tr.transaction_type

        if t == "buy" and q > 0:
            cost = -net
            if cost < 0:
                cost = -cost
            rem = q
            pay_left = cost

            while rem > 0 and shorts:
                sh_qty, sh_pr = shorts[0]
                take = min(rem, sh_qty)
                pay_part = pay_left * (take / rem) if rem else Decimal("0")
                p_short_part = sh_pr * take
                rlz = (p_short_part - pay_part).quantize(MONEY, rounding=ROUND_HALF_UP)
                cist += rlz
                if rlz > 0:
                    p_plus += rlz
                elif rlz < 0:
                    p_abs += -rlz
                nsh = sh_qty - take
                if nsh <= 0:
                    shorts.popleft()
                else:
                    shorts[0] = (nsh, sh_pr)
                rem -= take
                pay_left -= pay_part

            if rem > 0 and pay_left > 0:
                cpu = (pay_left / rem).quantize(MONEY, rounding=ROUND_HALF_UP)
                longs.append((rem, cpu))

        elif t == "sell" and q < 0:
            sell_abs = -q
            pro = net if net > 0 else -net
            rem = sell_abs
            proc_left = pro

            while rem > 0 and longs:
                lq, cpu = longs[0]
                take = min(rem, lq)
                pro_part = proc_left * (take / rem) if rem else Decimal("0")
                cost_part = (cpu * take).quantize(MONEY, rounding=ROUND_HALF_UP)
                rlz = (pro_part - cost_part).quantize(MONEY, rounding=ROUND_HALF_UP)
                cist += rlz
                if rlz > 0:
                    p_plus += rlz
                elif rlz < 0:
                    p_abs += -rlz
                nlq = lq - take
                if nlq <= 0:
                    longs.popleft()
                else:
                    longs[0] = (nlq, cpu)
                rem -= take
                proc_left -= pro_part

            if rem > 0 and proc_left > 0:
                pr = (proc_left / rem).quantize(MONEY, rounding=ROUND_HALF_UP)
                shorts.append((rem, pr))

    return (p_plus, p_abs, cist)


def fifo_danove_obchodne_toky(
    trades_by_symbol: dict[str, list[TradeForFifo]],
) -> tuple[Decimal, Decimal, Decimal]:
    t_plus = t_abs = t_c = Decimal("0")
    for symbol in sorted(trades_by_symbol.keys()):
        a, b, c = _fifo_for_symbol_rows(trades_by_symbol[symbol])
        t_plus += a
        t_abs += b
        t_c += c
    return (t_plus, t_abs, t_c)


def fifo_danove_z_db(conn: sqlite3.Connection) -> tuple[Decimal, Decimal, Decimal]:
    rows = _load_trades(conn)
    return fifo_danove_obchodne_toky(group_trades_by_symbol(rows))
