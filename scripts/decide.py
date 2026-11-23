#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""a-share-position 主入口：取数 -> 计算 -> 渲染决策卡图片。

用法：
  python3 scripts/decide.py                       # 今日决策卡（PNG）
  python3 scripts/decide.py --png out.png         # 指定输出路径
  python3 scripts/decide.py --text                # 同时打印纯文本版（兜底用）
  python3 scripts/decide.py --json                # 附带结构化结果
  python3 scripts/decide.py --cost 3680.00        # 覆盖持仓成本
  python3 scripts/decide.py --offline-dir DIR     # 离线数据目录（index.csv + breadth.csv）
  python3 scripts/decide.py --asof 2026-11-09     # 指定日期（需配合 --offline-dir）
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import engine  # noqa: E402
import fetch   # noqa: E402

try:
    import render as render_mod  # noqa: E402
except Exception:  # noqa: BLE001
    render_mod = None

SKILL_DIR = os.path.dirname(HERE)
DATA_DIR = os.path.join(SKILL_DIR, "data")
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
SPARK = "▁▂▃▄▅▆▇█"

TITLES = {"buy": "果断建仓", "hold": "耐心等待", "wait": "耐心等待", "sell": "立即离场"}
BADGES = {"buy": "买入信号", "sell": "卖出信号"}
DESCS = {
    "buy": "今日触发建仓信号，建议收盘前提升至 {pct}% 仓位",
    "hold": "今日未触发任何买卖信号，建议维持 {pct}% 仓位不动",
    "wait": "今日未触发任何买卖信号，建议保持空仓，等待下一次机会",
    "sell": "今日触发离场信号，建议收盘前清仓，仓位归零",
}

B1_DESC = "满仓择时策略，仅适用于趋势波段投资者，不适合短线情绪投资者"
B0_DESC = "持有周期小于 2 日，2 日后应严格清仓"
EXIT_PILL = {"trend": "逃顶", "risk": "止损", "window": "清仓", "open": None}


# ------------------------------------------------------------------ 历史温度

def history_file():
    for base in (DATA_DIR, os.path.join(os.path.expanduser("~"), ".a-share-position")):
        path = os.path.join(base, "temp_history.csv")
        if os.path.exists(path):
            return path
        try:
            os.makedirs(base, exist_ok=True)
            return path
        except OSError:
            continue
    return os.path.join(DATA_DIR, "temp_history.csv")


def load_history():
    rows = {}
    for path in (os.path.join(DATA_DIR, "temp_seed.csv"), history_file()):
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r.get("date"):
                        rows[r["date"]] = r
        except OSError:
            continue
    return rows


HEADER = ["date", "temp", "band", "breadth_pct", "zt", "dt",
          "idx_avg", "up", "down", "flat"]


def save_history(row, path):
    """按日期覆盖写入：同一交易日重复运行只保留最后一次（盘中 → 收盘）。"""
    rows = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8-sig") as f:
                rows = [r for r in csv.reader(f)
                        if r and r[0] != HEADER[0] and r[0] != row[0]]
        except OSError:
            rows = []
    rows.append(row)
    rows.sort(key=lambda r: r[0])
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            w.writerows(rows)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ 文案

def sparkline(values):
    if not values:
        return ""
    out = []
    for v in values:
        idx = int(round(max(0.0, min(100.0, v)) / 100 * (len(SPARK) - 1)))
        out.append(SPARK[idx])
    return "".join(out)


def session_label(now):
    if now.hour * 60 + now.minute < 9 * 60 + 30:
        return "未开盘"
    if now.hour * 60 + now.minute < 15 * 60:
        return "盘中"
    return "已收盘"


def commentary(state, vc, breadth, vol_yi, closed=True):
    close, chg = state["close"], state["chg_pct"]
    pts = close - state["prev_close"]
    if chg > 0:
        word = "涨"
    elif chg < 0:
        word = "跌"
    else:
        word = "平"
    verb = "收于" if closed else "最新报"
    s = [f"上证指数{verb} {close:.2f} 点，{word} {abs(pts):.2f} 点（{chg:+.2f}%），"
         f"日内 {state['low']:.2f} 至 {state['high']:.2f}。"]

    if closed:
        ratio = 100 + vc["vol_ratio"]
        s.append(f"成交 {vol_yi:.2f} 亿股，为 20 日均量的 {ratio:.1f}%。")
    else:
        s.append(f"成交 {vol_yi:.2f} 亿股（盘中，尚未收盘）。")

    s.append(f"两市 {breadth['up']} 涨、{breadth['down']} 跌，"
             f"涨停 {breadth['zt']} 家、跌停 {breadth['dt']} 家。")

    action = state["action"]
    if action == "buy":
        s.append(f"今日触发建仓条件，止损参考线设在 {state['stop']:.2f}。")
    elif action == "sell":
        s.append("今日触发离场条件，仓位归零，不做补仓。")
    elif state["holding"]:
        s.append(f"当前处于持仓周期第 {state['days_held']} 个交易日，"
                 f"止损参考线 {state['stop']:.2f} 尚未触及。")
    else:
        s.append("当前处于空仓状态，等待下一次建仓条件成立。")
    return "".join(s)


def text_card(res):
    lines = [
        "择时 · 仓位建议",
        "─" * 30,
        f"{res['date']}  {res['as_of_line']}",
        "",
        f"市场温度 {res['temp']:.1f} / 100  {res['band']}",
    ]
    if res.get("prev_temp") is not None:
        lines.append(f"前值 {res['prev_date']} {res['prev_band']} {res['prev_temp']:.1f}")
    lines += [
        f"市场广度 {res['breadth_pct']:.1f}%  ·  涨停/跌停 {res['zt']} / {res['dt']}"
        f"  ·  四指数均涨 {res['idx_avg']:+.2f}%",
        f"上证指数 {res['index_close']:.2f}  {res['index_chg']:+.2f}%",
        "",
        f"今日仓位建议  ·  {res['badge']}",
        f"建议仓位 {res['position_pct']}%",
        res["title"],
        res["desc"],
        f"持仓状态 {res['holding_state']}  ·  浮动盈亏 {res['pnl_text']}",
        f"持仓成本 {res['cost_text']}  ·  止损参考线 {res['stop_text']}",
        "",
        f"市场点评（{res['date']}）",
        res["commentary"],
        "",
        "风险提示：本策略为满仓择时策略，仅适用于趋势波段投资者，不适合短线情绪投资者。",
        "以上建议由规则自动生成，仅供参考，不构成投资建议。",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ 主流程

def build(args):
    now = dt.datetime.now()
    session = session_label(now)
    gap_days = 0

    if args.offline_dir:
        bars, breadth_map = fetch.load_offline(args.offline_dir)
        if args.asof:
            bars = [b for b in bars if b["d"] <= args.asof]
        day = bars[-1]["d"]
        session = "已收盘"
        b = breadth_map.get(day)
        if not b:
            raise SystemExit(f"离线数据缺少 {day} 的广度记录")
        breadth = {"up": b["up"], "down": b["down"], "flat": b["flat"],
                   "zt": b["zt"], "dt": b["dt"]}
        idx_avg = b["idx_avg"]
    else:
        bars = fetch.fetch_bars(fetch.MAIN_INDEX, limit=340)
        live = None
        for attempt in range(3):                # 实时快照偶发失败，重试
            try:
                live = fetch.fetch_live_bar(fetch.MAIN_INDEX)
            except Exception:  # noqa: BLE001
                live = None
            if live and live.get("c"):
                break
            if attempt < 2:
                time.sleep(0.8)
        if live and live["c"]:
            if live["d"] > bars[-1]["d"]:
                bars.append(live)               # 行情源缺当日 K 线时补齐
            elif live["d"] == bars[-1]["d"]:
                bars[-1] = live                 # 盘中刷新当日 K 线
        day = bars[-1]["d"]
        if args.asof and args.asof < day:
            raise SystemExit("指定历史日期需配合 --offline-dir 使用")
        breadth = fetch.fetch_breadth(day.replace("-", ""))
        changes = fetch.fetch_index_changes()
        vals = [v for v in changes.values() if v is not None]
        idx_avg = sum(vals) / len(vals) if vals else 0.0

    state = engine.analyze(bars)
    temp_info = engine.market_temp(breadth["up"], breadth["down"],
                                   breadth["zt"], breadth["dt"], idx_avg)
    temp, band = temp_info["temp"], temp_info["band"]

    hist = load_history()
    hist = {d: r for d, r in hist.items() if d <= day}
    prev_rows = sorted((d, r) for d, r in hist.items() if d < day)
    prev_temp = float(prev_rows[-1][1]["temp"]) if prev_rows else None
    prev_date = prev_rows[-1][0] if prev_rows else None
    prev_band = prev_rows[-1][1]["band"] if prev_rows else None

    hist[day] = {"date": day, "temp": f"{temp:.1f}", "band": band,
                 "breadth_pct": f"{temp_info['breadth_pct']:.1f}",
                 "zt": str(breadth["zt"]), "dt": str(breadth["dt"]),
                 "idx_avg": f"{idx_avg:.2f}", "up": str(breadth["up"]),
                 "down": str(breadth["down"]), "flat": str(breadth["flat"])}
    ordered = sorted(hist)
    tail_dates = ordered[-10:]
    tail_temps = [float(hist[d]["temp"]) for d in tail_dates]

    gap_days = sum(1 for b in bars if prev_date and prev_date < b["d"] < day)

    # 数据日期早于今天时，无论本地时钟如何，都按「该日已收盘」处理
    closed = session != "盘中" or day != now.date().isoformat()
    vc = engine.vol_context(bars)
    action = state["action"]
    pct = state["position_pct"]
    d = dt.date.fromisoformat(day)
    md = f"{d.month:02d}-{d.day:02d}"

    cost = args.cost if args.cost else state["entry_price"]
    pnl = (state["close"] / cost - 1) * 100 if cost else None
    stop = state["stop"] if state["holding"] else None

    entry_date = state["entry_date"]
    entry_md = (dt.date.fromisoformat(entry_date).strftime("%m-%d")
                if entry_date else md)

    if action == "buy":
        holding_state = f"B1 信号日 · {md} 建仓"
    elif action == "hold":
        holding_state = f"B1 持仓中 · {entry_md} 建仓"
    elif action == "sell":
        holding_state = f"已离场 · {md} 清仓"
    else:
        holding_state = "空仓"

    pnl_text = f"{pnl:+.2f}%" if pnl is not None else "—"
    tone = None
    if pnl is not None:
        tone = "pos" if pnl > 0 else ("neg" if pnl < 0 else None)

    last_exit = state.get("last_exit_date")
    last_exit_md = dt.date.fromisoformat(last_exit).strftime("%m-%d") if last_exit else None
    flat_days = (sum(1 for b in bars if last_exit and b["d"] > last_exit)
                 if last_exit else None)

    if action in ("buy", "hold"):
        grid = [("持仓状态", holding_state, None),
                ("浮动盈亏", pnl_text, tone),
                ("持仓成本", f"{cost:.2f}" if cost else "—", None),
                ("止损参考线", f"{stop:.2f}" if stop else "—", None)]
    elif action == "sell":
        reason = {"trend": "逃顶", "risk": "止损", "window": "清仓"}.get(
            state.get("exit_reason"), "—")
        grid = [("持仓状态", holding_state, None),
                ("本笔盈亏", pnl_text, tone),
                ("持仓成本", f"{cost:.2f}" if cost else "—", None),
                ("离场原因", reason, None)]
    else:
        grid = [("持仓状态", "空仓", None),
                ("建议仓位", "0%", None),
                ("最近离场", last_exit_md or "—", None),
                ("已空仓", f"{flat_days} 个交易日" if flat_days is not None else "—", None)]
    result = {
        "date": day,
        "session": session,
        "closed": closed,
        "as_of_line": f"上证指数 SH000001 · 截至 {md} {'收盘' if closed else session}",
        "action": action,
        "badge": BADGES.get(action, "无信号"),
        "position_pct": pct,
        "title": TITLES[action],
        "desc": DESCS[action].format(pct=pct),
        "temp": temp,
        "band": band,
        "temp_prev": prev_temp,
        "temp_prev_date": prev_date,
        "prev_temp": prev_temp,
        "prev_date": prev_date,
        "prev_band": prev_band,
        "prev_is_prev": gap_days == 0,
        "breadth_pct": temp_info["breadth_pct"],
        "up": breadth["up"],
        "down": breadth["down"],
        "zt": breadth["zt"],
        "dt": breadth["dt"],
        "idx_avg": round(idx_avg, 2),
        "index_close": round(state["close"], 2),
        "index_chg": round(state["chg_pct"], 2),
        "chg_pts": round(state["close"] - state["prev_close"], 2),
        "high": state["high"],
        "low": state["low"],
        "volume_yi": state["volume"] / 1e8,
        "vol_ratio_pct": 100 + vc["vol_ratio"],
        "grid": grid,
        "holding": state["holding"],
        "holding_state": holding_state,
        "entry_date": entry_date,
        "cost": cost,
        "cost_text": f"{cost:.2f}" if cost else "—",
        "pnl_pct": round(pnl, 2) if pnl is not None else None,
        "pnl_text": pnl_text,
        "stop": round(stop, 2) if stop else None,
        "stop_text": f"{stop:.2f}" if stop else "—",
        "days_held": state["days_held"],
        "b1_state": "已触发" if action == "buy" else "未触发",
        "b1_desc": B1_DESC,
        "b0_state": "未触发",
        "b0_desc": B0_DESC,
        "exit_state": "已触发" if action == "sell" else "未触发",
        "exit_pill": EXIT_PILL.get(state.get("exit_reason")) if action == "sell" else None,
        "spark": tail_temps,
    }
    result["commentary"] = commentary(state, vc, breadth, result["volume_yi"], closed)
    result["card"] = text_card(result)
    return result, [day, f"{temp:.1f}", band, f"{temp_info['breadth_pct']:.1f}",
                    breadth["zt"], breadth["dt"], f"{idx_avg:.2f}",
                    breadth["up"], breadth["down"], breadth["flat"]]


def default_png_path(day):
    out_dir = os.path.join(SKILL_DIR, "output")
    try:
        os.makedirs(out_dir, exist_ok=True)
        if os.access(out_dir, os.W_OK):
            return os.path.join(out_dir, f"position-card-{day}.png")
    except OSError:
        pass
    return os.path.join(tempfile.gettempdir(), "a-share-position", f"position-card-{day}.png")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="A股仓位管理：输出决策卡图片")
    ap.add_argument("--png", nargs="?", const="", help="生成 PNG，可指定路径")
    ap.add_argument("--scale", type=int, default=2, help="渲染倍率，默认 2")
    ap.add_argument("--text", action="store_true", help="同时打印纯文本版")
    ap.add_argument("--no-image", action="store_true", help="只输出文本")
    ap.add_argument("--json", action="store_true", help="附带输出 JSON")
    ap.add_argument("--cost", type=float, help="覆盖持仓成本")
    ap.add_argument("--asof", help="指定日期 YYYY-MM-DD（需 --offline-dir）")
    ap.add_argument("--offline-dir", help="离线数据目录")
    ap.add_argument("--no-save", action="store_true", help="不写入温度历史")
    args = ap.parse_args()

    try:
        result, hist_row = build(args)
    except fetch.DataError as exc:
        print(f"行情源暂时不可用，请稍后重试。（{exc}）", file=sys.stderr)
        sys.exit(2)
    except ValueError as exc:
        print(f"历史数据不足，无法计算仓位状态。（{exc}）", file=sys.stderr)
        sys.exit(2)

    want_image = not args.no_image
    if args.png is not None:
        want_image = True

    image_ok = False
    if want_image and render_mod is not None and render_mod.available():
        path = args.png or default_png_path(result["date"])
        try:
            path, w, h = render_mod.render(result, path, scale=max(1, args.scale))
            print(f"IMAGE: {os.path.abspath(path)}")
            print(f"SIZE: {w}x{h}")
            image_ok = True
        except Exception as exc:  # noqa: BLE001
            print(f"图片渲染失败：{type(exc).__name__}: {exc}", file=sys.stderr)

    if args.text or not image_ok:
        print(result["card"])

    if not args.no_save and not args.offline_dir:
        save_history(hist_row, history_file())

    if args.json:
        payload = {k: v for k, v in result.items() if k != "card"}
        print("\n---JSON---")
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
