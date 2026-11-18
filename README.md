# btc-race-1444-entry

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Holladitt/btc-race-1444-entry/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Holladitt/btc-race-1444-entry/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

Indicator-engine washout-bounce bot for make.fun `btc-race-24h-20261118-1444`
(Race a BTC trading bot live for 24 hours). It trades Coinbase Wrapped BTC
(cbBTC) on 1-minute bars with $1,000 of paper money, long only.

## Strategy

The bot only buys a washout bounce, never a single-indicator signal: a sharp
15-bar drop (150 bps) that has already started to turn — the minute itself up,
the MACD histogram turning up, RSI under 35, price in the lower half of the
Bollinger band, a wide bar versus ATR, a climax just-completed 5-minute bar,
price near the expanding 24h/48h lows after a deeply negative 6-hour move,
and at least 5 of 8 regime votes agreeing (downtrend EMA layout, at the
Donchian-20 low, below session VWAP, hot ATR, strong ADX with DI- over DI+,
Supertrend down, deeply negative 1-hour momentum). The setup is
edge-triggered and fires once. Exit: take-profit +120 bps, stop -80 bps from
the filled average cost, or 30 bars held; 10 bars of rest after any exit.

## Dev grade

Local replay with the kit's exact market model (fee 4 bps + slippage 2 bps +
constant-product impact, fill at the next minute's open), validated to the
cent against official `grade-local` on the buy-and-hold baseline
(-$31.633698). Note: `grade-local --driver process` in this VM runs Node
only and no Docker is available, so a Python bot cannot be graded by the
bundled CLI here; the server grades Python entries in Docker on submit.

- Net PnL **+$12.34** (exact replay: +$12.341487), 1 trade, win rate 100%,
  max drawdown 0.12%, profit factor ∞ (no losing trade), fees $0.81
- Net PnL at 2x fees (official convention, fee doubled): **+$11.53**
  (fee + slippage both doubled as a harsher stress test: +$11.13)
- The one trade: buy at bar 71 open $63,457.08, sell at bar 81 open
  $64,291.70 — the washout bounce after the day's opening plunge
- Two executions produce byte-identical action logs (deterministic)
- Split: first half (bars 0–719) +$12.34, second half (720–1439) $0.00
  (no setup fired — the gate is deliberately rare)

## Ablation (dev window, same harness simulator)

| Variant | Net PnL | Trades | Max DD |
|---|---:|---:|---:|
| A. Core only (all modes) | -$30.37 | 10 | 4.97% |
| B. Core + Donchian | -$37.02 | 11 | 4.66% |
| C. Core + MACD | -$20.04 | 7 | 3.79% |
| D. Core + Supertrend | -$30.37 | 10 | 4.97% |
| E. Best + activity proxy | -$6.35 | 4 | 1.60% |
| **Final: full-stack washout gate** | **+$12.34** | **1** | **0.12%** |

The dev window falls 280 bps with choppy range action, so trend, breakout
and range-scalp modes all lose; only the strict washout gate earns. The
range-low scalp and trend-pullback modes were tested and are not armed in
the final bot. House baselines on the same window: buy-and-hold -$31.63,
momentum -$23.87, ema-cross -$148.17, rsi-bollinger -$39.92,
adx-momentum -$75.57.

## Honest limits

- The kit bars carry no volume (`v` is null), so Relative Volume has no real
  input: the activity check uses bar range / ATR 14 as a labelled proxy.
- VWAP has no volume either, so it is the equal-weight session VWAP (running
  mean of the typical price).
- A 48-hour level cannot be complete inside a 24-hour window: it is the
  expanding high/low over all history available, never invented data.
- Multi-timeframe signals aggregate the same 1-minute bars, completed
  candles only, with no lookahead.

## Run it

Grade it locally with the competition kit:

```sh
node cli/makefun.mjs grade-local --kit <kit-dir> --bot . --driver process
```

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
