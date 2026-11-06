"""
File provider for PyneCore: candles from local files.

A :class:`~pynecore.core.plugin.LiveProviderPlugin` whose "exchange" is the file system.

* ``pyne data download file:BTCUSDT@60`` / ``pyne run my_strategy file:BTCUSDT@60``: the bars of
  the file become the history (written to ``data/file_BTCUSDT_60.ohlcv`` like any download).
* ``pyne run my_strategy file:BTCUSDT@60 --live``: the last ``replay_bars`` bars (or those from
  ``replay_from``) are kept out of the history and streamed as live bars, one every
  ``replay_speed`` seconds, optionally with an intra-bar update before each close. With
  ``tail = true`` the provider keeps watching the file for appended rows once it is exhausted.

Sources: ``.ohlcv`` files, or CSV / JSON / TXT exports, which PyneCore's own converter turns into
``.ohlcv`` + ``.toml`` next to the source (column and timeframe detection, symbol info). The
symbol info comes from the sibling ``.toml`` when it exists, else a 24/7 synthetic one.

Everything a plugin author needs to copy is in this one file: the config dataclass (rendered
into ``config/plugins/file.toml``), the five ProviderPlugin methods, the four live-provider
methods, and the rules the live runner imposes (see the comments in ``watch_ohlcv``).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone
from pathlib import Path

from pynecore.core.ohlcv import OHLCVReader
from pynecore.core.plugin import LiveProviderConfig, LiveProviderPlugin, ProviderError, override
from pynecore.core.syminfo import SymInfo, SymInfoInterval, SymInfoSession
from pynecore.lib.timeframe import _process_tf  # noqa: PLC2701  ("60" -> ('', 60), "1D" -> ('D', 1))
from pynecore.types.ohlcv import OHLCV

__all__ = ["FileConfig", "FileProvider"]

logger = logging.getLogger(__name__)

SOURCE_SUFFIXES = (".ohlcv", ".csv", ".json", ".txt")


@dataclass
class FileConfig(LiveProviderConfig):
    """File provider: candles from local files (.ohlcv, .csv, .json, .txt)."""

    root: str = ""
    """Folder searched for <SYMBOL>_<TF>.<ext> / <SYMBOL>.<ext> when `files` has no entry for the symbol.
    Empty: the workdir's data/ folder."""

    files: dict[str, str] = field(default_factory=dict)
    """Explicit symbol -> file map, e.g. { "BTCUSDT" = "/exports/btc_1h.csv" }. A symbol may also be a path itself."""

    source_timezone: str = "UTC"
    """Timezone of naive timestamps in CSV/JSON/TXT sources (passed to PyneCore's converter)."""

    replay_bars: int = 0
    """Live mode: the last N bars of the file are not history but are streamed as live bars (0 = none)."""

    replay_from: str = ""
    """Live mode: bars from this UTC time (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) are streamed as live bars.
    Overrides replay_bars when set."""

    replay_speed: float = 1.0
    """Seconds between two replayed bars (0 = as fast as the runner takes them)."""

    replay_realtime: bool = True
    """Re-stamp replayed bars onto the current clock (first bar = the bar slot open now, then one timeframe
    per bar). Required with pyne's live runner: it synthesises zero-volume bars whenever the last closed
    bar's timestamp lies more than two periods in the past, which would bury a replay of old timestamps."""

    emit_ticks: bool = True
    """Emit one intra-bar update (is_closed=False) before each replayed bar closes."""

    tail: bool = False
    """After the file is exhausted keep polling it: rows appended later (by another process) are streamed."""

    poll_interval: float = 2.0
    """Seconds between two polls of the source file when tailing."""


def _tf_key(timeframe: str) -> tuple[str, int]:
    modifier, multiplier = _process_tf(timeframe)
    return (modifier or "", int(multiplier))


def _parse_utc(text: str) -> int:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise ProviderError(f"replay_from {text!r}: use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (UTC)")


class FileProvider(LiveProviderPlugin[FileConfig]):
    """Candles from local files: .ohlcv or CSV/JSON/TXT converted on the fly. History for backtests and
    `pyne data download`, paced replay or a tailed file for `--live`."""

    Config = FileConfig
    plugin_name = "File"
    # A replayed file is silent between paced bars by design, so the dead-feed watchdog
    # (reconnect after N quiet bar periods) must not fire.
    feed_timeout_bars = None
    # "download" without a start date means the whole file, not one year back.
    fetch_all_by_default = True

    def __init__(self, *, symbol: str | None = None, timeframe: str | None = None,
                 ohlcv_dir: Path | None = None, config: FileConfig | None = None):
        super().__init__(symbol=symbol, timeframe=timeframe, ohlcv_dir=ohlcv_dir, config=config)
        self._ohlcv_dir = ohlcv_dir
        self._source: Path | None = None
        self._reader: OHLCVReader | None = None
        self._history_end_ts: int | None = None   # last bar handed out as history by download_ohlcv()
        # live state (reset in connect(), which the runner also calls on every reconnect)
        self._connected = False
        self._rows: list[tuple[OHLCV, bool]] = []     # (bar, re-stamp onto the clock?)
        self._pos = 0
        self._pending_close: OHLCV | None = None
        self._next_emit_at = 0.0
        self._source_mtime = 0.0
        self._last_live_ts: int | None = None          # last SOURCE timestamp handed out live
        self._next_stamp: int | None = None            # next re-stamped timestamp

    # ------------------------------------------------------------------ ProviderPlugin: timeframes

    @classmethod
    @override
    def to_tradingview_timeframe(cls, timeframe: str) -> str:
        return timeframe          # files carry PyneCore's own notation already

    @classmethod
    @override
    def to_exchange_timeframe(cls, timeframe: str) -> str:
        _process_tf(timeframe)    # validates the notation, raises ValueError otherwise
        return timeframe

    # ------------------------------------------------------------------ source resolution

    @property
    def _cfg(self) -> FileConfig:
        return self.config or FileConfig()

    def _roots(self) -> list[Path]:
        """Folders searched for a symbol's file: the configured root, else the data folder the provider was
        built for, the running CLI's data folder, $PYNE_WORK_DIR/data and ./data (first existing match wins)."""
        if self._cfg.root:
            return [Path(self._cfg.root).expanduser()]
        roots: list[Path] = []
        if self._ohlcv_dir is not None:
            roots.append(self._ohlcv_dir)
        app = sys.modules.get("pynecore.cli.app")      # listing: the CLI builds the provider without ohlcv_dir
        if app is not None:
            try:
                roots.append(Path(app.app_state.data_dir))
            except Exception:  # noqa: BLE001
                pass
        env = os.environ.get("PYNE_WORK_DIR")
        if env:
            roots.append(Path(env) / "data")
        roots.append(Path.cwd() / "data")
        unique: list[Path] = []
        for r in roots:
            if r not in unique:
                unique.append(r)
        return unique

    def _root(self) -> Path:
        return self._roots()[0]

    def resolve_source(self) -> Path:
        """The file behind the symbol: `files` map, a path given as the symbol, or `<root>/<SYMBOL>[_<TF>].<ext>`."""
        if self._source is not None:
            return self._source
        if not self.symbol:
            raise ProviderError("file provider: no symbol given (use file:SYMBOL@TIMEFRAME)")
        candidates: list[Path] = []
        mapped = self._cfg.files.get(self.symbol)
        if mapped:
            candidates.append(Path(mapped).expanduser())
        as_path = Path(self.symbol).expanduser()
        if as_path.suffix.lower() in SOURCE_SUFFIXES:
            candidates.append(as_path)
        stems = [f"{self.symbol}_{self.timeframe}", self.symbol] if self.timeframe else [self.symbol]
        for root in self._roots():
            for stem in stems:
                for suffix in SOURCE_SUFFIXES:
                    candidates.append(root / f"{stem}{suffix}")
        for c in candidates:
            if c.is_file():
                self._source = c
                return c
        tried = "\n  ".join(str(c) for c in candidates)
        raise ProviderError(f"file provider: no source for {self.symbol!r}; looked for:\n  {tried}\n"
                            f"Map it in config/plugins/file.toml ([files] \"{self.symbol}\" = \"/path/to/file.csv\") "
                            f"or set root.")

    def _ohlcv_source(self) -> Path:
        """The `.ohlcv` behind the source; CSV/JSON/TXT are converted next to themselves (skipped when current)."""
        src = self.resolve_source()
        if src.suffix.lower() == ".ohlcv":
            return src
        from pynecore.core.data_converter import DataConverter
        DataConverter().convert_to_ohlcv(src, provider="file", symbol=self.symbol, timezone=self._cfg.source_timezone)
        return src.with_suffix(".ohlcv")

    def _open_reader(self) -> OHLCVReader:
        if self._reader is not None:
            return self._reader
        path = self._ohlcv_source()
        reader = OHLCVReader(path)
        reader.open()
        if self.timeframe and reader.period and _tf_key(reader.period) != _tf_key(self.timeframe):
            reader.close()
            raise ProviderError(f"{path.name} holds {reader.period} bars, {self.timeframe} was requested: "
                                f"use the file's timeframe, or build another file with `pyne data aggregate`")
        self._reader = reader
        return reader

    def _close_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            finally:
                self._reader = None

    def _replay_cut_ts(self, reader: OHLCVReader) -> int | None:
        """First timestamp that belongs to the live replay (None: everything is history)."""
        cfg = self._cfg
        if cfg.replay_from:
            return _parse_utc(cfg.replay_from)
        if cfg.replay_bars > 0 and reader.size > cfg.replay_bars:
            start = max(reader.start_timestamp, reader.end_timestamp - 2 * 86_400_000 * (cfg.replay_bars + 1))
            bars = list(reader.read_from(start, reader.end_timestamp))[-cfg.replay_bars:]
            return bars[0].timestamp if bars else None
        return None

    # ------------------------------------------------------------------ ProviderPlugin: symbols and info

    @override
    def get_list_of_symbols(self, *args, **kwargs) -> list[str]:
        names: set[str] = set(self._cfg.files)
        for root in self._roots():
            if root.is_dir():
                for p in root.iterdir():
                    if p.suffix.lower() in SOURCE_SUFFIXES and p.is_file():
                        names.add(p.stem)
        return sorted(names)

    @override
    def update_symbol_info(self) -> SymInfo:
        """Sibling `.toml` of the source when it exists (written by pyne's converter or downloader), else 24/7."""
        src = self.resolve_source()
        toml = src.with_suffix(".toml")
        if toml.is_file():
            info = SymInfo.load_toml(toml)
            if self.timeframe:
                info.period = self.timeframe
            return info
        reader = self._open_reader()
        mintick = (reader.minmove / reader.pricescale) if (reader.minmove and reader.pricescale) else 0.01
        pricescale = int(round(1 / mintick)) if mintick < 1 else 1
        all_day = [SymInfoInterval(day=d, start=dt_time(0, 0), end=dt_time(23, 59, 59)) for d in range(7)]
        return SymInfo(
            prefix="FILE", description=f"{self.symbol} from {src.name}", ticker=self.symbol or src.stem,
            currency="USD", basecurrency=(self.symbol or "")[:3] or None, period=self.timeframe or "1D",
            type="crypto", mintick=mintick, pricescale=pricescale, minmove=1, pointvalue=1.0, mincontract=0.0001,
            timezone="UTC", volumetype="base", opening_hours=all_day,
            session_starts=[SymInfoSession(day=d, time=dt_time(0, 0)) for d in range(7)],
            session_ends=[SymInfoSession(day=d, time=dt_time(23, 59, 59)) for d in range(7)],
        )

    # ------------------------------------------------------------------ ProviderPlugin: history

    @override
    def download_ohlcv(self, time_from: datetime, time_to: datetime, on_progress=None, limit=None, with_extra=False):
        """Write the file's bars in [time_from, time_to] to the provider's .ohlcv through save_ohlcv_data().
        Bars reserved for the live replay (replay_bars / replay_from) are left out."""
        reader = self._open_reader()
        from_ms = int(time_from.replace(tzinfo=time_from.tzinfo or timezone.utc).timestamp() * 1000)
        to_ms = int(time_to.replace(tzinfo=time_to.tzinfo or timezone.utc).timestamp() * 1000)
        cut = self._replay_cut_ts(reader)
        if cut is not None:
            to_ms = min(to_ms, cut - 1)
        from_ms = max(from_ms, reader.start_timestamp)
        to_ms = min(to_ms, reader.end_timestamp)
        if to_ms < from_ms:
            return
        batch: list[OHLCV] = []
        last = None
        for bar in reader.read_from(from_ms, to_ms):
            batch.append(bar)
            last = bar.timestamp
            if len(batch) >= 5000:
                self.save_ohlcv_data(batch)      # one call = one durable publication; drops bars <= resume point
                batch = []
                if on_progress:
                    on_progress(datetime.fromtimestamp(last / 1000, tz=timezone.utc).replace(tzinfo=None))
        if batch:
            self.save_ohlcv_data(batch)
        if last is not None:
            self._history_end_ts = last if self._history_end_ts is None else max(self._history_end_ts, last)
        if on_progress:
            on_progress(time_to.replace(tzinfo=None))

    # ------------------------------------------------------------------ LiveProviderPlugin

    def _tf_ms(self) -> int:
        from pynecore.lib.timeframe import in_seconds
        assert self.timeframe is not None
        return int(in_seconds(self.timeframe) * 1000)

    def _load_live_rows(self, *, replay: bool) -> None:
        """Rows after the history end into memory. replay=True: the replay portion of the file (re-stamped
        onto the clock when replay_realtime); replay=False: rows appended later (tail), kept as they are."""
        reader = self._open_reader()
        start_after = self._last_live_ts
        if start_after is None:
            start_after = self._history_end_ts
        if start_after is None:
            cut = self._replay_cut_ts(reader)
            start_after = (cut - 1) if cut is not None else reader.end_timestamp
        if reader.end_timestamp <= start_after:
            self._rows, self._pos = [], 0
            return
        restamp = replay and self._cfg.replay_realtime
        self._rows = [(bar, restamp) for bar in reader.read_from(start_after + 1, reader.end_timestamp)]
        self._pos = 0

    @override
    async def connect(self) -> None:
        # Called on the first connection AND on every reconnect: rebuild all connection state here.
        self._close_reader()
        self._source_mtime = self.resolve_source().stat().st_mtime
        self._load_live_rows(replay=True)
        self._pending_close = None
        self._next_emit_at = 0.0
        tf_ms = self._tf_ms()
        slot_open = (int(time.time() * 1000) // tf_ms) * tf_ms       # the bar slot that is open right now
        self._next_stamp = slot_open if self._next_stamp is None else max(self._next_stamp, slot_open)
        self._connected = True
        logger.info("file provider: %d bar(s) queued for replay from %s", len(self._rows), self.resolve_source().name)

    @override
    async def disconnect(self) -> None:
        self._connected = False
        self._close_reader()

    @property
    @override
    def is_connected(self) -> bool:
        return self._connected

    async def _refresh_if_appended(self) -> bool:
        """Tail mode: reopen the source when it changed on disk; True when new rows arrived."""
        src = self.resolve_source()
        mtime = src.stat().st_mtime
        if mtime == self._source_mtime:
            return False
        self._source_mtime = mtime
        self._close_reader()
        self._load_live_rows(replay=False)
        return bool(self._rows)

    @override
    async def watch_ohlcv(self, symbol: str, timeframe: str) -> OHLCV:
        """Next live update. The runner awaits this call with a short timeout (about 2 s) and cancels it
        on expiry, so every wait here is short and all progress lives on `self`, never in locals."""
        cfg = self._cfg
        while True:
            if self._pending_close is not None:            # the close that follows an intra-bar update
                bar, self._pending_close = self._pending_close, None
                return bar
            now = time.monotonic()
            if now < self._next_emit_at:
                await asyncio.sleep(min(self._next_emit_at - now, 1.5))
                continue
            if self._pos >= len(self._rows):
                if cfg.tail:
                    if not await self._refresh_if_appended():
                        await asyncio.sleep(min(max(cfg.poll_interval, 0.2), 1.5))
                        continue
                else:
                    await asyncio.sleep(1.0)              # exhausted: park until the run is stopped
                    continue
            row, restamp = self._rows[self._pos]
            self._pos += 1
            self._last_live_ts = row.timestamp
            self._next_emit_at = time.monotonic() + max(cfg.replay_speed, 0.0)
            ts = row.timestamp
            if restamp:
                assert self._next_stamp is not None
                ts = self._next_stamp
                self._next_stamp += self._tf_ms()
            closed = OHLCV(timestamp=ts, open=row.open, high=row.high, low=row.low,
                           close=row.close, volume=row.volume, is_closed=True)
            if not cfg.emit_ticks:
                return closed
            mid = (row.open + row.close) / 2.0
            self._pending_close = closed
            return OHLCV(timestamp=ts, open=row.open, high=max(row.open, mid),
                         low=min(row.open, mid), close=mid, volume=row.volume / 2.0, is_closed=False)
