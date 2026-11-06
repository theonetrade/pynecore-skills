"""
The simulated venue: one netting account, orders matched against the price feed, state on disk.

`SimState` / `SimOrder` carry the same attribute names as the offline broker lab's `VenueState` /
`VenueOrder` (orders, position, position_owners, pending_events, calls, ...), so the lab's
reference profile can drive and check this venue unchanged. Everything a real exchange would do
behind its API happens in `SimVenue`: order placement, limit/stop/trailing triggers, fills with
fees, OCA bookkeeping between a take-profit and its stop-loss, reduce-only capping, cancels, and
the position/cash ledger. The state is persisted to a JSON file after every mutation so a
restarted `pyne run --broker` adopts the open orders and the position like it would on a venue.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from pynecore.core.broker.models import (
    CANCEL_REASON_VENUE_OCA,
    CANCEL_REASON_VENUE_POSITION_CLEARED,
    ExchangeOrder,
    LegType,
    OrderEvent,
    OrderStatus,
    OrderType,
)

__all__ = ["SimOrder", "SimState", "SimVenue"]

OPEN_STATES = (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)


@dataclass
class SimOrder:
    """One venue order with its Pine identity (same attribute names as the lab's VenueOrder)."""

    order: ExchangeOrder
    run_name: str                      # owner: the run tag of the bot run (or the lab's run name)
    pine_id: str
    leg_type: LegType
    intent_key: str
    from_entry: str | None = None
    oca_group: str | None = None       # exits: the TP and SL legs of one intent; entries: oca_name
    oca_type: str = "reduce"           # "reduce" | "cancel" | "none"
    trail_price: float | None = None   # trailing stop: activation level
    trail_offset: float | None = None  # trailing stop: distance from the extreme
    trail_active: bool = False
    fills: int = 0                     # fill counter -> stable fill ids "<order id>:<n>"
    external: bool = False             # cancelled by someone other than the bot (pyne simbroker cancel-order)
    reported: bool = False             # the external change was reported to the engine


@dataclass
class SimState:
    """Account-wide venue state (same attribute names as the lab's VenueState, plus the ledger)."""

    symbol: str = "SIM"
    orders: dict[str, SimOrder] = field(default_factory=dict)
    position: float = 0.0                                     # signed net size of the account
    position_owners: dict[str, float] = field(default_factory=dict)   # signed size per run
    position_legs: dict = field(default_factory=dict)         # unused (netting venue); lab compatibility
    pending_events: list[tuple[str, OrderEvent]] = field(default_factory=list)   # (owner, event), not persisted
    calls: list[tuple[str, str, str]] = field(default_factory=list)              # (owner, kind, intent_key)
    next_id: int = 1
    reject_entries: int = 0            # lab fault injection
    skip_entries: int = 0
    read_error: Exception | None = None
    stale_position: float | None = None
    terminal_snapshots: dict = field(default_factory=dict)
    cash: float = 0.0
    entry_price: float = 0.0
    realized_pnl: float = 0.0
    last_price: float | None = None
    revision: int = 0

    def new_id(self) -> str:
        value = f"sim-{self.next_id}"
        self.next_id += 1
        return value


def _order_to_json(record: SimOrder) -> dict:
    d = asdict(record)
    d["order"]["order_type"] = record.order.order_type.value
    d["order"]["status"] = record.order.status.value
    d["leg_type"] = record.leg_type.value
    return d


def _order_from_json(d: dict) -> SimOrder:
    o = dict(d["order"])
    o["order_type"] = OrderType(o["order_type"])
    o["status"] = OrderStatus(o["status"])
    known = {f for f in ExchangeOrder.__dataclass_fields__}
    order = ExchangeOrder(**{k: v for k, v in o.items() if k in known})
    rest = {k: v for k, v in d.items() if k != "order"}
    rest["leg_type"] = LegType(rest["leg_type"])
    return SimOrder(order=order, **rest)


class SimVenue:
    """Matching engine + ledger over a SimState, optionally persisted to a JSON file."""

    def __init__(self, state: SimState, path: Path | None, *, fee_rate: float = 0.0, slippage_ticks: int = 0,
                 mintick: float = 0.01, initial_balance: float = 0.0, quote_currency: str = "USD"):
        self.state = state
        self.path = path
        self.fee_rate = fee_rate
        self.slippage = slippage_ticks * (mintick if mintick > 0 else 0.0)
        self.quote_currency = quote_currency
        self._mtime = 0.0
        if path is not None and path.is_file():
            self.load()
        elif not state.cash:
            state.cash = initial_balance

    # ------------------------------------------------------------------ persistence

    def load(self) -> None:
        assert self.path is not None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        st = self.state
        st.symbol = data.get("symbol", st.symbol)
        st.orders = {k: _order_from_json(v) for k, v in data.get("orders", {}).items()}
        st.position = float(data.get("position", 0.0))
        st.position_owners = {k: float(v) for k, v in data.get("position_owners", {}).items()}
        st.next_id = int(data.get("next_id", 1))
        st.cash = float(data.get("cash", 0.0))
        st.entry_price = float(data.get("entry_price", 0.0))
        st.realized_pnl = float(data.get("realized_pnl", 0.0))
        st.last_price = data.get("last_price")
        st.revision = int(data.get("revision", 0))
        self._mtime = self.path.stat().st_mtime

    def save(self) -> None:
        if self.path is None:
            return
        st = self.state
        st.revision += 1
        data = {
            "symbol": st.symbol, "orders": {k: _order_to_json(v) for k, v in st.orders.items()},
            "position": st.position, "position_owners": st.position_owners, "next_id": st.next_id,
            "cash": st.cash, "entry_price": st.entry_price, "realized_pnl": st.realized_pnl,
            "last_price": st.last_price, "revision": st.revision,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime

    def reload_if_changed(self) -> list[SimOrder]:
        """Pick up changes another process made to the state file (the CLI's cancel-order, balance);
        returns the orders cancelled externally that were not reported to the engine yet."""
        if self.path is None or not self.path.is_file():
            return []
        mtime = self.path.stat().st_mtime
        if mtime == self._mtime:
            return []
        pending = self.state.pending_events          # in-memory only, keep across the reload
        self.load()
        self.state.pending_events = pending
        return [r for r in self.state.orders.values() if r.external and not r.reported]

    # ------------------------------------------------------------------ orders

    def place(self, record: SimOrder) -> SimOrder:
        self.state.orders[record.order.id] = record
        self.save()
        return record

    def find_by_coid(self, coid: str) -> SimOrder | None:
        for r in self.state.orders.values():
            if r.order.client_order_id == coid:
                return r
        return None

    def open_orders(self, owner: str | None = None) -> list[SimOrder]:
        return [r for r in self.state.orders.values()
                if r.order.status in OPEN_STATES and (owner is None or r.run_name == owner)]

    def cancel(self, record: SimOrder, *, reason: str | None = None, ts_ms: int | None = None,
               emit: bool = True, external: bool = False) -> None:
        if record.order.status not in OPEN_STATES:
            return
        record.order = replace(record.order, status=OrderStatus.CANCELLED, remaining_qty=0.0)
        record.external = external
        if emit:
            self.state.pending_events.append((record.run_name, OrderEvent(
                order=record.order, event_type="cancelled", fill_price=None, fill_qty=None,
                timestamp=(ts_ms or int(time.time() * 1000)) / 1000.0, pine_id=record.pine_id,
                from_entry=record.from_entry, leg_type=record.leg_type, cancel_reason=reason)))
        self.save()

    # ------------------------------------------------------------------ matching

    def on_price(self, ts_ms: int, open_: float, high: float, low: float, close: float, *,
                 is_closed: bool = True) -> int:
        """Match every open order against one price update; returns the number of fills."""
        st = self.state
        st.last_price = close
        fills = 0
        for record in sorted(self.open_orders(), key=lambda r: r.order.timestamp):
            if record.order.status not in OPEN_STATES:
                continue                                   # cancelled meanwhile (OCA sibling)
            o = record.order
            buy = o.side == "buy"
            price: float | None = None
            if o.order_type is OrderType.MARKET:
                price = close + (self.slippage if buy else -self.slippage)
            elif o.order_type is OrderType.LIMIT and o.price is not None:
                if (buy and low <= o.price) or (not buy and high >= o.price):
                    price = o.price
            elif o.order_type is OrderType.STOP and o.stop_price is not None:
                if (buy and high >= o.stop_price) or (not buy and low <= o.stop_price):
                    price = o.stop_price + (self.slippage if buy else -self.slippage)
            elif o.order_type is OrderType.TRAILING_STOP:
                price = self._trail(record, high, low)
            if price is not None:
                fills += self._fill(record, o.remaining_qty, price, ts_ms)
        if fills or True:
            self.save()
        return fills

    def _trail(self, record: SimOrder, high: float, low: float) -> float | None:
        o = record.order
        offset = record.trail_offset or 0.0
        sell = o.side == "sell"                       # closes a long: stop below the running high
        if not record.trail_active:
            activation = record.trail_price if record.trail_price is not None else (o.stop_price or 0.0)
            if (sell and high >= activation) or (not sell and low <= activation):
                record.trail_active = True
                record.order = o = replace(o, stop_price=(high - offset) if sell else (low + offset))
            elif o.stop_price is not None and ((sell and low <= o.stop_price) or (not sell and high >= o.stop_price)):
                return o.stop_price - (self.slippage if sell else -self.slippage)   # the initial stop, before activation
            return None
        new_stop = (high - offset) if sell else (low + offset)
        if (sell and new_stop > (o.stop_price or -1e300)) or (not sell and new_stop < (o.stop_price or 1e300)):
            record.order = o = replace(o, stop_price=new_stop)
        if o.stop_price is not None and ((sell and low <= o.stop_price) or (not sell and high >= o.stop_price)):
            return o.stop_price - (self.slippage if sell else -self.slippage)
        return None

    def fill(self, record: SimOrder, qty: float, price: float, ts_ms: int) -> int:
        """Fill `qty` of an open order at `price` (also used by lab steps)."""
        return self._fill(record, qty, price, ts_ms)

    def _fill(self, record: SimOrder, qty: float, price: float, ts_ms: int) -> int:
        st = self.state
        o = record.order
        qty = min(qty, o.remaining_qty)
        if record.leg_type is not LegType.ENTRY:
            # reduce-only: never flips or opens; capped at the owner's exposure on the side it reduces
            owned = st.position_owners.get(record.run_name, 0.0)
            room = max(0.0, -owned if o.side == "buy" else owned)
            qty = min(qty, room)
            if qty <= 0.0:
                self.cancel(record, reason=CANCEL_REASON_VENUE_POSITION_CLEARED, ts_ms=ts_ms)
                return 0
        if qty <= 0.0:
            return 0
        signed = qty if o.side == "buy" else -qty
        fee = abs(qty * price) * self.fee_rate
        # ledger: VWAP while adding, realized while reducing
        before = st.position
        after = before + signed
        if before == 0.0 or (before > 0) == (signed > 0):
            total = abs(before) + qty
            st.entry_price = (st.entry_price * abs(before) + price * qty) / total if total else 0.0
        else:
            closed = min(abs(before), qty)
            pnl = (price - st.entry_price) * closed * (1 if before > 0 else -1)
            st.realized_pnl += pnl
            st.cash += pnl
            if after == 0.0:
                st.entry_price = 0.0
            elif (after > 0) != (before > 0):          # flipped through zero: the remainder opens at this price
                st.entry_price = price
        st.cash -= fee
        st.position = after
        st.position_owners[record.run_name] = st.position_owners.get(record.run_name, 0.0) + signed
        filled = o.filled_qty + qty
        remaining = max(0.0, o.remaining_qty - qty)
        avg = ((o.average_fill_price or 0.0) * o.filled_qty + price * qty) / filled if filled else price
        record.order = replace(o, filled_qty=filled, remaining_qty=remaining, average_fill_price=avg,
                               status=OrderStatus.FILLED if remaining <= 1e-12 else OrderStatus.PARTIALLY_FILLED,
                               fee=o.fee + fee, fee_currency=self.quote_currency)
        record.fills += 1
        st.pending_events.append((record.run_name, OrderEvent(
            order=record.order, event_type="filled" if remaining <= 1e-12 else "partial",
            fill_price=price, fill_qty=qty, timestamp=ts_ms / 1000.0, pine_id=record.pine_id,
            from_entry=record.from_entry, leg_type=record.leg_type, fee=fee, fee_currency=self.quote_currency,
            fill_id=f"{o.id}:{record.fills}")))
        self._oca_after_fill(record, qty, ts_ms)
        return 1

    def _oca_after_fill(self, record: SimOrder, qty: float, ts_ms: int) -> None:
        """OCA: a take-profit fill shrinks (reduce) or cancels (cancel) its siblings, and the other way round."""
        if not record.oca_group:
            return
        for sibling in list(self.state.orders.values()):
            if sibling is record or sibling.oca_group != record.oca_group or sibling.order.status not in OPEN_STATES:
                continue
            if record.oca_type == "cancel":
                self.cancel(sibling, reason=CANCEL_REASON_VENUE_OCA, ts_ms=ts_ms)
            elif record.oca_type == "reduce":
                new_remaining = sibling.order.remaining_qty - qty
                if new_remaining <= 1e-12:
                    self.cancel(sibling, reason=CANCEL_REASON_VENUE_OCA, ts_ms=ts_ms)
                else:
                    sibling.order = replace(sibling.order, remaining_qty=new_remaining,
                                            qty=sibling.order.filled_qty + new_remaining)
