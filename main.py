"""Indicator Engine: adaptive washout-bounce bot for make.fun
btc-race-24h-20261118-1444 (Race a BTC trading bot live for 24 hours).

One symbol (CBBTC/USDC), long only, paper $1,000. The bot reads 1-minute bars
in order and answers each batch with at most one order. Everything is
deterministic: no clock, no network, no files, no randomness; every number
comes from bars already closed.

Indicator stack (all causal, current bar included, nothing from the future):
  core      EMA 20 / EMA 50, RSI 14, ADX 14 with DI+ / DI-, ATR 14,
            Bollinger Bands 20,2, session VWAP, activity proxy
  structural  expanding 24h high/low, expanding 48h high/low (inside a 24h
            window the 48h level is the whole history seen so far),
            Donchian 20 low
  extra     MACD 12,26,9, Supertrend 10,3
  multi-timeframe  5-minute bars (completed only) for execution timing,
            1-hour momentum and 6-hour momentum from completed 1-minute bars

Honest limits of this data (the kit's bars carry no volume):
  - "Relative Volume 20" has no real input: every bar's volume is null, so
    the activity check uses bar range / ATR 14 instead, and says so.
  - VWAP has no volume either, so it is the equal-weight session VWAP:
    the running mean of the typical price (high+low+close)/3.
  - A 48-hour level cannot be complete inside a 24-hour window: it is the
    expanding high/low over all history available, never invented data.

Strategy in one paragraph: the engine only buys a washout bounce. It waits
for a sharp 15-bar washout that has already started to turn (RSI oversold
is never enough on its own, and no entry is ever taken on an EMA cross):
the close must be up on the minute, the MACD histogram turning up, the bar
itself wide versus ATR, the just-completed 5-minute bar a climax bar, the
price near the 24h/48h lows after a deeply negative 6-hour move, and at
least 5 of 8 regime votes must agree (downtrend EMA layout, price at the
Donchian low, below session VWAP, hot ATR, strong ADX with DI- over DI+,
Supertrend down, deeply negative 1-hour momentum). The setup is
edge-triggered: it fires once when it first becomes true. Exits are
take-profit +120 bps, stop -80 bps from the filled average cost, or 30 bars
held, whichever comes first; after any exit the bot rests 10 bars. The
range-low scalp and trend-pullback modes of the same stack were ablated
and lost money on the dev window, so they are not armed here.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
MAX_SLIPPAGE_BPS = 500
BUY_DROP_BPS = 150          # 15-bar washout depth that arms the setup
RSI_MAX = 35.0              # oversold ceiling (one vote among many, never alone)
PCTB_MAX = 0.5              # inside the lower half of the Bollinger band
ACT_MIN = 0.8               # bar range versus ATR 14 (activity proxy)
FIVEM_RANGE_MIN = 4.0       # completed 5-minute bar range versus ATR 14
RANGE_POS_MAX = 0.30        # position inside the expanding 24h/48h range
MOM6H_MAX_BPS = -150        # 6-hour momentum must be deeply negative
VOTES_NEEDED = 5            # of the 8 regime votes below
TAKE_BPS = 120
STOP_BPS = 80
MAX_HOLD_BARS = 30
COOLDOWN_BARS = 10


class Wilder:
    """Wilder smoothing that seeds from the mean of its first n finite inputs,
    exactly like a textbook Wilder average (no lookahead, no NaN poisoning)."""

    def __init__(self, period):
        self.period = period
        self.buffer = []
        self.value = None

    def feed(self, number):
        if number is None:
            return self.value
        if self.value is None:
            self.buffer.append(number)
            if len(self.buffer) == self.period:
                self.value = sum(self.buffer) / self.period
            return self.value
        self.value = (self.value * (self.period - 1) + number) / self.period
        return self.value


class Ema:
    """Classic EMA, seeded with the first value."""

    def __init__(self, period):
        self.alpha = 2.0 / (period + 1)
        self.value = None

    def feed(self, number):
        if self.value is None:
            self.value = number
        else:
            self.value = number * self.alpha + self.value * (1.0 - self.alpha)
        return self.value


class Rolling:
    """Rolling mean and population standard deviation over the last n values."""

    def __init__(self, window):
        self.window = window
        self.values = []
        self.total = 0.0
        self.total_sq = 0.0

    def feed(self, number):
        self.values.append(number)
        self.total += number
        self.total_sq += number * number
        if len(self.values) > self.window:
            old = self.values.pop(0)
            self.total -= old
            self.total_sq -= old * old
        n = len(self.values)
        mean = self.total / n
        var = self.total_sq / n - mean * mean
        return mean, (var ** 0.5 if var > 0.0 else 0.0)

    def __len__(self):
        return len(self.values)


class Feed:
    """Streaming indicator state for one symbol. feed(bar) folds one closed
    1-minute bar in; every attribute then describes closes up to and
    including that bar."""

    def __init__(self):
        self.count = 0
        self.closes = []
        self.prev_close = None
        self.ema20 = Ema(20)
        self.ema50 = Ema(50)
        self.ema_macd_fast = Ema(12)
        self.ema_macd_slow = Ema(26)
        self.ema_macd_sig = Ema(9)
        self.wilder_atr10 = Wilder(10)
        self.wilder_up = Wilder(14)
        self.wilder_dn = Wilder(14)
        self.wilder_pdm = Wilder(14)
        self.wilder_mdm = Wilder(14)
        self.wilder_tr14 = Wilder(14)
        self.wilder_dx = Wilder(14)
        self.bb = Rolling(20)
        self.mean60 = Rolling(60)
        self.lows20 = []
        self.vwap_total = 0.0
        self.high24 = None
        self.low24 = None
        self.high48 = None
        self.low48 = None
        self.five_open = None
        self.five_high = None
        self.five_low = None
        self.five_count = 0
        self.five_range_raw = None
        self.super_up = None
        self.super_dn = None
        self.super_trend = 1
        # current values, refreshed by feed()
        self.close = None
        self.atr14 = None
        self.atr10 = None
        self.rsi = None
        self.adx = None
        self.di_plus = None
        self.di_minus = None
        self.pct_b = None
        self.vwap = None
        self.activity = None
        self.macd_hist = None
        self.macd_hist_prev = None

    def feed(self, bar):
        """bar: dict with int-like o/h/l/c strings (price-scale units)."""
        close = float(int(bar["c"]))
        high = float(int(bar["h"]))
        low = float(int(bar["l"]))
        self.close = close
        self.count += 1
        prev_close = self.prev_close
        self.prev_close = close
        self.closes.append(close)
        if prev_close is None:
            prev_close = close
        # --- true range and Wilder family ---
        true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr14 = self.wilder_tr14.feed(true_range)
        self.atr14 = tr14
        atr10 = self.wilder_atr10.feed(true_range)
        self.atr10 = atr10
        change = close - prev_close
        up = change if change > 0.0 else 0.0
        down = -change if change < 0.0 else 0.0
        avg_up = self.wilder_up.feed(up)
        avg_dn = self.wilder_dn.feed(down)
        if avg_up is not None and avg_dn is not None:
            denom = avg_dn if avg_dn > 0.0 else 1e-12
            self.rsi = 100.0 - 100.0 / (1.0 + avg_up / denom)
        up_move = high - self._prev_high if hasattr(self, "_prev_high") else 0.0
        down_move = self._prev_low - low if hasattr(self, "_prev_low") else 0.0
        self._prev_high = high
        self._prev_low = low
        plus_dm = up_move if (up_move > down_move and up_move > 0.0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0.0) else 0.0
        avg_pdm = self.wilder_pdm.feed(plus_dm)
        avg_mdm = self.wilder_mdm.feed(minus_dm)
        if tr14 is not None and avg_pdm is not None and avg_mdm is not None:
            self.di_plus = 100.0 * avg_pdm / tr14 if tr14 > 0.0 else 0.0
            self.di_minus = 100.0 * avg_mdm / tr14 if tr14 > 0.0 else 0.0
            denom = self.di_plus + self.di_minus
            dx = 100.0 * abs(self.di_plus - self.di_minus) / denom if denom > 0.0 else 0.0
            self.adx = self.wilder_dx.feed(dx)
        # --- EMAs ---
        ema20 = self.ema20.feed(close)
        ema50 = self.ema50.feed(close)
        self.ema20_now = ema20
        self.ema50_now = ema50
        macd_line = self.ema_macd_fast.feed(close) - self.ema_macd_slow.feed(close)
        macd_sig = self.ema_macd_sig.feed(macd_line)
        self.macd_hist_prev = self.macd_hist
        self.macd_hist = macd_line - macd_sig
        # --- Bollinger 20,2 and 60-bar mean ---
        mean20, std20 = self.bb.feed(close)
        self.mean60.feed(close)
        upper = mean20 + 2.0 * std20
        lower = mean20 - 2.0 * std20
        self.pct_b = (close - lower) / (upper - lower) if upper > lower else 0.5
        # --- session VWAP (equal weight: no volume in the bars) ---
        typical = (high + low + close) / 3.0
        self.vwap_total += typical
        self.vwap = self.vwap_total / self.count
        # --- activity proxy (bar range versus ATR, volume is absent) ---
        self.activity = (high - low) / tr14 if tr14 else None
        # --- expanding structural highs/lows ---
        self.high24 = high if self.high24 is None else max(self.high24, high)
        self.low24 = low if self.low24 is None else min(self.low24, low)
        self.high48 = self.high24
        self.low48 = self.low24
        # --- Supertrend 10,3 (bands ratchet one way only) ---
        if atr10 is not None:
            mid = (high + low) / 2.0
            basic_up = mid - 3.0 * atr10
            basic_dn = mid + 3.0 * atr10
            if self.super_up is None:
                self.super_up = basic_up
                self.super_dn = basic_dn
            else:
                self.super_up = basic_up if basic_up > self.super_up else self.super_up
                self.super_dn = basic_dn if basic_dn < self.super_dn else self.super_dn
            if self.super_trend == 1 and close < self.super_up:
                self.super_trend = -1
            elif self.super_trend == -1 and close > self.super_dn:
                self.super_trend = 1
        # --- 5-minute aggregation (completed bars only) ---
        if self.five_open is None:
            self.five_open = float(int(bar["o"]))
            self.five_high = high
            self.five_low = low
        else:
            self.five_high = max(self.five_high, high)
            self.five_low = min(self.five_low, low)
        self.five_count += 1
        if self.five_count == 5:
            # store the raw range; the ratio uses the CURRENT ATR at decision
            # time, exactly like a ratio against the live ATR.
            self.five_range_raw = self.five_high - self.five_low
            self.five_open = None
            self.five_count = 0

    def five_range_ratio(self):
        """Last completed 5-minute bar range versus the current ATR 14."""
        if self.five_range_raw is None or not self.atr14:
            return None
        return self.five_range_raw / self.atr14

    # -- derived, lookback-based readings (all use closed bars only) --
    def ret_bars_ago(self, n):
        if len(self.closes) <= n:
            base = self.closes[0]
        else:
            base = self.closes[-1 - n]
        return (self.close / base - 1.0) * 10000.0 if base else 0.0

    def range_position(self, high, low):
        if high is None or low is None or high <= low:
            return 0.5
        return (self.close - low) / (high - low)

    def near_donchian_low_bps(self):
        if len(self.lows20) < 20:
            return None
        return (self.close - min(self.lows20)) / self.close * 10000.0

    def note_donchian_bar(self, bar):
        """Record this bar's low for the NEXT bar's Donchian window."""
        self.lows20.append(float(int(bar["l"])))
        if len(self.lows20) > 20:
            self.lows20.pop(0)


def gate_open(feed):
    """True when the washout-bounce setup is fully armed on the current bar.

    Hard conditions (every one must hold), then a regime vote of 5/8.
    Nothing here is a single-indicator trigger: RSI oversold and EMA layout
    are only members of a wider confirmation set, and no EMA cross is used.
    """
    if feed.close is None or feed.rsi is None or feed.adx is None:
        return False
    if feed.macd_hist is None or feed.macd_hist_prev is None:
        return False
    if feed.atr14 is None or feed.pct_b is None or feed.activity is None:
        return False
    # --- hard conditions ---
    if feed.ret_bars_ago(15) > -BUY_DROP_BPS:
        return False
    if feed.rsi >= RSI_MAX:
        return False
    if feed.pct_b >= PCTB_MAX:
        return False
    if len(feed.closes) < 2 or feed.close <= feed.closes[-2]:
        return False  # the minute itself must turn up
    if feed.macd_hist <= feed.macd_hist_prev:
        return False  # histogram must be turning up, not still falling
    if feed.activity < ACT_MIN:
        return False
    five_ratio = feed.five_range_ratio()
    if five_ratio is None or five_ratio < FIVEM_RANGE_MIN:
        return False  # the completed 5-minute bar must be a climax bar
    if feed.range_position(feed.high24, feed.low24) > RANGE_POS_MAX:
        return False
    if feed.range_position(feed.high48, feed.low48) > RANGE_POS_MAX:
        return False
    if feed.ret_bars_ago(360) > MOM6H_MAX_BPS:
        return False
    # --- regime vote: at least 5 of 8 must agree ---
    near_low = feed.near_donchian_low_bps()
    votes = 0
    votes += 1 if feed.ema20_now < feed.ema50_now else 0
    votes += 1 if (near_low is not None and near_low <= 25.0) else 0
    votes += 1 if feed.close < feed.vwap else 0
    votes += 1 if (feed.atr14 / feed.close * 10000.0) > 8.0 else 0
    votes += 1 if feed.adx > 20.0 else 0
    votes += 1 if feed.di_minus > feed.di_plus else 0
    votes += 1 if feed.super_trend == -1 else 0
    votes += 1 if feed.ret_bars_ago(60) <= -150.0 else 0
    return votes >= VOTES_NEEDED


class Trader:
    """One-position trader around a Feed: edge-triggered entries, cost-based
    exits, a short rest after every exit."""

    def __init__(self, hello):
        self.feed = Feed()
        symbols = [s["symbol"] for s in hello["symbols"]]
        self.symbol = SYMBOL if SYMBOL in symbols else symbols[0]
        self.quote_scale = int(hello["capital"]["quote_scale"])
        self.min_notional = int(hello["fees"]["min_notional_quote_units"])
        self.prev_gate = False
        self.entry_seq = None
        self.entry_cost = None
        self.cool_until = -1
        self.waiting_flat = False
        self.pending_buy = False
        self.had_position = False

    def average_cost_price(self, state):
        """Average filled cost per unit, in price-scale units, from the
        harness-reported position (cost includes fees)."""
        holding = state.positions.get(self.symbol)
        if holding is None or holding.qty <= 0:
            return None
        spec = state.symbols[self.symbol]
        scale = 10 ** (int(spec["qty_scale"]) + int(spec["price_scale"]))
        return holding.cost * scale / (holding.qty * (10 ** self.quote_scale))

    def on_batch(self, state, batch):
        bar = state.latest.get(self.symbol)
        if bar is None:
            return []
        self.feed.feed(bar)
        gate = gate_open(self.feed)
        orders = []
        holding = state.positions.get(self.symbol)
        # --- learn what last batch's orders did ---
        if self.pending_buy and holding is None:
            # the buy did not fill: let the setup fire again on its next edge
            self.prev_gate = False
        self.pending_buy = False
        if self.waiting_flat and holding is None:
            self.cool_until = state.seq + COOLDOWN_BARS
            self.waiting_flat = False
        if holding is not None and not self.had_position:
            self.entry_seq = state.seq
            self.entry_cost = self.average_cost_price(state)
        self.had_position = holding is not None
        if state.warming_up:
            self.prev_gate = gate
            self.feed.note_donchian_bar(bar)
            return []
        if holding is not None:
            cost = self.entry_cost
            if cost is None:
                cost = self.average_cost_price(state)
            held = state.seq - (self.entry_seq if self.entry_seq is not None else state.seq)
            if cost is not None and self.feed.close is not None:
                if self.feed.close >= cost * (1.0 + TAKE_BPS / 10000.0):
                    orders.append({"side": "sell", "symbol": self.symbol,
                                   "qty": holding.qty, "max_slippage_bps": MAX_SLIPPAGE_BPS})
                elif self.feed.close <= cost * (1.0 - STOP_BPS / 10000.0):
                    orders.append({"side": "sell", "symbol": self.symbol,
                                   "qty": holding.qty, "max_slippage_bps": MAX_SLIPPAGE_BPS})
                elif held >= MAX_HOLD_BARS:
                    orders.append({"side": "sell", "symbol": self.symbol,
                                   "qty": holding.qty, "max_slippage_bps": MAX_SLIPPAGE_BPS})
            if orders:
                self.waiting_flat = True
        elif state.seq >= self.cool_until:
            if gate and not self.prev_gate:
                room = state.weight_room(self.symbol)
                spend = state.cash if state.cash < room else room
                if spend >= self.min_notional and spend > 0:
                    orders.append({"side": "buy", "symbol": self.symbol,
                                   "notional_quote_units": int(spend),
                                   "max_slippage_bps": MAX_SLIPPAGE_BPS})
                    self.pending_buy = True
        self.prev_gate = gate
        self.feed.note_donchian_bar(bar)
        return orders


def main():
    trader = None

    def on_hello(state):
        nonlocal trader
        trader = Trader(state.hello)

    def on_batch(state, batch):
        return trader.on_batch(state, batch)

    makefun_bars.run(on_batch, on_hello=on_hello)


if __name__ == "__main__":
    main()
