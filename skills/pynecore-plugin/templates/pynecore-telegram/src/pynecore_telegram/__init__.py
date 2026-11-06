"""
Telegram notifications for PyneCore runs.

PyneCore has no notification hook of its own: a script's ``log.*`` lines and the live runner's
``[BROKER]`` / quarantine lines go through the ``pyne_core_logger`` logger, and ``alert()`` prints
to the console. This plugin attaches to both:

* a :class:`logging.Handler` on ``pyne_core_logger`` forwards script log records at or above
  ``min_level`` (only on realtime bars unless ``history_bars = true``) and every ``[BROKER]`` /
  quarantine line;
* ``pynecore.lib.alert.alert`` is wrapped so ``alert("text")`` in a script is forwarded too;
* ``pyne telegram test`` / ``send TEXT`` / ``status`` are added to the CLI.

The handler is installed when this module is imported, which the ``pyne`` CLI does at startup
for every installed CLI plugin. It stays dormant until the first record, when it reads
``<workdir>/config/plugins/telegram.toml`` (or the ``PYNE_TELEGRAM_*`` environment variables);
nothing is sent unless ``enabled = true`` or ``PYNE_TELEGRAM=1``. Programmatic runs
(``ScriptRunner`` in your own Python) get the same behaviour with ``import pynecore_telegram``.
Messages are sent from a background thread with a per-minute cap, so the trading loop never
waits on the network.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pynecore.core.plugin import CLIPlugin, override

__all__ = ["TelegramConfig", "TelegramPlugin", "notify", "install", "resolve_config"]

CORE_LOGGER = "pyne_core_logger"
ENV_ENABLE, ENV_TOKEN, ENV_CHAT = "PYNE_TELEGRAM", "PYNE_TELEGRAM_TOKEN", "PYNE_TELEGRAM_CHAT_ID"
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


@dataclass
class TelegramConfig:
    """Telegram notifications for pyne runs (log lines, alert() calls, broker and quarantine events)."""

    enabled: bool = False
    """Send messages. Also enabled by PYNE_TELEGRAM=1 in the environment."""

    bot_token: str = ""
    """Bot token from @BotFather (123456:ABC-...). Environment override: PYNE_TELEGRAM_TOKEN."""

    chat_id: str = ""
    """Chat, group or channel id the bot posts to (the bot must be a member). Override: PYNE_TELEGRAM_CHAT_ID."""

    min_level: str = "WARNING"
    """Lowest script log level forwarded: INFO | WARNING | ERROR."""

    forward_alerts: bool = True
    """Forward alert() calls of the script."""

    forward_broker: bool = True
    """Forward [BROKER] lines and quarantine notices of a live run (connection, fills, stops), any level."""

    history_bars: bool = False
    """Also forward during the historical phase (backtests, warm-up). Default: realtime bars only."""

    prefix: str = ""
    """Text put in front of every message, e.g. "[bot-1]"."""

    api_base: str = "https://api.telegram.org"
    """Telegram Bot API base URL (a local relay or a test stub may replace it)."""

    timeout: float = 10.0
    """Seconds per HTTP request."""

    rate_limit: int = 20
    """Messages per minute; the excess is dropped and counted in one summary line."""


# ----------------------------------------------------------------------------- config resolution

def _workdir() -> Path | None:
    """The workdir of the running CLI, else $PYNE_WORK_DIR, else the .pyne marker upwards from cwd."""
    app = sys.modules.get("pynecore.cli.app")
    if app is not None:
        try:
            return Path(app.app_state.workdir)
        except Exception:  # noqa: BLE001
            pass
    env = os.environ.get("PYNE_WORK_DIR")
    if env:
        return Path(env)
    cur = Path.cwd().resolve()
    for _ in range(10):
        if (cur / ".pyne").is_file():
            return cur
        if (cur / "workdir").is_dir():
            return cur / "workdir"
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def resolve_config(workdir: Path | None = None) -> TelegramConfig:
    """config/plugins/telegram.toml of the workdir (created with defaults when missing) + environment overrides."""
    wd = workdir or _workdir()
    cfg = TelegramConfig()
    if wd is not None:
        from pynecore.core.config import ensure_config
        path = wd / "config" / "plugins" / "telegram.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        loaded = ensure_config(TelegramConfig, path)
        if isinstance(loaded, TelegramConfig):
            cfg = loaded
    if os.environ.get(ENV_ENABLE, "").strip().lower() in ("1", "true", "yes", "on"):
        cfg.enabled = True
    if os.environ.get(ENV_TOKEN):
        cfg.bot_token = os.environ[ENV_TOKEN]
    if os.environ.get(ENV_CHAT):
        cfg.chat_id = os.environ[ENV_CHAT]
    return cfg


# ----------------------------------------------------------------------------- sender

class _Sender:
    """Background thread posting messages to the Bot API with retries and a per-minute cap."""

    def __init__(self, cfg: TelegramConfig):
        self.cfg = cfg
        self._q: queue.Queue[str] = queue.Queue()
        self._sent_minute: list[float] = []
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="pynecore-telegram", daemon=True)
        self._thread.start()

    def enqueue(self, text: str) -> None:
        now = time.monotonic()
        self._sent_minute = [t for t in self._sent_minute if now - t < 60]
        if len(self._sent_minute) >= max(self.cfg.rate_limit, 1):
            self._dropped += 1
            return
        self._sent_minute.append(now)
        if self._dropped:
            text += f"\n(+{self._dropped} message(s) dropped by the rate limit)"
            self._dropped = 0
        self._q.put(text)

    def send_now(self, text: str) -> tuple[bool, str]:
        """Synchronous send (CLI commands, notify()); returns (ok, detail)."""
        return self._post(text)

    def _run(self) -> None:
        while True:
            text = self._q.get()
            ok, detail = self._post(text)
            if not ok:
                print(f"[pynecore-telegram] not sent: {detail}", file=sys.stderr)

    def _post(self, text: str) -> tuple[bool, str]:
        cfg = self.cfg
        if not cfg.bot_token or not cfg.chat_id:
            return False, "bot_token / chat_id not configured (config/plugins/telegram.toml or PYNE_TELEGRAM_TOKEN / PYNE_TELEGRAM_CHAT_ID)"
        url = f"{cfg.api_base.rstrip('/')}/bot{cfg.bot_token}/sendMessage"
        body = json.dumps({"chat_id": cfg.chat_id, "text": text[:4000], "disable_web_page_preview": True}).encode()
        delay = 1.0
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=cfg.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True, "ok"
                    return False, f"HTTP {resp.status}"
            except urllib.error.HTTPError as e:
                if e.code in (400, 401, 403, 404):
                    return False, f"HTTP {e.code}: {e.read(200)!r}"      # permanent: bad token / chat
                detail = f"HTTP {e.code}"
            except (urllib.error.URLError, OSError) as e:
                detail = str(e)
            if attempt < 2:
                time.sleep(delay)
                delay *= 2
        return False, detail


_senders: dict[tuple, _Sender] = {}


def _sender(cfg: TelegramConfig) -> _Sender:
    key = (cfg.api_base, cfg.bot_token, cfg.chat_id, cfg.rate_limit, cfg.timeout)
    s = _senders.get(key)
    if s is None:
        s = _senders[key] = _Sender(cfg)
    return s


def _context_suffix() -> str:
    """Symbol and bar time of the running script, when a run is active."""
    try:
        from pynecore import lib
        sym = getattr(getattr(lib, "syminfo", None), "ticker", None)
        t = getattr(lib, "_time", None)
        parts = []
        if sym:
            parts.append(str(sym))
        if t:
            parts.append(datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        return f"  ({', '.join(parts)})" if parts else ""
    except Exception:  # noqa: BLE001
        return ""


def _realtime() -> bool:
    try:
        from pynecore.lib import barstate
        return bool(barstate.isrealtime)
    except Exception:  # noqa: BLE001
        return False


# ----------------------------------------------------------------------------- handler + alert hook

class TelegramHandler(logging.Handler):
    """Forwards pyne_core_logger records according to the config; dormant until the first record."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self._cfg: TelegramConfig | None = None
        self._cfg_checked = 0.0

    def config(self) -> TelegramConfig:
        if self._cfg is None or time.monotonic() - self._cfg_checked > 60:   # pick up edits every minute
            try:
                self._cfg = resolve_config()
            except Exception:  # noqa: BLE001
                self._cfg = TelegramConfig()
            self._cfg_checked = time.monotonic()
        return self._cfg

    def emit(self, record: logging.LogRecord) -> None:
        try:
            cfg = self.config()
            if not cfg.enabled:
                return
            msg = record.getMessage()
            broker_line = "[BROKER]" in msg or "quarantin" in msg.lower()
            if broker_line:
                if not cfg.forward_broker:
                    return
            else:
                if record.levelno < _LEVELS.get(cfg.min_level.upper(), 30):
                    return
                if not cfg.history_bars and not _realtime():
                    return
            text = f"{cfg.prefix + ' ' if cfg.prefix else ''}{record.levelname} {msg}{'' if broker_line else _context_suffix()}"
            _sender(cfg).enqueue(text)
        except Exception:  # noqa: BLE001  (a notifier must never break the run)
            self.handleError(record)


_handler: TelegramHandler | None = None
_alert_original = None


def _alert_hook(message: str, *args, **kwargs):
    assert _alert_original is not None
    result = _alert_original(message, *args, **kwargs)
    try:
        cfg = _handler.config() if _handler is not None else resolve_config()
        if cfg.enabled and cfg.forward_alerts and (cfg.history_bars or _realtime()):
            _sender(cfg).enqueue(f"{cfg.prefix + ' ' if cfg.prefix else ''}ALERT {message}{_context_suffix()}")
    except Exception:  # noqa: BLE001
        pass
    return result


def install() -> None:
    """Attach the handler to pyne_core_logger and wrap alert(); idempotent."""
    global _handler, _alert_original
    if _handler is None:
        import pynecore.lib.log  # noqa: F401  (installs PyneCore's own handler first)
        _handler = TelegramHandler()
        logging.getLogger(CORE_LOGGER).addHandler(_handler)
    if _alert_original is None:
        import pynecore.lib.alert as alert_mod
        _alert_original = alert_mod.alert
        alert_mod.alert = _alert_hook      # transformed scripts call lib.alert.alert(...) through the module


def notify(text: str, workdir: Path | None = None) -> bool:
    """Send one message now (from a script, a runner or a tool); False when not configured or failed."""
    cfg = resolve_config(workdir)
    if not cfg.bot_token or not cfg.chat_id:
        return False
    ok, _ = _sender(cfg).send_now(f"{cfg.prefix + ' ' if cfg.prefix else ''}{text}")
    return ok


# ----------------------------------------------------------------------------- CLI plugin

class TelegramPlugin(CLIPlugin[TelegramConfig]):
    """Telegram notifications: forwards script log lines, alert() calls and broker/quarantine events of
    pyne runs to a chat; `pyne telegram test|send|status`."""

    Config = TelegramConfig

    @staticmethod
    @override
    def cli():
        import typer

        app = typer.Typer(help="Telegram notifications (config/plugins/telegram.toml)")

        def _mask(token: str) -> str:
            return (token[:4] + "..." + token[-3:]) if len(token) > 10 else ("***" if token else "(empty)")

        @app.command()
        def status():
            """Show the effective configuration (token masked) and where it comes from."""
            wd = _workdir()
            cfg = resolve_config(wd)
            typer.echo(f"config  : {(wd / 'config' / 'plugins' / 'telegram.toml') if wd else '(no workdir found)'}")
            typer.echo(f"enabled : {cfg.enabled}   (PYNE_TELEGRAM={os.environ.get(ENV_ENABLE, '')!r})")
            typer.echo(f"token   : {_mask(cfg.bot_token)}   chat_id: {cfg.chat_id or '(empty)'}   api: {cfg.api_base}")
            typer.echo(f"forward : log >= {cfg.min_level}, alerts={cfg.forward_alerts}, broker={cfg.forward_broker}, "
                       f"history_bars={cfg.history_bars}, rate_limit={cfg.rate_limit}/min")

        @app.command()
        def test():
            """Send a test message with the current configuration (ignores `enabled`)."""
            cfg = resolve_config()
            ok, detail = _sender(cfg).send_now(f"{cfg.prefix + ' ' if cfg.prefix else ''}pyne telegram test "
                                               f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC")
            typer.echo("sent" if ok else f"failed: {detail}")
            raise typer.Exit(0 if ok else 1)

        @app.command()
        def send(text: str = typer.Argument(..., help="Message text")):
            """Send TEXT to the configured chat (ignores `enabled`)."""
            cfg = resolve_config()
            ok, detail = _sender(cfg).send_now(f"{cfg.prefix + ' ' if cfg.prefix else ''}{text}")
            typer.echo("sent" if ok else f"failed: {detail}")
            raise typer.Exit(0 if ok else 1)

        return app


install()
