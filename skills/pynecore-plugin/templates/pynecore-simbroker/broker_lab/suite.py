"""
Offline conformance suite of the simulated broker.

    python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode smoke
    python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode extended --seed 1337

The real `SimBroker` runs against the real OrderSyncEngine, BrokerStore and BrokerPosition; only
the price feed is absent (the venue is driven by `price` and `fill` steps). The profile reuses
PyneCore's `ReferenceVenueProfile` for the step vocabulary and the invariants, because the
venue's state carries the same attribute names as the lab's reference venue.
"""
from __future__ import annotations

from typing import Any

from pynecore.testing.broker_lab import ReferenceVenueProfile, Scenario, Step, pairwise_cases

from pynecore_simbroker import PLUGIN_NAME, SimBroker, SimBrokerConfig, SimState, SimVenue


class OfflineSimBroker(SimBroker):
    """The real plugin without a price feed: the lab feeds prices through steps."""

    def _feed(self):  # pragma: no cover - the lab never asks for candles
        raise RuntimeError("the offline lab does not use a price feed")

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class SimBrokerProfile(ReferenceVenueProfile):
    """One simulated account shared by every run of a scenario (netting)."""

    plugin_name = f"{PLUGIN_NAME}-offline-lab"
    account_id = f"{PLUGIN_NAME}-lab"
    symbol = "LABUSD"
    wire_symbol = "LABUSD"
    timeframe = "1"
    quantity_step = 1.0

    def __init__(self) -> None:
        self.state = SimState(symbol=self.symbol)
        self.venue = SimVenue(self.state, None, fee_rate=0.0, initial_balance=1_000_000.0, quote_currency="USD")
        self.state.last_price = 100.0

    def create_broker(self, run_name: str, store_ctx: Any) -> OfflineSimBroker:
        broker = OfflineSimBroker(symbol=self.wire_symbol, timeframe=self.timeframe,
                                  config=SimBrokerConfig(fee_rate=0.0, account="lab"))
        broker.attach_venue(self.venue, owner=run_name)
        broker.store_ctx = store_ctx
        return broker

    def _fill(self, runner: Any, step: Step) -> None:
        """A `fill` step goes through the venue's own matching (position ledger, OCA sibling reduction,
        stable fill ids) instead of the reference venue's plain bookkeeping."""
        record = self._select_order(step)
        qty = float(step.values.get("qty", record.order.remaining_qty))
        if qty <= 0.0 or qty > record.order.remaining_qty + 1e-12:
            raise ValueError("fill qty must be positive and at most the remaining quantity")
        # the plugin journals the cumulative filled_qty itself (SimBroker._journal_fill via the venue's listener)
        self.venue.fill(record, qty, float(step.values.get("price", 100.0)), runner.now_ms)

    def handle_step(self, runner: Any, step: Step) -> bool:
        if step.kind == "price":
            # one feed update: the venue matches limit / stop / trailing / market orders against it
            v = step.values
            close = float(v.get("close", v.get("price", 100.0)))
            self.venue.on_price(runner.now_ms, float(v.get("open", close)), float(v.get("high", close)),
                                float(v.get("low", close)), close, is_closed=bool(v.get("closed", True)))
            return True
        return super().handle_step(runner, step)


class DoubleFillProfile(SimBrokerProfile):
    """Control: the venue reports one execution twice under two fill ids (contract rule 2: fill_id must be
    stable). The engine books it twice, the oracle must see the position mismatch."""

    plugin_name = f"{PLUGIN_NAME}-offline-lab-doublefill"

    def _fill(self, runner: Any, step: Step) -> None:
        super()._fill(runner, step)
        from dataclasses import replace
        owner, event = self.state.pending_events[-1]
        self.state.pending_events.append((owner, replace(event, fill_id=f"{event.fill_id}-dup")))


def _s(kind: str, **values: Any) -> Step:
    return Step(kind, values=values)


def _quiet(kind: str, **values: Any) -> Step:
    """A step after which the invariants are not checked (a partial exit fill leaves the logical exit
    oversized until the strategy re-issues it on the next bar, exactly as Pine does)."""
    return Step(kind, values=values, check_invariants=False)


def build_suite(*, mode: str, seed: int):
    smoke = (
        Scenario(
            name="restart-adopts-working-order", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0, limit=90.0), _s("sync"), _s("restart"),
                   _s("entry", id="L", qty=1.0, limit=90.0), _s("sync"), _s("expect", open_orders=1, total_orders=1)),
        ),
        Scenario(
            name="market-entry-fills-on-next-price", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0), _s("sync"), _s("price", close=101.0), _s("deliver"), _s("sync"),
                   _s("expect", position=1.0, engine_position=1.0, open_orders=0)),
        ),
        Scenario(
            name="bracket-covers-position", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0), _s("sync"), _s("price", close=100.0), _s("deliver"), _s("sync"),
                   _s("exit", id="X", from_entry="L", qty=1.0, limit=110.0, stop=90.0), _s("sync"),
                   _s("expect", position=1.0, open_orders=2)),
        ),
        Scenario(
            name="take-profit-fill-cancels-stop", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0), _s("sync"), _s("price", close=100.0), _s("deliver"), _s("sync"),
                   _s("exit", id="X", from_entry="L", qty=1.0, limit=110.0, stop=90.0), _s("sync"),
                   _s("price", open=105.0, high=111.0, low=104.0, close=109.0), _s("deliver"), _s("sync"),
                   _s("expect", position=0.0, engine_position=0.0, open_orders=0)),
        ),
        Scenario(
            name="stop-loss-fill-cancels-take-profit", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0), _s("sync"), _s("price", close=100.0), _s("deliver"), _s("sync"),
                   _s("exit", id="X", from_entry="L", qty=1.0, limit=110.0, stop=90.0), _s("sync"),
                   _s("price", open=95.0, high=96.0, low=89.0, close=91.0), _s("deliver"), _s("sync"),
                   _s("expect", position=0.0, engine_position=0.0, open_orders=0)),
        ),
        Scenario(
            name="partial-fill-keeps-coverage", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=2.0), _s("sync"), _s("price", close=100.0), _s("deliver"), _s("sync"),
                   _s("exit", id="X", from_entry="L", qty=2.0, limit=110.0, stop=90.0), _s("sync"),
                   _quiet("fill", qty=1.0, price=110.0), _quiet("deliver"),
                   # the strategy re-issues the exit for the remaining position on the next bar
                   _quiet("exit", id="X", from_entry="L", qty=1.0, limit=110.0, stop=90.0), _s("sync"),
                   _s("expect", position=1.0, engine_position=1.0)),
        ),
        Scenario(
            name="cancelled-entry-leaves-nothing", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0, limit=90.0), _s("sync"), _s("cancel", id="L"), _s("sync"),
                   _s("expect", open_orders=0, position=0.0)),
        ),
        Scenario(
            name="duplicate-fill-event-is-ignored", profile_factory=SimBrokerProfile, seed=seed,
            steps=(_s("entry", id="L", qty=1.0), _s("sync"), _s("price", close=100.0), _s("duplicate_event"),
                   _s("deliver"), _s("sync"), _s("expect", position=1.0, engine_position=1.0)),
        ),
        Scenario(
            name="two-runs-share-the-account", profile_factory=SimBrokerProfile, seed=seed, runs=("main", "second"),
            steps=(_s("entry", id="L", qty=1.0), Step("entry", run="second", values={"id": "L", "qty": 2.0}),
                   _s("sync"), Step("sync", run="second"), _s("price", close=100.0), _s("deliver"),
                   Step("deliver", run="second"), _s("sync"), Step("sync", run="second"),
                   _s("expect", position=1.0, account_position=3.0), Step("expect", run="second", values={"position": 2.0})),
        ),
        Scenario(
            name="control-duplicate-fill-id-is-detected", profile_factory=DoubleFillProfile, seed=seed,
            expected_violation="position mismatch",
            steps=(_s("entry", id="L", qty=1.0, limit=90.0), _s("sync"), _s("fill", qty=1.0, price=90.0),
                   _s("deliver"), _s("sync"), _s("expect", position=1.0)),
        ),
    )
    if mode == "smoke":
        return smoke

    extended = list(smoke)
    axes = {"entry": ("market", "limit", "stop"), "exit": ("tp", "sl", "both"), "fill": ("full", "partial")}
    for i, case in enumerate(pairwise_cases(axes, seed=seed)):
        entry_values: dict[str, Any] = {"id": "L", "qty": 2.0}
        if case["entry"] == "limit":
            entry_values["limit"] = 99.0
        elif case["entry"] == "stop":
            entry_values["stop"] = 101.0
        exit_values: dict[str, Any] = {"id": "X", "from_entry": "L", "qty": 2.0}
        if case["exit"] in ("tp", "both"):
            exit_values["limit"] = 110.0
        if case["exit"] in ("sl", "both"):
            exit_values["stop"] = 90.0
        steps = [_s("entry", **entry_values), _s("sync"),
                 _s("price", open=100.0, high=102.0, low=98.0, close=100.0), _s("deliver"), _s("sync"),
                 _s("exit", **exit_values), _s("sync")]
        if case["fill"] == "partial":
            steps += [_quiet("fill", qty=1.0, price=110.0 if "limit" in exit_values else 90.0), _quiet("deliver"),
                      _quiet("exit", **{**exit_values, "qty": 1.0}), _s("sync"),
                      _s("expect", position=1.0, engine_position=1.0)]
        else:
            trigger = {"open": 105.0, "high": 111.0, "low": 104.0, "close": 109.0} if "limit" in exit_values \
                else {"open": 95.0, "high": 96.0, "low": 89.0, "close": 91.0}
            steps += [_s("price", **trigger), _s("deliver"), _s("sync"),
                      _s("expect", position=0.0, engine_position=0.0, open_orders=0)]
        extended.append(Scenario(name=f"pairwise-{i}-{case['entry']}-{case['exit']}-{case['fill']}",
                                 profile_factory=SimBrokerProfile, seed=seed, tags=frozenset({"extended"}),
                                 steps=tuple(steps)))
    return tuple(extended)
