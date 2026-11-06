# pynecore-ccxtrest

CCXT exchanges as a PyneCore live data source **without websockets**: history, symbol info and
exchange configuration are inherited from the built-in `ccxt` plugin; live bars come from
polling `fetch_ohlcv` over REST.

```bash
pip install -e .                                               # or: uv pip install -e .
pyne data download ccxtrest:BINANCE:BTC/USDT@60 --from 2024-01-01
pyne run my_strategy ccxtrest:BINANCE:BTC/USDT@1 --live -f -500
```

`config/plugins/ccxtrest.toml` holds the poll settings (`poll_interval`, `request_timeout`,
`poll_limit`) and the usual CCXT credentials; per-exchange tables such as `[binance]` with
custom `urls` are read from `config/plugins/ccxt.toml`, exactly as the built-in plugin does.
