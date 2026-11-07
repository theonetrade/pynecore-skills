"""
SimBroker: a BrokerPlugin over the simulated venue, with candles from another provider plugin.

What a real broker plugin has to do, and where this file does it:

* data layer (five ProviderPlugin members) and live layer (connect / watch_ohlcv / ...): delegated
  to the feed plugin named in the config, writing into this plugin's own data file;
* `_account_id` populated before the first venue write (here: in the constructor; a real plugin
  does it in its authentication call) so the run identity is not "default";
* every `execute_*` allocates client order ids from the envelope (`KIND_*`), echoes them on the
  returned `ExchangeOrder`, dedups a repeated id (idempotency), and passes the write through
  `ensure_venue_writes_allowed()`;
* `watch_orders()` streams fills with INCREMENTAL `fill_qty` and stable `fill_id`s, plus
  synthesised `cancelled` events for orders cancelled outside the bot, reported with
  `report_external_intervention()` (the quarantine path);
* `get_position()` returns the account-wide position (`None` only when flat), `get_open_orders()`
  this run's working orders, `get_capabilities()` what the venue delivers end to end.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from pynecore.core.broker.exceptions import ClientOrderIdSpentError, ExchangeOrderRejectedError, OrderSkippedByPlugin
from pynecore.core.broker.idempotency import (
    KIND_CLOSE, KIND_ENTRY, KIND_EXIT_SL, KIND_EXIT_TP,
)
from pynecore.core.broker.models import (
    CancelDispositionOutcome, CapabilityLevel, DispatchEnvelope, ExchangeCapabilities, ExchangeOrder,
    ExchangePosition, LegType, OrderEvent, OrderStatus, OrderType,
)
from pynecore.core.plugin import CLIPlugin, override
from pynecore.core.plugin.broker import BrokerPlugin
from pynecore.core.syminfo import SymInfo
from pynecore.types.ohlcv import OHLCV

from .config import SimBrokerConfig
from .venue import OPEN_STATES, SimOrder, SimState, SimVenue

__all__ = ["SimBroker"]

logger = logging.getLogger(__name__)


def _workdir_from_env() -> Path | None:
    app = sys.modules.get("pynecore.cli.app")
    if app is not None:
        try:
            return Path(app.app_state.workdir)
        except Exception:  # noqa: BLE001
            pass
    env = os.environ.get("PYNE_WORK_DIR")
    return Path(env) if env else None


PLUGIN_NAME = "simbroker"   # the entry-point key: config/plugins/<name>.toml, cache/<name>_<account>.json, identity <name>-<account>


class SimBroker(BrokerPlugin[SimBrokerConfig], CLIPlugin):
    """Simulated broker: candles from any installed provider plugin (`feed`), orders filled by an in-process
    netting venue with fees, slippage, OCA brackets and trailing stops; state persisted per account."""

    Config = SimBrokerConfig
    plugin_name = "SimBroker"
    multi_broker = False                 # the whole "EXCHANGE:SYMBOL" is passed to the feed plugin unchanged
    fill_report_grace_s = 5.0            # fills are published within one feed update
    simulated_venue = True               # no exchange behind it: run_live.py labels it SIMULATED, not REAL MONEY
    feed_timeout_bars = 3                # replaced per instance by the feed plugin's own value

    def __init__(self, *, symbol: str | None = None, timeframe: str | None = None,
                 ohlcv_dir: Path | None = None, config: SimBrokerConfig | None = None):
        super().__init__(symbol=symbol, timeframe=timeframe, ohlcv_dir=ohlcv_dir, config=config)
        self._ohlcv_dir = ohlcv_dir
        self._cfg = config if isinstance(config, SimBrokerConfig) else SimBrokerConfig()
        # Identity before the first venue write: a real plugin sets it in its authentication call.
        self._account_id = f"{PLUGIN_NAME}-{self._cfg.account}"
        self._feed_instance: Any = None
        self._venue_instance: SimVenue | None = None
        self._owner_override: str | None = None
        self._connected = False

    # ------------------------------------------------------------------ ownership / venue

    @property
    def owner(self) -> str:
        """Who owns an order on the venue: the run tag of this bot run (stable across restarts)."""
        if self._owner_override:
            return self._owner_override
        ctx = self.store_ctx
        tag = getattr(ctx, "run_tag", None) if ctx is not None else None
        return str(tag) if tag else self.account_id

    def attach_venue(self, venue: SimVenue, *, owner: str | None = None) -> None:
        """Use an externally created venue (the offline lab shares one venue between runs)."""
        self._venue_instance = venue
        self._owner_override = owner

    def _state_path(self) -> Path | None:
        root = self._ohlcv_dir.parent if self._ohlcv_dir is not None else _workdir_from_env()
        if root is None:
            return None
        return root / "cache" / f"{PLUGIN_NAME}_{self._cfg.account}.json"

    def _mintick(self) -> float:
        if self._cfg.mintick > 0:
            return self._cfg.mintick
        try:
            if self.ohlcv_path is not None and self.ohlcv_path.with_suffix(".toml").is_file():
                return float(SymInfo.load_toml(self.ohlcv_path.with_suffix(".toml")).mintick)
        except Exception:  # noqa: BLE001
            pass
        return 0.01

    def venue(self) -> SimVenue:
        if self._venue_instance is None:
            self._venue_instance = SimVenue(
                SimState(symbol=self.symbol or "SIM"), self._state_path(), fee_rate=self._cfg.fee_rate,
                slippage_ticks=self._cfg.slippage_ticks, mintick=self._mintick(),
                initial_balance=self._cfg.initial_balance, quote_currency=self._cfg.quote_currency)
        self._venue_instance.fill_listeners[self.owner] = self._journal_fill
        return self._venue_instance

    def _journal_fill(self, record: SimOrder) -> None:
        """Keep the journal row's cumulative filled_qty current on every fill of this run's orders. At startup
        the engine sums filled_qty (buys +, sells -) over the run's live journal rows to know which part of the
        venue position is this run's; a row left at 0 makes a restart see owned=0, treat the run as flat while
        the venue holds its position, and enter again."""
        coid = record.order.client_order_id
        if self.store_ctx is not None and coid:
            self.store_ctx.set_filled(coid, float(record.order.filled_qty))

    # ------------------------------------------------------------------ data layer: delegated to the feed

    def _feed(self):
        if self._feed_instance is None:
            from pynecore.core.config import ensure_config
            from pynecore.core.plugin import load_plugin
            cls = load_plugin(self._cfg.feed)
            cfg = None
            cfg_cls = getattr(cls, "Config", None)
            root = self._ohlcv_dir.parent if self._ohlcv_dir is not None else _workdir_from_env()
            if cfg_cls is not None:
                if root is not None:
                    path = root / "config" / "plugins" / f"{self._cfg.feed}.toml"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    cfg = ensure_config(cfg_cls, path)
                else:
                    cfg = cfg_cls()
            feed = cls(symbol=self.symbol, timeframe=self.timeframe, ohlcv_dir=self._ohlcv_dir, config=cfg)
            # the feed writes into THIS plugin's data file (simbroker_<SYMBOL>_<TF>.ohlcv)
            feed.ohlcv_path = self.ohlcv_path
            feed.ohlcv_file = self.ohlcv_file
            self.feed_timeout_bars = getattr(type(feed), "feed_timeout_bars", 3)
            self._feed_instance = feed
        return self._feed_instance

    @classmethod
    @override
    def to_tradingview_timeframe(cls, timeframe: str) -> str:
        return timeframe

    @classmethod
    @override
    def to_exchange_timeframe(cls, timeframe: str) -> str:
        return timeframe             # the feed plugin converts for its own venue

    @override
    def get_list_of_symbols(self, *args, **kwargs) -> list[str]:
        return self._feed().get_list_of_symbols(*args, **kwargs)

    @override
    def update_symbol_info(self) -> SymInfo:
        info = self._feed().update_symbol_info()
        if self.timeframe:
            info.period = self.timeframe
        return info

    @override
    def download_ohlcv(self, time_from: datetime, time_to: datetime, on_progress=None, limit=None, with_extra=False):
        feed = self._feed()
        # the download flow may have swapped our writer for a temporary one (pyne run provider mode
        # publishes atomically), so re-point the feed on every call, not only when it was created
        feed.ohlcv_path = self.ohlcv_path
        feed.ohlcv_file = self.ohlcv_file
        feed.resume_timestamp = self.resume_timestamp          # the dedup boundary the download flow set on us
        try:
            feed.download_ohlcv(time_from, time_to, on_progress=on_progress, limit=limit, with_extra=with_extra)
        finally:
            feed.resume_timestamp = None

    # ------------------------------------------------------------------ live layer: feed + matching

    @override
    async def connect(self) -> None:
        self.venue()
        await self._feed().connect()
        self._connected = True

    @override
    async def disconnect(self) -> None:
        self._connected = False
        if self._feed_instance is not None:
            await self._feed_instance.disconnect()

    @property
    @override
    def is_connected(self) -> bool:
        return self._connected and bool(getattr(self._feed_instance, "is_connected", False))

    @override
    async def watch_ohlcv(self, symbol: str, timeframe: str) -> OHLCV:
        bar = await self._feed().watch_ohlcv(symbol, timeframe)
        self._on_bar(bar)
        return bar

    @override
    async def backfill_closed_bars(self, symbol: str, timeframe: str, since_ms: int) -> list[OHLCV]:
        return await self._feed().backfill_closed_bars(symbol, timeframe, since_ms)

    def _on_bar(self, bar: OHLCV) -> None:
        """Match orders against the update and surface external changes made through the CLI."""
        venue = self.venue()
        for record in venue.reload_if_changed():
            if record.run_name != self.owner:
                continue
            record.reported = True
            venue.state.pending_events.append((record.run_name, OrderEvent(
                order=record.order, event_type="cancelled", fill_price=None, fill_qty=None,
                timestamp=time.time(), pine_id=record.pine_id, from_entry=record.from_entry,
                leg_type=record.leg_type, from_disappearance_tracker=True)))
            venue.save()
            self.report_external_intervention(
                f"order {record.order.id} ({record.pine_id}, {record.leg_type.value}) was cancelled outside the bot",
                {"order_id": record.order.id}, client_order_id=record.order.client_order_id,
                exchange_order_id=record.order.id, changed_at_ms=int(time.time() * 1000))
        if self._cfg.fill_on_ticks or bar.is_closed:
            venue.on_price(bar.timestamp, bar.open, bar.high, bar.low, bar.close, is_closed=bar.is_closed)

    # ------------------------------------------------------------------ order intents

    def _record(self, envelope: DispatchEnvelope, kind: str, leg: LegType, *, side: str, qty: float,
                order_type: OrderType, price: float | None, stop: float | None, reduce_only: bool,
                oca_group: str | None, oca_type: str, trail_price: float | None = None,
                trail_offset: float | None = None) -> ExchangeOrder:
        """Place one venue order for an intent leg; a repeated client id returns the existing order."""
        venue = self.venue()
        st = venue.state
        intent = envelope.intent
        st.calls.append((self.owner, kind, intent.intent_key))
        if leg is LegType.ENTRY and st.skip_entries:                      # lab fault injection
            st.skip_entries -= 1
            raise OrderSkippedByPlugin("simulated venue skipped the entry", intent_key=intent.intent_key,
                                       reason="below_min_size")
        if leg is LegType.ENTRY and st.reject_entries:
            st.reject_entries -= 1
            raise ExchangeOrderRejectedError("simulated venue rejected the entry")
        coid = envelope.client_order_id(kind)
        existing = venue.find_by_coid(coid)
        if existing is not None:
            if existing.order.status in OPEN_STATES or existing.order.status is OrderStatus.FILLED:
                return existing.order                                    # idempotent: same id, same order
            # a cancelled / rejected order spent the id: real venues refuse a re-create under it, and the
            # engine then re-dispatches under a bumped retry sequence (fresh id) instead of adopting a dead order
            raise ClientOrderIdSpentError(f"client order id {coid} was spent by {existing.order.status.value} "
                                          f"order {existing.order.id}")
        self.ensure_venue_writes_allowed(f"{leg.value} order")
        order = ExchangeOrder(
            id=st.new_id(), symbol=self.symbol or st.symbol, side=side, order_type=order_type, qty=float(qty),
            filled_qty=0.0, remaining_qty=float(qty), price=price, stop_price=stop, average_fill_price=None,
            status=OrderStatus.OPEN, timestamp=time.time(), fee=0.0, fee_currency=self._cfg.quote_currency,
            reduce_only=reduce_only, client_order_id=coid)
        pine_id = str(getattr(intent, "pine_id", ""))
        venue.place(SimOrder(order=order, run_name=self.owner, pine_id=pine_id, leg_type=leg,
                             intent_key=intent.intent_key, from_entry=getattr(intent, "from_entry", None),
                             oca_group=oca_group, oca_type=oca_type, trail_price=trail_price, trail_offset=trail_offset))
        if self.store_ctx is not None:
            # the journal row of every venue leg: restart adoption and the lab's fill accounting read it
            self.store_ctx.upsert_order(
                coid, symbol=order.symbol, side=order.side, qty=order.qty, state="confirmed",
                intent_key=intent.intent_key, exchange_order_id=order.id,
                from_entry=getattr(intent, "from_entry", None), pine_entry_id=pine_id or None, filled_qty=0.0)
        return order

    @override
    async def execute_entry(self, envelope: DispatchEnvelope) -> list[ExchangeOrder]:
        it = envelope.intent
        stop = getattr(it, "stop", None)
        limit = getattr(it, "limit", None)
        order_type = OrderType.STOP if stop is not None else (OrderType.LIMIT if limit is not None else OrderType.MARKET)
        oca_name = getattr(it, "oca_name", None)
        return [self._record(envelope, KIND_ENTRY, LegType.ENTRY, side=it.side, qty=it.qty, order_type=order_type,
                             price=limit, stop=stop, reduce_only=False,
                             oca_group=f"{self.owner}|entry|{oca_name}" if oca_name else None,
                             oca_type=str(getattr(it, "oca_type", None) or "none"))]

    @override
    async def execute_exit(self, envelope: DispatchEnvelope) -> list[ExchangeOrder]:
        it = envelope.intent
        group = f"{self.owner}|exit|{it.intent_key}"
        orders: list[ExchangeOrder] = []
        if getattr(it, "tp_price", None) is not None:
            orders.append(self._record(envelope, KIND_EXIT_TP, LegType.TAKE_PROFIT, side=it.side, qty=it.qty,
                                       order_type=OrderType.LIMIT, price=it.tp_price, stop=None, reduce_only=True,
                                       oca_group=group, oca_type="reduce"))
        trail_price = getattr(it, "trail_price", None)
        sl = getattr(it, "sl_price", None)
        if trail_price is not None:
            orders.append(self._record(envelope, KIND_EXIT_SL, LegType.TRAILING_STOP, side=it.side, qty=it.qty,
                                       order_type=OrderType.TRAILING_STOP, price=None, stop=sl, reduce_only=True,
                                       oca_group=group, oca_type="reduce", trail_price=trail_price,
                                       trail_offset=getattr(it, "trail_offset", None) or 0.0))
        elif sl is not None:
            orders.append(self._record(envelope, KIND_EXIT_SL, LegType.STOP_LOSS, side=it.side, qty=it.qty,
                                       order_type=OrderType.STOP, price=None, stop=sl, reduce_only=True,
                                       oca_group=group, oca_type="reduce"))
        if not orders:
            raise ExchangeOrderRejectedError("exit without take-profit, stop-loss or trailing level")
        return orders

    @override
    async def execute_close(self, envelope: DispatchEnvelope) -> ExchangeOrder:
        it = envelope.intent
        return self._record(envelope, KIND_CLOSE, LegType.CLOSE, side=it.side, qty=it.qty, order_type=OrderType.MARKET,
                            price=None, stop=None, reduce_only=True, oca_group=None, oca_type="none")

    def _cancel_matching(self, intent) -> tuple[int, bool]:
        """Cancel this run's open orders of the intent's Pine id (and from_entry); (cancelled, saw_filled)."""
        venue = self.venue()
        venue.state.calls.append((self.owner, "cancel", intent.intent_key))
        pine_id = str(getattr(intent, "pine_id", ""))
        from_entry = getattr(intent, "from_entry", None)
        cancelled, saw_filled = 0, False
        for record in list(venue.state.orders.values()):
            if record.run_name != self.owner or record.pine_id != pine_id:
                continue
            if from_entry is not None and record.from_entry not in (None, from_entry):
                continue
            if record.order.status in OPEN_STATES:
                self.ensure_venue_writes_allowed("cancel")
                venue.cancel(record, emit=False)
                cancelled += 1
            elif record.order.status is OrderStatus.FILLED:
                saw_filled = True
        return cancelled, saw_filled

    @override
    async def execute_cancel(self, envelope: DispatchEnvelope) -> bool:
        cancelled, _ = self._cancel_matching(envelope.intent)
        return cancelled > 0

    @override
    async def execute_cancel_with_outcome(self, envelope: DispatchEnvelope) -> CancelDispositionOutcome:
        cancelled, saw_filled = self._cancel_matching(envelope.intent)
        if cancelled:
            return CancelDispositionOutcome.CANCEL_CONFIRMED
        if saw_filled:
            return CancelDispositionOutcome.ALREADY_FILLED
        return CancelDispositionOutcome.UNKNOWN

    @override
    async def cancel_broker_order_ref(self, ref: str) -> None:
        record = self.venue().state.orders.get(ref)
        if record is not None and record.order.status in OPEN_STATES:
            self.ensure_venue_writes_allowed("cancel")
            self.venue().cancel(record, emit=False)

    def _open_legs(self, intent) -> list[SimOrder]:
        pine_id = str(getattr(intent, "pine_id", ""))
        from_entry = getattr(intent, "from_entry", None)
        return [r for r in self.venue().open_orders(self.owner)
                if r.pine_id == pine_id and (from_entry is None or r.from_entry in (None, from_entry))]

    @override
    async def modify_entry(self, old: DispatchEnvelope, new: DispatchEnvelope) -> list[ExchangeOrder]:
        """Amend the working entry in place (price, trigger, quantity); the venue order keeps its id."""
        legs = self._open_legs(old.intent)
        if not legs:
            return await self.execute_entry(new)
        self.ensure_venue_writes_allowed("amend entry")
        it = new.intent
        venue = self.venue()
        stop = getattr(it, "stop", None)
        limit = getattr(it, "limit", None)
        order_type = OrderType.STOP if stop is not None else (OrderType.LIMIT if limit is not None else OrderType.MARKET)
        record = legs[0]
        record.order = replace(record.order, side=it.side, order_type=order_type, price=limit, stop_price=stop,
                               qty=float(it.qty), remaining_qty=max(0.0, float(it.qty) - record.order.filled_qty))
        for extra in legs[1:]:
            venue.cancel(extra, emit=False)
        venue.state.calls.append((self.owner, "amend", it.intent_key))
        venue.save()
        return [record.order]

    @override
    async def modify_exit(self, old: DispatchEnvelope, new: DispatchEnvelope) -> list[ExchangeOrder]:
        """Amend the bracket in place: existing legs get the new levels and quantity, a level the new intent
        adds is placed, a level it dropped is cancelled. No instant without protection."""
        legs = {r.leg_type: r for r in self._open_legs(old.intent)}
        if not legs:
            return await self.execute_exit(new)
        self.ensure_venue_writes_allowed("amend exit")
        it = new.intent
        venue = self.venue()
        group = f"{self.owner}|exit|{it.intent_key}"
        out: list[ExchangeOrder] = []

        def amend(leg_type: LegType, **changes) -> None:
            # the exit intent's qty is what is still to be exited (Pine re-issues it for the remaining
            # position), so it becomes the leg's remaining quantity on top of what the leg already filled
            r = legs.pop(leg_type)
            r.order = replace(r.order, side=it.side, remaining_qty=float(it.qty),
                              qty=r.order.filled_qty + float(it.qty), **changes)
            r.oca_group = group
            out.append(r.order)

        tp, sl = getattr(it, "tp_price", None), getattr(it, "sl_price", None)
        trail_price, trail_offset = getattr(it, "trail_price", None), getattr(it, "trail_offset", None)
        if tp is not None:
            if LegType.TAKE_PROFIT in legs:
                amend(LegType.TAKE_PROFIT, price=tp)
            else:
                out.append(self._record(new, KIND_EXIT_TP, LegType.TAKE_PROFIT, side=it.side, qty=it.qty,
                                        order_type=OrderType.LIMIT, price=tp, stop=None, reduce_only=True,
                                        oca_group=group, oca_type="reduce"))
        if trail_price is not None:
            if LegType.TRAILING_STOP in legs:
                r = legs[LegType.TRAILING_STOP]
                r.trail_price, r.trail_offset = trail_price, trail_offset or 0.0
                amend(LegType.TRAILING_STOP, **({} if r.trail_active else {"stop_price": sl}))
            else:
                out.append(self._record(new, KIND_EXIT_SL, LegType.TRAILING_STOP, side=it.side, qty=it.qty,
                                        order_type=OrderType.TRAILING_STOP, price=None, stop=sl, reduce_only=True,
                                        oca_group=group, oca_type="reduce", trail_price=trail_price,
                                        trail_offset=trail_offset or 0.0))
        elif sl is not None:
            if LegType.STOP_LOSS in legs:
                amend(LegType.STOP_LOSS, stop_price=sl)
            else:
                out.append(self._record(new, KIND_EXIT_SL, LegType.STOP_LOSS, side=it.side, qty=it.qty,
                                        order_type=OrderType.STOP, price=None, stop=sl, reduce_only=True,
                                        oca_group=group, oca_type="reduce"))
        for leftover in legs.values():                 # a level the new intent no longer has
            venue.cancel(leftover, emit=False)
        venue.state.calls.append((self.owner, "amend", it.intent_key))
        venue.save()
        return out

    # ------------------------------------------------------------------ state queries

    def _read_guard(self) -> None:
        st = self.venue().state
        if st.read_error is not None:                                     # lab fault injection
            err, st.read_error = st.read_error, None
            raise err

    @override
    async def get_open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        self._read_guard()
        return [r.order for r in self.venue().open_orders(self.owner) if symbol is None or r.order.symbol == symbol]

    @override
    async def get_position(self, symbol: str) -> ExchangePosition | None:
        self._read_guard()
        st = self.venue().state
        size = st.position if st.stale_position is None else st.stale_position
        if abs(size) < 1e-12:
            return None
        last = st.last_price if st.last_price is not None else st.entry_price
        return ExchangePosition(symbol=symbol, side="long" if size > 0 else "short", size=abs(size),
                                entry_price=st.entry_price, unrealized_pnl=(last - st.entry_price) * size,
                                liquidation_price=None, leverage=1.0, margin_mode="cross")

    @override
    async def get_balance(self) -> dict[str, float]:
        return {self._cfg.quote_currency: self.venue().state.cash}

    @override
    async def watch_orders(self):
        """Fills, partial fills and cancels of this run's orders, in the order the venue produced them."""
        while True:
            st = self.venue().state
            mine = [ev for owner, ev in st.pending_events if owner == self.owner]
            if mine:
                st.pending_events = [item for item in st.pending_events if item[0] != self.owner]
                for ev in mine:
                    yield ev
            else:
                await asyncio.sleep(0.2)

    @override
    def get_capabilities(self) -> ExchangeCapabilities:
        n, s = CapabilityLevel.NATIVE, CapabilityLevel.SOFTWARE
        return ExchangeCapabilities(
            stop_order=n, trailing_stop=n, tp_sl_bracket=n, partial_qty_bracket_exit=n,
            partial_qty_bracket_exit_pyramiding=n, oca_cancel=n, amend_order=n, cancel_all=s, reduce_only=n,
            watch_orders=n, fetch_position=n, idempotency=n, short_selling=n)

    # ------------------------------------------------------------------ CLI: pyne simbroker ...

    @staticmethod
    @override
    def cli():
        import typer

        app = typer.Typer(help=f"Simulated broker account (config/plugins/{PLUGIN_NAME}.toml, cache/{PLUGIN_NAME}_<account>.json)")

        def _venue(account: str | None) -> tuple[SimVenue, Path]:
            from pynecore.cli.app import app_state
            from pynecore.core.config import ensure_config
            cfg = ensure_config(SimBrokerConfig, app_state.config_dir / "plugins" / f"{PLUGIN_NAME}.toml")
            assert isinstance(cfg, SimBrokerConfig)
            path = app_state.cache_dir / f"{PLUGIN_NAME}_{account or cfg.account}.json"
            return SimVenue(SimState(), path, fee_rate=cfg.fee_rate, initial_balance=cfg.initial_balance,
                            quote_currency=cfg.quote_currency), path

        @app.command()
        def status(account: str = typer.Option(None, "--account", "-a", help="account name (default: the config's)")):
            """Position, cash, realized PnL and working orders of the simulated account."""
            venue, path = _venue(account)
            st = venue.state
            typer.echo(f"state    : {path}{'' if path.is_file() else '   (no trades yet)'}")
            typer.echo(f"position : {st.position:+g} @ {st.entry_price:g}   last {st.last_price}   realized {st.realized_pnl:+.2f}")
            typer.echo(f"cash     : {st.cash:.2f} {venue.quote_currency}   orders total {len(st.orders)}")
            for r in venue.open_orders():
                o = r.order
                typer.echo(f"  {o.id:<10} {r.leg_type.value:<5} {o.side:<4} {o.order_type.value:<13} qty {o.remaining_qty:g}/{o.qty:g}"
                           f"  limit {o.price}  stop {o.stop_price}  {r.pine_id}  owner {r.run_name}")

        @app.command("cancel-order")
        def cancel_order(order_id: str = typer.Argument(..., help="venue order id shown by status, e.g. sim-3"),
                         account: str = typer.Option(None, "--account", "-a")):
            """Cancel a working order as an operator would on a venue UI: the running bot reports an external
            intervention and, with the default policy, enters quarantine (pyne broker resume ...)."""
            venue, _ = _venue(account)
            record = venue.state.orders.get(order_id)
            if record is None or record.order.status not in OPEN_STATES:
                typer.echo(f"no open order {order_id}")
                raise typer.Exit(1)
            venue.cancel(record, emit=False, external=True)
            typer.echo(f"cancelled {order_id} externally")

        @app.command()
        def reset(account: str = typer.Option(None, "--account", "-a"),
                  yes: bool = typer.Option(False, "--yes", help="do not ask")):
            """Delete the account state (orders, position, cash). The broker journal in output/logs is not touched."""
            _, path = _venue(account)
            if not path.is_file():
                typer.echo("nothing to reset")
                return
            if not yes:
                typer.confirm(f"delete {path}?", abort=True)
            path.unlink()
            typer.echo(f"deleted {path}")

        return app
