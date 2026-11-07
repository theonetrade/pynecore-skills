# pynecore-telegram

Telegram notifications for PyneCore runs.

```bash
pip install -e .                       # or: uv pip install -e .
pyne telegram status                   # config path, effective settings
pyne telegram test                     # one test message
pyne run my_strategy ccxt:BYBIT:BTC/USDT:USDT@1 --live  # log/alert/broker lines are forwarded (spot pairs: default_type = "spot" in ccxt.toml)
```

`<workdir>/config/plugins/telegram.toml` (generated on first use):

```toml
enabled = true
bot_token = "123456:ABC..."            # or PYNE_TELEGRAM_TOKEN
chat_id = "-1001234567890"             # or PYNE_TELEGRAM_CHAT_ID
min_level = "WARNING"                  # script log.* lines at or above this level
forward_alerts = true                  # alert("...") calls
forward_broker = true                  # [BROKER] and quarantine lines
history_bars = false                   # realtime bars only
prefix = "[bot-1]"
```

In a script, gate side effects on the live phase (`barstate.isrealtime`) and use `log.warning`
or `alert()`; for a direct message `from pynecore_telegram import notify; notify("text")`.
Keep the token out of version control (`config/` is git-ignored in PyneCore projects).
