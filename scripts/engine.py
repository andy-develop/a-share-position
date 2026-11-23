#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""内部计算引擎（a-share-position）。

职责：
  1) 市场温度 0-100 与档位划分；
  2) 仓位状态机：空仓 / 持仓 / 建仓 / 离场 的逐日判定。

注意：本文件包含全部内部判定细节，属于实现层。
面向用户的输出只允许出现最终结论，不得出现本文件的任何术语与参数。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 温度模型

S1_PTS = [(0.0, 0.0), (0.10, 10.0), (0.30, 25.0), (0.50, 50.0), (0.70, 75.0), (0.90, 100.0)]
ZT_PTS = [(20.0, 0.0), (30.0, 10.0), (120.0, 100.0)]
DT_PTS = [(0.0, 65.0), (5.0, 55.0), (12.0, 45.0), (25.0, 25.0),
          (50.0, 12.0), (100.0, 6.0), (200.0, 0.0)]
S3_PTS = [(-10.0, 0.0), (-9.0, 2.0), (-8.0, 4.0), (-7.0, 5.5), (-6.0, 7.0), (-5.0, 8.5),
          (-3.5, 10.0), (-2.5, 20.0), (-1.5, 30.0), (-0.5, 40.0),
          (0.5, 50.0), (1.5, 60.0), (2.5, 70.0), (3.5, 80.0), (4.5, 90.0),
          (6.0, 95.0), (7.5, 100.0)]

BANDS = [(10.0, "极度冰点"), (20.0, "冰点"), (40.0, "低温"),
         (60.0, "常温"), (80.0, "偏热"), (90.0, "高温"), (100.0, "极热")]

TEMP_WEIGHTS = (0.40, 0.20, 0.40)   # S1 广度 / S2 涨跌停 / S3 指数均涨


def piece(x: float, pts) -> float:
    """分段线性插值，超出两端取值域端点。"""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x <= x2:
            if x2 == x1:
                return y2
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return pts[-1][1]


def band_of(t: float) -> str:
    for edge, name in BANDS:
        if t <= edge:
            return name
    return "极热"


def market_temp(up: int, down: int, zt: int, dt: int, idx_avg: float) -> dict:
    """市场温度。

    up/down 为上涨/下跌家数；zt/dt 为涨停/跌停家数；idx_avg 为四指数平均涨跌幅（%）。
    返回 {"temp","band","s1","s2","s3","breadth_pct"}。
    """
    total = up + down
    breadth = up / total if total > 0 else 0.0
    s1 = piece(breadth, S1_PTS)
    s2 = (piece(float(zt), ZT_PTS) + piece(float(dt), DT_PTS)) / 2.0
    s3 = piece(float(idx_avg), S3_PTS)
    w1, w2, w3 = TEMP_WEIGHTS
    t = w1 * s1 + w2 * s2 + w3 * s3
    return {
        "temp": round(t, 1),
        "band": band_of(round(t, 1)),
        "s1": round(s1, 1),
        "s2": round(s2, 1),
        "s3": round(s3, 1),
        "breadth_pct": round(breadth * 100, 1),
    }


# ---------------------------------------------------------------- 仓位状态机

MA_FAST, MA_MID, MA_SLOW = 5, 10, 20   # 三条参考线
GAP_OPEN = 3.0                          # 转折需确认的最小间距（点）
GAP_MARK = 55.0                         # 下行确认间距（点）
HOLD_WINDOW = 15                        # 持仓窗口（交易日）
STOP_LOOKBACK = 5                       # 风控位取样回看（交易日）


def _ma(series, n, i):
    if i < n - 1:
        return None
    return sum(series[i - n + 1:i + 1]) / n


def analyze(bars, hold_cap: int = HOLD_WINDOW) -> dict:
    """对日线序列做逐日状态推进，返回最后一根 K 线收盘后的仓位状态。

    bars: [{"d","o","h","l","c","v"}, ...] 按日期升序，长度需 >= 60（建议 >= 300）。
    """
    if len(bars) < MA_SLOW + 2:
        raise ValueError("历史数据不足，无法计算仓位状态")

    dates = [b["d"] for b in bars]
    close = [float(b["c"]) for b in bars]
    low = [float(b["l"]) for b in bars]
    n = len(bars)

    ma_f = [_ma(close, MA_FAST, i) for i in range(n)]
    ma_m = [_ma(close, MA_MID, i) for i in range(n)]
    ma_s = [_ma(close, MA_SLOW, i) for i in range(n)]

    # 转折点：快线首次同时落到两条参考线下方，且间距达到确认阈值
    events = {}          # index -> "strong" / "weak"
    prev_below = False
    for i in range(MA_SLOW, n):
        below = ma_f[i] < ma_m[i] and ma_f[i] < ma_s[i]
        if below and not prev_below and (min(ma_m[i], ma_s[i]) - ma_f[i]) > GAP_OPEN:
            strong = close[i] <= max(ma_f[i], ma_m[i], ma_s[i])
            events[i] = "strong" if strong else "weak"
        prev_below = below

    def is_strong(i):
        return events.get(i) == "strong"

    pos = None
    signals = []
    stop_pending = False     # 风险离场后等待重新武装
    rearmed = False
    anchor = None            # 最近转折点
    mark = None              # 下行确认点
    last_exit = None

    for i in range(MA_SLOW, n):
        if pos:
            # 窗口内的弱转折只记录，不触发离场
            if hold_cap and i - pos["i"] <= hold_cap and i in events:
                pos["extended"] = True
            # 风险离场：连续两日收盘低于风控位
            if close[i] < pos["stop"] and close[i - 1] < pos["stop"]:
                pos.update(exit_i=i, exit=close[i], reason="risk")
                signals.append(pos)
                pos, stop_pending, rearmed = None, True, False
                last_exit = i
                continue
            # 转折离场
            if is_strong(i):
                pos.update(exit_i=i, exit=close[i], reason="trend")
                signals.append(pos)
                pos, anchor, mark, stop_pending, rearmed = None, i, None, False, False
                last_exit = i
                continue
            # 窗口到期
            if hold_cap and i - pos["i"] >= hold_cap and not pos["extended"]:
                pos.update(exit_i=i, exit=close[i], reason="window")
                signals.append(pos)
                pos, stop_pending, rearmed = None, False, False
                last_exit = i
                continue
            continue

        if is_strong(i):
            anchor, mark, rearmed, stop_pending = i, None, False, False

        if stop_pending and (ma_s[i] - ma_f[i]) > GAP_MARK:
            rearmed = True

        if anchor is not None and mark is None and i > anchor and (ma_s[i] - ma_f[i]) > GAP_MARK:
            mark = i

        ready = (mark is not None and i > mark) or rearmed
        if ready and ma_f[i] > ma_f[i - 1]:
            stop = min(low[max(0, i - STOP_LOOKBACK):i + 1])
            pos = {"i": i, "d": dates[i], "entry": close[i], "stop": stop, "extended": False}
            mark, rearmed, stop_pending, anchor = None, False, False, None

    if pos:
        signals.append({**pos, "exit_i": None, "exit": None, "reason": "open"})

    last_i = n - 1
    last = signals[-1] if signals else None
    state = {
        "date": dates[last_i],
        "close": close[last_i],
        "prev_close": close[last_i - 1],
        "chg_pct": (close[last_i] / close[last_i - 1] - 1) * 100,
        "high": float(bars[last_i]["h"]),
        "low": float(bars[last_i]["l"]),
        "volume": float(bars[last_i].get("v") or 0),
        "signals": len(signals),
        "holding": False,
        "entry_date": None,
        "entry_price": None,
        "stop": None,
        "days_held": None,
        "action": "wait",
        "exit_reason": None,
        "last_exit_date": dates[last_exit] if last_exit is not None else None,
    }

    if last and last["i"] == last_i:
        state.update(holding=True, entry_date=dates[last_i], entry_price=close[last_i],
                     stop=last["stop"], days_held=0, action="buy")
    elif last and last["exit_i"] is None:
        state.update(holding=True, entry_date=last["d"], entry_price=last["entry"],
                     stop=last["stop"], days_held=last_i - last["i"], action="hold")
    elif last and last["exit_i"] == last_i:
        state.update(action="sell", exit_reason=last["reason"],
                     entry_date=last["d"], entry_price=last["entry"], stop=last["stop"])

    state["position_pct"] = 100 if state["holding"] else 0
    return state


def vol_context(bars, window: int = 20) -> dict:
    """量能与近端区间，用于点评文案（不含任何指标名称）。"""
    vols = [float(b.get("v") or 0) for b in bars]
    last = vols[-1]
    # 基准取「此前 20 个交易日」的均量，避免当日自身稀释比较口径
    prev = [v for v in vols[-window - 1:-1] if v > 0]
    base = sum(prev) / len(prev) if prev else 0.0
    prev_window = bars[-window - 1:-1] if len(bars) > window else bars[-window:]
    highs = [float(b["h"]) for b in prev_window]
    lows = [float(b["l"]) for b in prev_window]
    closes = [float(b["c"]) for b in prev_window]
    return {
        "vol_ratio": (last / base - 1) * 100 if base else 0.0,
        "range_high": max(highs),
        "range_low": min(lows),
        "close_high": max(closes),
        "close_low": min(closes),
        "avg_volume": base,
    }
