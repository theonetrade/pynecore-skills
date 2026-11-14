# PyneCore and the generated code against Pine Script, in this project

What this file is: the findings made while developing the strategies of this project, in two
kinds. **Runtime**: PyneCore computes something differently from Pine Script on TradingView.
**Generator**: the Pyne script that pine2pyne wrote from `content/<name>.pine` fails to load or
run, or computes other values than the Pine source. Every finding is one row, with the Pine
construct, what happened, the workaround that is in force in `content/`, and the basis (a
measurement on TradingView, a run on PyneCore, a message), so that a later session, a new
PyneCore release or a new pine2pyne release can be checked against it.

Sources of truth, in this order: (1) the same script on the same bars on TradingView
("measured on TradingView", with the symbol, timeframe and date range); (2) a run of the
generated script on PyneCore (`pyne run <name>.py <data>`, the error line or the figure);
(3) the Pine Script manual. A row without a TradingView measurement goes to section 3 first.

How to update: a new runtime difference is a row in section 1; a new generator problem is a row
in section 2; something not yet measured on TradingView is a bullet in section 3 and moves to
1 or 2 once measured. Keep the minimal reproduction next to the row (`content/probes/<id>.pine`,
converted with `pine_first.py content/probes/<id>.pine`) when the full script is too big to read.
Write the pine2pyne and PyneCore versions and the date into the basis; when a newer version
removes the problem, say so in the row instead of deleting it. Rows that are reported upstream
carry the issue link (pine2pyne: <https://github.com/pine2pyne/pine2pyne/issues>; PyneCore:
<https://github.com/PyneSys/pynecore/issues>).

Sections 1 and 2 differ: section 1 is about the **runtime** (how PyneCore executes correct Pyne
code), section 2 about the **generator** (what code pine2pyne wrote).

---

## 1. PyneCore runtime against Pine

Each row is worked around in `content/<name>.pine` (the Pine stays the source of truth) unless
marked "no workaround yet".

| # | In Pine (TradingView) | In PyneCore | Workaround in the Pine source | Basis |
|---|-----------------------|-------------|-------------------------------|-------|
| 1 | | | | script, data, bars; TradingView measurement date; PyneCore version |

---

## 2. Generated code (pine2pyne) against Pine

Each row names the construct in `content/<name>.pine`, what pine2pyne wrote in
`scripts/<name>.py`, what happened (a message at load or run time, or a value that differs), and
what was done: a rewrite of the Pine that converts correctly, or a hand-maintained port
(`content/ported/<name>.pine`, see the skill).

| # | Pine construct (script, line) | What pine2pyne wrote | What happens | Fix in force | Basis |
|---|-------------------------------|----------------------|--------------|--------------|-------|
| 1 | | | | | pine2pyne version, PyneCore version, date, exact message |

---

## 3. Not measured on TradingView

- (a difference suspected from the manual or from a run, with the script and the bars to check;
  move it to section 1 or 2 once the same bars were measured on TradingView)

---

## 4. Where things are in this project

| What | Path |
|------|------|
| Pine sources (source of truth) | `content/<name>.pine`, libraries `content/lib/<user>/<Name>/v<N>.pine` |
| Hand-maintained ports (left the generated flow) | `content/ported/<name>.pine` + `scripts/<name>.py` |
| Minimal reproductions of the rows above | `content/probes/<id>.pine` |
| Generated scripts and their inputs | `scripts/<name>.py`, `scripts/<name>.toml` |
| Run outputs used as basis | `output/<name>.csv`, `output/<name>_strat.csv`, `output/<name>_trade.csv` |
| TradingView exports used as basis (Strategy Tester / data window CSV) | `docs/tradingview/<name>_<symbol>_<tf>_<from>_<to>.csv` |
