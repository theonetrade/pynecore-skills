"""
File provider for PyneCore: candles from local files.

A :class:`~pynecore.core.plugin.LiveProviderPlugin` whose "exchange" is the file system.

* ``pyne data download file:BTCUSDT@60`` / ``pyne run my_strategy file:BTCUSDT@60``: the bars of
  the file become the history (written to ``data/file_BTCUSDT_60.ohlcv`` like any download).
* ``pyne run my_strategy file:BTCUSDT@60 --live``: the last ``replay_bars`` bars (or those from
  ``replay_from``) are kept out of the history and streamed as live bars, one every
  ``replay_speed`` seconds, re-stamped onto the clock, optionally with an intra-bar update before
  each close. With ``tail = true`` the provider keeps watching the file for appended rows.
* A higher timeframe than the file's (``file:BTCUSDT@240`` on an hourly file) is aggregated:
  the history with PyneCore's aggregator into a cached ``<stem>_agg_<TF>.ohlcv``, the replay
  bar by bar with PyneCore's HTF aggregator (every source bar is an intra-bar update of the
  forming target bar, the last one closes it).

Sources: ``.ohlcv`` files; CSV / JSON / TXT exports (PyneCore's converter writes ``.ohlcv`` +
``.toml`` next to them); ``.parquet`` (pyarrow or pandas) and SQLite databases (stdlib), which
are converted into a ``.ohlcv`` cache next to the source by this plugin. The symbol info comes
from the sibling ``.toml`` when it exists, else a 24/7 synthetic one from the file's tick grid.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pynecore.core.ohlcv import OHLCVReader, OHLCVWriter
from pynecore.core.plugin import LiveProviderConfig, LiveProviderPlugin, ProviderError, override
from pynecore.core.syminfo import SymInfo, SymInfoInterval, SymInfoSession
from pynecore.lib.timeframe import _process_tf, in_seconds  # noqa: PLC2701  ("60" -> ('', 60), "1D" -> ('D', 1))
from pynecore.types.ohlcv import OHLCV

__all__ = ["FileConfig", "FileProvider"]

logger = logging.getLogger(__name__)

NATIVE_SUFFIXES = (".ohlcv",)
CONVERTER_SUFFIXES = (".csv", ".json", ".txt")             # PyneCore's own converter
PARQUET_SUFFIXES = (".parquet", ".pq")
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
SOURCE_SUFFIXES = NATIVE_SUFFIXES + CONVERTER_SUFFIXES + PARQUET_SUFFIXES + SQLITE_SUFFIXES
TIME_COLUMNS = ("timestamp", "time", "date", "datetime", "ts", "open_time", "t")
PRICE_COLUMNS = {"open": ("open", "o"), "high": ("high", "h"), "low": ("low", "l"), "close": ("close", "c"),
                 "volume": ("volume", "vol", "v", "base_volume")}


@dataclass
class FileConfig(LiveProviderConfig):
    """File provider: candles from local files (.ohlcv, .csv, .json, .txt, .parquet, SQLite)."""

    root: str = ""
    """Folder searched for <SYMBOL>_<TF>.<ext> / <SYMBOL>.<ext> when `files` has no entry for the symbol.
    Empty: the workdir's data/ folder."""

    files: dict[str, str] = field(default_factory=dict)
    """Explicit symbol -> file map, e.g. { "BTCUSDT" = "/exports/btc_1h.parquet" }. A symbol may also be a path."""

    source_timezone: str = "UTC"
    """Timezone of naive timestamps in CSV/JSON/TXT/parquet/SQLite sources."""

    sqlite_table: str = "candles"
    """SQLite: table with timestamp/open/high/low/close[/volume] columns (names detected case-insensitively)."""

    sqlite_query: str = ""
    """SQLite: a SELECT returning those columns instead of reading sqlite_table (e.g. with a WHERE on the symbol)."""

    replay_bars: int = 0
    """Live mode: the last N SOURCE bars of the file are not history but are streamed as live bars (0 = none)."""

    replay_from: str = ""
    """Live mode: bars from this UTC time (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS) are streamed as live bars.
    Overrides replay_bars when set."""

    replay_speed: float = 1.0
    """Seconds between two replayed source bars (0 = as fast as the runner takes them)."""

    replay_realtime: bool = True
    """Re-stamp replayed bars onto the current clock (first bar = the slot open now, then one source period per
    bar). Required with pyne's live runner: it synthesises zero-volume bars whenever the last closed bar's
    timestamp lies more than two periods in the past, which would bury a replay of old timestamps."""

    emit_ticks: bool = True
    """Emit an intra-bar update (is_closed=False) before each replayed bar closes (with aggregation: every
    source bar inside the target period is such an update)."""

    tail: bool = False
    """After the file is exhausted keep polling it: rows appended later (by another process) are streamed."""

    poll_interval: float = 2.0
    """Seconds between two polls of the source file when tailing."""


# ----------------------------------------------------------------------------- helpers

def _tf_key(timeframe: str) -> tuple[str, int]:
    modifier, multiplier = _process_tf(timeframe)
    return (modifier or "", int(multiplier))


def _tf_ms(timeframe: str) -> int:
    return int(in_seconds(timeframe) * 1000)


def _parse_utc(text: str) -> int:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    raise ProviderError(f"replay_from {text!r}: use YYYY-MM-DD or 'YYYY-MM-DD HH:MM:SS' (UTC)")


def _to_ms(value, tz: ZoneInfo) -> int:
    """Any timestamp representation to Unix milliseconds: datetime, epoch s/ms/us/ns, ISO string."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e17:
            return int(v / 1_000_000)      # nanoseconds
        if v > 1e14:
            return int(v / 1000)           # microseconds
        if v > 1e11:
            return int(v)                  # milliseconds
        return int(v * 1000)               # seconds
    text = str(value).strip()
    if text.isdigit():
        return _to_ms(int(text), tz)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" UTC", ""))
    return _to_ms(dt, tz)


def _infer_period(ts: list[int]) -> str:
    """PyneCore timeframe string from the typical distance between consecutive bars."""
    if len(ts) < 2:
        raise ProviderError("at least two bars are needed to infer the timeframe")
    diff = int(statistics.median(b - a for a, b in zip(ts, ts[1:]) if b > a))
    day = 86_400_000
    if 27 * day <= diff <= 32 * day:
        return "1M"
    if diff % (7 * day) == 0:
        return f"{diff // (7 * day)}W"
    if diff % day == 0:
        return f"{diff // day}D"
    if diff % 60_000 == 0:
        return str(diff // 60_000)
    if diff % 1000 == 0:
        return f"{diff // 1000}S"
    raise ProviderError(f"cannot map a bar distance of {diff} ms to a timeframe")


def _pick(columns: list[str], names: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in columns}
    for n in names:
        if n in lower:
            return lower[n]
    return None


def _rows_to_ohlcv(path: Path, columns: list[str], rows: list[tuple], tz: ZoneInfo, period_hint: str | None) -> str:
    """Write a .ohlcv next to `path` from tabular rows; returns the period written."""
    tcol = _pick(columns, TIME_COLUMNS)
    if tcol is None:
        raise ProviderError(f"{path.name}: no timestamp column among {columns}")
    idx = {name: columns.index(col) for name, col in
           ((k, _pick(columns, v)) for k, v in PRICE_COLUMNS.items()) if col is not None}
    for required in ("open", "high", "low", "close"):
        if required not in idx:
            raise ProviderError(f"{path.name}: column {required!r} not found among {columns}")
    ti = columns.index(tcol)
    bars = []
    for r in rows:
        ts = _to_ms(r[ti], tz)
        vol = float(r[idx["volume"]]) if "volume" in idx and r[idx["volume"]] is not None else 0.0
        bars.append((ts, float(r[idx["open"]]), float(r[idx["high"]]), float(r[idx["low"]]), float(r[idx["close"]]), vol))
    bars.sort(key=lambda b: b[0])
    deduped = [b for i, b in enumerate(bars) if i == 0 or b[0] != bars[i - 1][0]]
    period = period_hint or _infer_period([b[0] for b in deduped])
    out = path.with_suffix(".ohlcv")
    with OHLCVWriter(out, period, truncate=True) as writer, writer.batched():
        for ts, o, h, l, c, v in deduped:
            writer.write(OHLCV(timestamp=ts, open=o, high=max(h, o, c), low=min(l, o, c), close=c, volume=v))
        if writer.analyzed_min_move and writer.analyzed_price_scale:
            writer.set_tick_info(writer.analyzed_min_move, writer.analyzed_price_scale)
    return period


def _read_parquet(path: Path) -> tuple[list[str], list[tuple]]:
    try:
        import pyarrow.parquet as pq
        table = pq.read_table(path)
        columns = table.column_names
        data = table.to_pydict()
        return columns, list(zip(*(data[c] for c in columns)))
    except ImportError:
        pass
    try:
        import pandas as pd
        frame = pd.read_parquet(path)
        if frame.index.name and frame.index.name.lower() in TIME_COLUMNS:
            frame = frame.reset_index()
        return list(frame.columns), [tuple(r) for r in frame.itertuples(index=False, name=None)]
    except ImportError:
        raise ProviderError(f"{path.name}: reading parquet needs pyarrow or pandas (pip install pyarrow)")


def _read_sqlite(path: Path, table: str, query: str) -> tuple[list[str], list[tuple]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.execute(query or f'SELECT * FROM "{table}"')
        columns = [d[0] for d in cur.description]
        return columns, cur.fetchall()
    except sqlite3.Error as e:
        raise ProviderError(f"{path.name}: {e} (set sqlite_table or sqlite_query in config/plugins/file.toml)")
    finally:
        con.close()


# ----------------------------------------------------------------------------- the plugin

class FileProvider(LiveProviderPlugin[FileConfig]):
    """Candles from local files: .ohlcv, CSV/JSON/TXT, parquet or SQLite, converted on the fly and aggregated to a
    higher timeframe when asked. History for backtests and `pyne data download`, paced replay or a tailed file
    for `--live`."""

    Config = FileConfig
    plugin_name = "File"
    # A replayed file is silent between paced bars by design, so the dead-feed watchdog must not fire.
    feed_timeout_bars = None
    # "download" without a start date means the whole file, not one year back.
    fetch_all_by_default = True

    def __init__(self, *, symbol: str | None = None, timeframe: str | None = None,
                 ohlcv_dir: Path | None = None, config: FileConfig | None = None):
        super().__init__(symbol=symbol, timeframe=timeframe, ohlcv_dir=ohlcv_dir, config=config)
        self._ohlcv_dir = ohlcv_dir
        self._source: Path | None = None
        self._source_period: str | None = None
        self._reader: OHLCVReader | None = None          # the source-timeframe .ohlcv
        self._history_reader: OHLCVReader | None = None  # the aggregated file when aggregating, else the source
        self._history_end_ts: int | None = None          # last bar handed out as history by download_ohlcv()
        # live state (reset in connect(), which the runner also calls on every reconnect)
        self._connected = False
        self._rows: list[tuple[OHLCV, bool]] = []        # (source bar, re-stamp onto the clock?)
        self._pos = 0
        self._out: deque[OHLCV] = deque()                # updates produced by one source bar
        self._next_emit_at = 0.0
        self._source_mtime = 0.0
        self._last_live_ts: int | None = None            # last SOURCE timestamp handed out live
        self._next_stamp: int | None = None              # next re-stamped source timestamp
        self._agg = None                                 # HTFAggregator when aggregating

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

    def _tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._cfg.source_timezone or "UTC")
        except Exception:  # noqa: BLE001
            raise ProviderError(f"source_timezone {self._cfg.source_timezone!r} is not an IANA timezone")

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
        rejected: list[str] = []
        lower = self._lower_timeframe_source(rejected)
        if lower is not None:
            self._source = lower
            return lower
        tried = "\n  ".join(str(c) for c in candidates)
        why = ("\n  ".join(rejected) + "\n") if rejected else ""
        raise ProviderError(f"file provider: no source for {self.symbol!r}@{self.timeframe}; looked for:\n  {tried}\n"
                            f"  and {self.symbol}_<lower timeframe>.<ext> to aggregate from{':' if rejected else ''}\n  {why}"
                            f"Map it in config/plugins/file.toml ([files] \"{self.symbol}\" = \"/path/to/file\") "
                            f"or set root.")

    def _lower_timeframe_source(self, rejected: list[str] | None = None) -> Path | None:
        """`<SYMBOL>_<tf>.<ext>` in the roots with a tf below the requested one that aggregates into it; the
        largest such tf wins (fewest bars to aggregate), a native .ohlcv before a convertible export.
        Files of the symbol that cannot serve the timeframe are described in `rejected`."""
        if not self.timeframe or not self.symbol:
            return None
        from pynecore.core.aggregator import validate_aggregation
        prefix = f"{self.symbol}_"
        best: tuple[tuple[float, int], Path] | None = None
        for root in self._roots():
            if not root.is_dir():
                continue
            for p in root.iterdir():
                suffix = p.suffix.lower()
                if suffix not in SOURCE_SUFFIXES or not p.stem.startswith(prefix) or not p.is_file():
                    continue
                tf = p.stem[len(prefix):]
                try:
                    _process_tf(tf)
                except Exception:  # noqa: BLE001  (the stem's tail is not a timeframe, e.g. an _agg_ cache)
                    continue
                try:
                    validate_aggregation(tf, self.timeframe)
                except ValueError as e:
                    if rejected is not None:
                        rejected.append(f"{p.name}: {e}")
                    continue
                key = (in_seconds(tf), 1 if suffix in NATIVE_SUFFIXES else 0)
                if best is None or key > best[0]:
                    best = (key, p)
        return best[1] if best else None

    def _ohlcv_source(self) -> Path:
        """The `.ohlcv` behind the source. CSV/JSON/TXT go through PyneCore's converter, parquet and SQLite through
        this plugin; both write next to the source and are skipped while the cache is newer than the source."""
        src = self.resolve_source()
        suffix = src.suffix.lower()
        if suffix in NATIVE_SUFFIXES:
            return src
        cache = src.with_suffix(".ohlcv")
        if suffix in CONVERTER_SUFFIXES:
            from pynecore.core.data_converter import DataConverter
            DataConverter().convert_to_ohlcv(src, provider="file", symbol=self.symbol, timezone=self._cfg.source_timezone)
            return cache
        if cache.is_file() and cache.stat().st_mtime >= src.stat().st_mtime:
            return cache
        if suffix in PARQUET_SUFFIXES:
            columns, rows = _read_parquet(src)
        else:
            columns, rows = _read_sqlite(src, self._cfg.sqlite_table, self._cfg.sqlite_query)
        period = _rows_to_ohlcv(src, columns, rows, self._tz(), None)
        logger.info("file provider: converted %s (%d rows, period %s) -> %s", src.name, len(rows), period, cache.name)
        return cache

    def _open_reader(self) -> OHLCVReader:
        """The source-timeframe reader; validates the timeframe relation to the requested one."""
        if self._reader is not None:
            return self._reader
        path = self._ohlcv_source()
        reader = OHLCVReader(path)
        reader.open()
        self._source_period = reader.period
        if self.timeframe and reader.period and _tf_key(reader.period) != _tf_key(self.timeframe):
            from pynecore.core.aggregator import validate_aggregation
            try:
                validate_aggregation(reader.period, self.timeframe)
            except ValueError as e:
                reader.close()
                raise ProviderError(f"{path.name} holds {reader.period} bars, {self.timeframe} was requested: {e}")
        self._reader = reader
        return reader

    def _aggregating(self) -> bool:
        reader = self._open_reader()
        return bool(self.timeframe and reader.period and _tf_key(reader.period) != _tf_key(self.timeframe))

    def _history_source(self) -> OHLCVReader:
        """The reader history is served from: the source, or the aggregated cache `<stem>_agg_<TF>.ohlcv`."""
        if self._history_reader is not None:
            return self._history_reader
        reader = self._open_reader()
        if not self._aggregating():
            self._history_reader = reader
            return reader
        from pynecore.core.aggregator import aggregate_ohlcv
        assert reader.path is not None and self.timeframe
        src = Path(reader.path)
        target = src.with_name(f"{src.stem}_agg_{self.timeframe}.ohlcv")
        if not target.is_file() or target.stat().st_mtime < src.stat().st_mtime:
            info = self._base_symbol_info()
            try:
                tz = ZoneInfo(info.timezone or "UTC")
            except Exception:  # noqa: BLE001
                tz = ZoneInfo("UTC")
            aggregate_ohlcv(src, target, self.timeframe, tz=tz, session_starts=info.session_starts,
                            opening_hours=info.opening_hours, sym_type=info.type, source_tf=reader.period,
                            prefix=info.prefix)
            logger.info("file provider: aggregated %s %s -> %s", src.name, reader.period, target.name)
        agg_reader = OHLCVReader(target)
        agg_reader.open()
        self._history_reader = agg_reader
        return agg_reader

    def _close_readers(self) -> None:
        for attr in ("_history_reader", "_reader"):
            r = getattr(self, attr)
            if r is not None:
                if attr == "_history_reader" and r is self._reader:
                    setattr(self, attr, None)
                    continue
                try:
                    r.close()
                finally:
                    setattr(self, attr, None)

    # ------------------------------------------------------------------ replay cut (aligned to the target period)

    def _period_start(self, ts: int) -> int:
        """Target-period open time of a source timestamp (identity without aggregation)."""
        if not self._aggregating():
            return ts
        from pynecore.core.resampler import Resampler, grid_mode
        info = self._base_symbol_info()
        try:
            tz = ZoneInfo(info.timezone or "UTC")
        except Exception:  # noqa: BLE001
            tz = ZoneInfo("UTC")
        assert self.timeframe
        return Resampler.get_resampler(self.timeframe).get_bar_time(
            ts, tz, info.session_starts, info.opening_hours, grid_mode(info.type, info.opening_hours, info.prefix))

    def _replay_cut_ts(self) -> int | None:
        """First SOURCE timestamp that belongs to the live replay (None: everything is history); when
        aggregating it is moved forward to the first source bar that opens a new target period."""
        reader = self._open_reader()
        cfg = self._cfg
        cut: int | None = None
        if cfg.replay_from:
            cut = _parse_utc(cfg.replay_from)
        elif cfg.replay_bars > 0 and reader.size > cfg.replay_bars:
            start = max(reader.start_timestamp, reader.end_timestamp - 2 * 86_400_000 * (cfg.replay_bars + 1))
            bars = list(reader.read_from(start, reader.end_timestamp))[-cfg.replay_bars:]
            cut = bars[0].timestamp if bars else None
        if cut is None or not self._aggregating():
            return cut
        prev_period = None
        for bar in reader.read_from(max(reader.start_timestamp, cut - 32 * 86_400_000), reader.end_timestamp):
            period = self._period_start(bar.timestamp)
            if bar.timestamp >= cut and prev_period is not None and period != prev_period:
                return bar.timestamp
            prev_period = period
        return None

    # ------------------------------------------------------------------ ProviderPlugin: symbols and info

    @override
    def get_list_of_symbols(self, *args, **kwargs) -> list[str]:
        names: set[str] = set(self._cfg.files)
        for root in self._roots():
            if root.is_dir():
                for p in root.iterdir():
                    if p.suffix.lower() in SOURCE_SUFFIXES and p.is_file() and "_agg_" not in p.stem:
                        names.add(p.stem)
        return sorted(names)

    def _base_symbol_info(self) -> SymInfo:
        """Symbol info in the SOURCE timeframe: the sibling .toml when it exists, else a 24/7 synthetic one."""
        src = self.resolve_source()
        toml = src.with_suffix(".toml")
        if toml.is_file():
            return SymInfo.load_toml(toml)
        reader = self._open_reader()
        mintick = (reader.minmove / reader.pricescale) if (reader.minmove and reader.pricescale) else 0.01
        pricescale = int(round(1 / mintick)) if mintick < 1 else 1
        all_day = [SymInfoInterval(day=d, start=dt_time(0, 0), end=dt_time(23, 59, 59)) for d in range(7)]
        return SymInfo(
            prefix="FILE", description=f"{self.symbol} from {src.name}", ticker=self.symbol or src.stem,
            currency="USD", basecurrency=(self.symbol or "")[:3] or None, period=reader.period or "1D",
            type="crypto", mintick=mintick, pricescale=pricescale, minmove=1, pointvalue=1.0, mincontract=0.0001,
            timezone="UTC", volumetype="base", opening_hours=all_day,
            session_starts=[SymInfoSession(day=d, time=dt_time(0, 0)) for d in range(7)],
            session_ends=[SymInfoSession(day=d, time=dt_time(23, 59, 59)) for d in range(7)],
        )

    @override
    def update_symbol_info(self) -> SymInfo:
        info = self._base_symbol_info()
        if self.timeframe:
            info.period = self.timeframe
        return info

    # ------------------------------------------------------------------ ProviderPlugin: history

    @override
    def download_ohlcv(self, time_from: datetime, time_to: datetime, on_progress=None, limit=None, with_extra=False):
        """Write the bars in [time_from, time_to] to the provider's .ohlcv through save_ohlcv_data(). Bars reserved
        for the live replay are left out; with aggregation the bars come from the aggregated cache."""
        reader = self._history_source()
        from_ms = int(time_from.replace(tzinfo=time_from.tzinfo or timezone.utc).timestamp() * 1000)
        to_ms = int(time_to.replace(tzinfo=time_to.tzinfo or timezone.utc).timestamp() * 1000)
        cut = self._replay_cut_ts()
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

    def _load_live_rows(self, *, replay: bool) -> None:
        """SOURCE rows after the history end into memory. replay=True: the replay portion (re-stamped onto the
        clock when replay_realtime); replay=False: rows appended later (tail), kept as they are."""
        reader = self._open_reader()
        start_after = self._last_live_ts
        if start_after is None and self._history_end_ts is not None:
            # history ends on a target-period open; the replay starts with the first source bar after that period
            end_period = self._history_end_ts
            if self._aggregating():
                end_period = max((b.timestamp for b in reader.read_from(end_period, min(reader.end_timestamp, end_period + 40 * 86_400_000))
                                  if self._period_start(b.timestamp) == end_period), default=end_period)
            start_after = end_period
        if start_after is None:
            cut = self._replay_cut_ts()
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
        self._close_readers()
        self._source_mtime = self.resolve_source().stat().st_mtime
        self._load_live_rows(replay=True)
        self._out.clear()
        self._next_emit_at = 0.0
        source_ms = _tf_ms(self._source_period or self.timeframe or "1D")
        target_ms = _tf_ms(self.timeframe) if self.timeframe else source_ms
        slot_open = (int(time.time() * 1000) // target_ms) * target_ms     # the target slot open right now
        self._next_stamp = slot_open if self._next_stamp is None else max(self._next_stamp, slot_open)
        self._agg = None
        if self._aggregating():
            from pynecore.core.htf_aggregator import HTFAggregator
            from pynecore.core.resampler import grid_mode
            info = self._base_symbol_info()
            try:
                tz = ZoneInfo(info.timezone or "UTC")
            except Exception:  # noqa: BLE001
                tz = ZoneInfo("UTC")
            assert self.timeframe
            self._agg = HTFAggregator(self.timeframe, tz, session_starts=info.session_starts, chart_span_ms=source_ms,
                                      opening_hours=info.opening_hours,
                                      grid_mode=grid_mode(info.type, info.opening_hours, info.prefix))
        self._connected = True
        logger.info("file provider: %d source bar(s) queued for replay from %s%s", len(self._rows),
                    self.resolve_source().name, f" aggregated to {self.timeframe}" if self._agg else "")

    @override
    async def disconnect(self) -> None:
        self._connected = False
        self._close_readers()

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
        self._close_readers()
        self._load_live_rows(replay=False)
        return bool(self._rows)

    def _produce(self, row: OHLCV, restamp: bool) -> None:
        """Turn one source bar into the updates the runner will see (direct or aggregated)."""
        cfg = self._cfg
        ts = row.timestamp
        if restamp:
            assert self._next_stamp is not None
            ts = self._next_stamp
            self._next_stamp += _tf_ms(self._source_period or self.timeframe or "1D")
        if self._agg is None:
            closed = OHLCV(timestamp=ts, open=row.open, high=row.high, low=row.low, close=row.close, volume=row.volume)
            if cfg.emit_ticks:
                mid = (row.open + row.close) / 2.0
                self._out.append(OHLCV(timestamp=ts, open=row.open, high=max(row.open, mid), low=min(row.open, mid),
                                       close=mid, volume=row.volume / 2.0, is_closed=False))
            self._out.append(closed)
            return
        _is_new, developing, closed = self._agg.update(ts, row.open, row.high, row.low, row.close, row.volume,
                                                      chart_confirmed=True)
        if closed is not None:
            self._out.append(OHLCV(timestamp=closed.period_start, open=closed.open, high=closed.high, low=closed.low,
                                   close=closed.close, volume=closed.volume))
        if developing is not None and cfg.emit_ticks:
            self._out.append(OHLCV(timestamp=developing.period_start, open=developing.open, high=developing.high,
                                   low=developing.low, close=developing.close, volume=developing.volume, is_closed=False))

    @override
    async def watch_ohlcv(self, symbol: str, timeframe: str) -> OHLCV:
        """Next live update. The runner awaits this call with a short timeout (about 2 s) and cancels it
        on expiry, so every wait here is short and all progress lives on `self`, never in locals."""
        cfg = self._cfg
        while True:
            if self._out:
                return self._out.popleft()
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
            self._produce(row, restamp)
