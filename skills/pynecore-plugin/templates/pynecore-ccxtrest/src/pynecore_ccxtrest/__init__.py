"""
CCXT over REST polling: a PyneCore live provider without websockets.

Subclasses the built-in :class:`pynecore.providers.ccxt.CCXTProvider`, so symbol discovery, symbol
info, history download (``pyne data download ccxtrest:BINANCE:BTC/USDT@1``) and the
``config/plugins/ccxt.toml`` exchange tables (keys, custom ``urls``) are inherited unchanged.
Only the live part is replaced: instead of ccxt.pro's websocket ``watch_ohlcv`` a background task
polls ``fetch_ohlcv`` over REST every ``poll_interval`` seconds and turns the answers into
intra-bar updates (``is_closed=False``) and closed bars (``is_closed=True``).

Use it where websockets are blocked or where an exchange has no ccxt.pro support, and as the
pattern for any provider whose venue only offers polling: the poll loop runs as an asyncio task
started in ``connect()`` and feeds a queue; ``watch_ohlcv`` only takes from that queue, which keeps
it safe against the short cancellation timeout the live runner applies to every call.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pynecore.core.plugin import override
from pynecore.lib.timeframe import in_seconds
from pynecore.providers.ccxt import CCXTConfig, CCXTError, CCXTProvider
from pynecore.types.ohlcv import OHLCV

__all__ = ["CCXTRestConfig", "CCXTRestProvider"]

logger = logging.getLogger(__name__)


@dataclass
class CCXTRestConfig(CCXTConfig):
    """CCXT provider polling REST (no websocket). Exchange tables ([binance] ...) are read from ccxt.toml."""

    poll_interval: float = 5.0
    """Seconds between two REST polls of the forming bar (1 poll = 1 fetch_ohlcv request)."""

    request_timeout: float = 20.0
    """Seconds one REST request may take before it is abandoned and the poll retried."""

    poll_limit: int = 5
    """Candles asked for per poll; must cover the bars that can close between two polls."""


class CCXTRestProvider(CCXTProvider):
    """CCXT market data without websockets: history and symbol info as the built-in ccxt plugin, live bars
    by polling fetch_ohlcv over REST."""

    plugin_name = "CCXT REST"
    Config = CCXTRestConfig

    def __init__(self, *, symbol: str | None = None, timeframe: str | None = None,
                 ohlcv_dir: Path | None = None, config: CCXTRestConfig | None = None):
        super().__init__(symbol=symbol, timeframe=timeframe, ohlcv_dir=ohlcv_dir, config=config)
        self._rest_connected = False
        self._queue: asyncio.Queue[OHLCV | BaseException] | None = None
        self._poll_task: asyncio.Task | None = None
        self._last_closed_ts: int | None = None
        self._last_forming: tuple | None = None

    @property
    def _rcfg(self) -> CCXTRestConfig:
        return self.config if isinstance(self.config, CCXTRestConfig) else CCXTRestConfig()

    # ------------------------------------------------------------------ lifecycle

    @override
    async def connect(self) -> None:
        """Start the poll task. Called again on every reconnect, so all live state is rebuilt here."""
        await self.disconnect()
        self._client.timeout = int(self._rcfg.request_timeout * 1000)      # ccxt's own per-request timeout (ms)
        await asyncio.wait_for(asyncio.to_thread(self._client.load_markets), self._rcfg.request_timeout)
        if self.symbol not in (self._client.markets or {}):
            raise CCXTError(f"{self._client.id}: unknown symbol {self.symbol!r}")
        self._queue = asyncio.Queue()
        self._last_forming = None
        self._rest_connected = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("ccxtrest: polling %s %s on %s every %.1fs", self.symbol, self.timeframe, self._client.id,
                    self._rcfg.poll_interval)

    @override
    async def disconnect(self) -> None:
        self._rest_connected = False
        task, self._poll_task = self._poll_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    @property
    @override
    def is_connected(self) -> bool:
        return self._rest_connected

    # ------------------------------------------------------------------ streaming

    def _tf_ms(self) -> int:
        assert self.timeframe is not None
        return int(in_seconds(self.timeframe) * 1000)

    def _fetch(self, since: int | None, limit: int) -> list:
        assert self.symbol is not None and self.xchg_timeframe is not None
        return self._client.fetch_ohlcv(self.symbol, self.xchg_timeframe, since=since, limit=limit)

    async def _poll_loop(self) -> None:
        """Poll fetch_ohlcv; push closed bars once and the forming bar whenever it changed."""
        assert self._queue is not None
        cfg = self._rcfg
        tf_ms = self._tf_ms()
        first = True
        while True:
            try:
                candles = await asyncio.wait_for(asyncio.to_thread(self._fetch, None, cfg.poll_limit),
                                                 cfg.request_timeout)
                now_ms = self._client.milliseconds()
                # The last candle of the answer is the one still forming (or a just-closed bar the
                # exchange has not rolled over yet); only the candles before it are final.
                closed = [c for c in candles[:-1] if c[0] + tf_ms <= now_ms]
                forming = candles[-1] if candles else None
                if first:
                    # The warm-up history and backfill_closed_bars() cover everything up to now;
                    # start emitting closed bars from the next close on.
                    if closed:
                        self._last_closed_ts = max(self._last_closed_ts or 0, closed[-1][0])
                    first = False
                else:
                    for c in closed:
                        if self._last_closed_ts is None or c[0] > self._last_closed_ts:
                            self._last_closed_ts = c[0]
                            self._queue.put_nowait(self._to_bar(c, True))
                if forming is not None:
                    snapshot = tuple(forming[:6])
                    if snapshot != self._last_forming:        # unchanged quotes are not re-sent
                        self._last_forming = snapshot
                        self._queue.put_nowait(self._to_bar(forming, False))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  (network, exchange, timeout: let the runner reconnect)
                self._queue.put_nowait(exc)
                return
            await asyncio.sleep(max(cfg.poll_interval, 0.5))

    @staticmethod
    def _to_bar(c: list, is_closed: bool) -> OHLCV:
        return OHLCV(timestamp=int(c[0]), open=float(c[1]), high=float(c[2]), low=float(c[3]),
                     close=float(c[4]), volume=float(c[5]), is_closed=is_closed)

    @override
    async def watch_ohlcv(self, symbol: str, timeframe: str) -> OHLCV:
        """Next update from the poll task. Taking from the queue is cancellation-safe: the runner's
        ~2 s timeout on this call never loses a bar."""
        assert self._queue is not None, "connect() precedes watch_ohlcv()"
        item = await self._queue.get()
        if isinstance(item, BaseException):
            self._rest_connected = False
            raise item                      # the live runner logs it and drives disconnect() -> connect()
        return item

    @override
    async def backfill_closed_bars(self, symbol: str, timeframe: str, since_ms: int) -> list[OHLCV]:
        """Closed bars after `since_ms` (the bars that closed while the history was being prepared)."""
        tf_ms = self._tf_ms()
        candles = await asyncio.wait_for(asyncio.to_thread(self._fetch, since_ms, 500), self._rcfg.request_timeout)
        now_ms = self._client.milliseconds()
        bars = [self._to_bar(c, True) for c in candles[:-1] if c[0] > since_ms and c[0] + tf_ms <= now_ms]
        if bars:
            self._last_closed_ts = max(self._last_closed_ts or 0, bars[-1].timestamp)
        return bars
