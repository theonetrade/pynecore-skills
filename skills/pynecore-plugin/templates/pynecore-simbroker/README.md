# pynecore-simbroker

A PyneCore **broker plugin** whose venue is simulated in-process. Candles come from any installed
provider plugin (`feed = "ccxt" | "ccxtrest" | "file" | ...`); orders are matched against those
candles with fees, slippage, OCA brackets and trailing stops, and the account state is persisted
per account. It runs the complete `pyne run --broker` machinery (contract validation, SQLite
journal, restart adoption, quarantine on external intervention) without an exchange account.

```bash
pip install -e .                                      # or: uv pip install -e .
pyne plugin info simbroker                            # [provider, broker, cli]
pyne run my_strategy simbroker:BINANCE:BTC/USDT@1 --broker -f -500 --no-plot
pyne simbroker status                                 # position, cash, working orders
pyne simbroker cancel-order sim-3                     # act "outside the bot": the run enters quarantine
pyne simbroker reset --yes
python -m pynecore.testing.broker_lab run broker_lab/suite.py --mode smoke      # offline conformance lab
```

`config/plugins/simbroker.toml`: `feed`, `account`, `initial_balance`, `quote_currency`,
`fee_rate`, `slippage_ticks`, `fill_on_ticks`, `mintick`. The feed's own configuration lives in
its own file (`config/plugins/ccxt.toml` for Binance host overrides, `file.toml` for a replayed
file). The state file is `cache/simbroker_<account>.json`; the broker journal is PyneCore's
`output/logs/broker.sqlite`.
