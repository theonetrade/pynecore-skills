"""Config dataclass of the simulated broker (rendered into config/plugins/simbroker.toml)."""
from __future__ import annotations

from dataclasses import dataclass

from pynecore.core.plugin import LiveProviderConfig


@dataclass
class SimBrokerConfig(LiveProviderConfig):
    """Simulated broker: real candles from another provider plugin, orders filled by an in-process netting venue."""

    feed: str = "ccxt"
    """Plugin that supplies the candles (ccxt, ccxtrest, file, ...). Its own config/plugins/<feed>.toml applies;
    the symbol is passed through unchanged (simbroker:BINANCE:BTC/USDT@1 feeds ccxt with BINANCE:BTC/USDT)."""

    account: str = "demo"
    """Account name: state file cache/simbroker_<account>.json, run identity simbroker-<account>."""

    initial_balance: float = 10000.0
    """Cash of a fresh account, in quote_currency."""

    quote_currency: str = "USDT"
    """Currency the balance and fees are kept in."""

    fee_rate: float = 0.0004
    """Fee as a fraction of the filled notional, charged on every fill (0.0004 = 0.04 %)."""

    slippage_ticks: int = 0
    """Ticks added against the order on market, stop and trailing fills (0 = fill at the trigger or tick price)."""

    fill_on_ticks: bool = True
    """Match orders on every intra-bar update of the feed. False: only when a bar closes."""

    mintick: float = 0.0
    """Price grid used for slippage; 0 = the symbol info's mintick."""
