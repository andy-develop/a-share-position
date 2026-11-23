#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行情取数层（a-share-position）。

只依赖 Python 标准库，任何装有 Python 3.8+ 的环境都能运行。
主源：东方财富公开行情接口；备用源：新浪财经、腾讯财经。
"""

from __future__ import annotations

import csv
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/", "Accept": "*/*"}

INDEXES = {
    "sh": ("1.000001", "sh000001"),      # 上证指数
    "szc": ("0.399001", "sz399001"),     # 深证成指
    "cyb": ("0.399006", "sz399006"),     # 创业板指
    "kc50": ("1.000688", "sh000688"),    # 科创50
}
MAIN_INDEX = "sh"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


class DataError(RuntimeError):
    pass


def _get(url, timeout=20, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.6 * (attempt + 1))
    raise DataError(f"请求失败: {type(last).__name__}: {last}")


def _get_json(url, timeout=20, retries=2):
    raw = _get(url, timeout=timeout, retries=retries)
    raw = raw.strip()
    if raw.startswith("["):                      # 新浪返回裸数组
        return json.loads(raw)
    return json.loads(raw)


# ------------------------------------------------------------------ 日线

def _bars_em(secid, limit):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
           f"&klt=101&fqt=1&end=20500101&lmt={limit}")
    data = _get_json(url)
    klines = ((data or {}).get("data") or {}).get("klines") or []
    bars = []
    for line in klines:
        p = line.split(",")
        # 该接口成交量单位为手，统一换算为股
        bars.append({"d": p[0], "o": float(p[1]), "c": float(p[2]),
                     "h": float(p[3]), "l": float(p[4]), "v": float(p[5]) * 100})
    return bars


def _bars_sina(symbol, limit):
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={limit}")
    rows = _get_json(url)
    return [{"d": r["day"][:10], "o": float(r["open"]), "c": float(r["close"]),
             "h": float(r["high"]), "l": float(r["low"]), "v": float(r["volume"])}
            for r in rows]


def _bars_tx(symbol, limit):
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={symbol},day,,,{limit},qfq")
    data = _get_json(url)
    node = (data.get("data") or {}).get(symbol) or {}
    rows = node.get("qfqday") or node.get("day") or []
    return [{"d": r[0], "o": float(r[1]), "c": float(r[2]),
             "h": float(r[3]), "l": float(r[4]), "v": float(r[5]) * 100}
            for r in rows]


def fetch_bars(key="sh", limit=340):
    """取指数日线，自动在多源间回退，返回按日期升序的 bars。"""
    secid, symbol = INDEXES[key]
    errors = []
    for name, fn, arg in (("eastmoney", _bars_em, secid),
                          ("sina", _bars_sina, symbol),
                          ("tencent", _bars_tx, symbol)):
        try:
            bars = fn(arg, limit)
            bars = [b for b in bars if b.get("c")]
            if len(bars) >= 60:
                bars.sort(key=lambda b: b["d"])
                return bars
            errors.append(f"{name}: 数据过短({len(bars)})")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}")
    raise DataError("指数日线获取失败 -> " + "; ".join(errors))


_CN_HEADERS = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/", "Accept": "*/*"}


def _get_raw(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return r.read()


def _live_em(key):
    secid = INDEXES[key][0]
    url = ("https://push2.eastmoney.com/api/qt/stock/get"
           f"?secid={secid}&ut=fa5fd1943c7b386f172d6893dbfba10b"
           "&fields=f43,f44,f45,f46,f47,f86")
    data = ((_get_json(url, retries=0) or {}).get("data")) or {}
    if not data.get("f43") or not data.get("f86"):
        return None
    day = time.strftime("%Y-%m-%d", time.localtime(int(data["f86"])))
    return {"d": day,
            "o": float(data["f46"]) / 100,
            "h": float(data["f44"]) / 100,
            "l": float(data["f45"]) / 100,
            "c": float(data["f43"]) / 100,
            "v": float(data["f47"]) * 100}


def _live_tx(key):
    symbol = INDEXES[key][1]
    raw = _get_raw(f"https://qt.gtimg.cn/q={symbol}", _CN_HEADERS).decode("gbk", "ignore")
    body = raw.split('"')[1] if '"' in raw else ""
    p = body.split("~")
    if len(p) < 35 or not p[3]:
        return None
    stamp = p[30]
    day = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"
    return {"d": day, "o": float(p[5]), "h": float(p[33]), "l": float(p[34]),
            "c": float(p[3]), "v": float(p[6]) * 100}


def _live_sina(key):
    symbol = INDEXES[key][1]
    raw = _get_raw(f"https://hq.sinajs.cn/list={symbol}", _CN_HEADERS).decode("gbk", "ignore")
    body = raw.split('"')[1] if '"' in raw else ""
    p = body.split(",")
    if len(p) < 32 or not p[3]:
        return None
    return {"d": p[30], "o": float(p[1]), "h": float(p[4]), "l": float(p[5]),
            "c": float(p[3]), "v": float(p[8])}


def fetch_live_bar(key="sh"):
    """当日实时快照（盘中补当日 K 线用）。收盘后返回的即当日收盘数据。

    三个行情源依次尝试，任一成功即返回；全部失败返回 None。
    """
    for fn in (_live_em, _live_tx, _live_sina):
        try:
            bar = fn(key)
            if bar and bar.get("c"):
                return bar
        except Exception:  # noqa: BLE001
            continue
    return None


def fetch_index_changes():
    """四指数最新涨跌幅（%），用于温度与展示。"""
    codes = {"sh": (1, "000001"), "szc": (0, "399001"),
             "cyb": (0, "399006"), "kc50": (1, "000688")}
    try:
        url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2"
               "&fields=f2,f3,f12,f13,f14"
               "&secids=1.000001,0.399001,0.399006,1.000688")
        diff = ((_get_json(url) or {}).get("data") or {}).get("diff") or []
        found = {}
        for row in diff:
            for key, (mkt, code) in codes.items():
                if row.get("f13") == mkt and str(row.get("f12")) == code:
                    pct = row.get("f3")
                    if isinstance(pct, (int, float)):
                        found[key] = float(pct)
        if len(found) == 4:
            return found
    except Exception:  # noqa: BLE001
        pass

    out = {}
    for key, (secid, symbol) in INDEXES.items():
        out[key] = None
        for fn, arg in ((_bars_em, secid), (_bars_sina, symbol), (_bars_tx, symbol)):
            try:
                bars = fn(arg, 6)
                if len(bars) >= 2 and bars[-1]["c"] and bars[-2]["c"]:
                    out[key] = (bars[-1]["c"] / bars[-2]["c"] - 1) * 100
                    break
            except Exception:  # noqa: BLE001
                continue
    return out


# ------------------------------------------------------------------ 广度

_CLIST = ("https://push2.eastmoney.com/api/qt/clist/get?po=1&np=1&fltt=2&invt=2&fid=f3"
          "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f2,f3,f12,f14,f18")
_PAGE = 100


def _limit_pct(code: str, name: str):
    if name.upper().startswith(("N", "C")):
        return None                       # 新股首日无涨跌幅限制，不计入涨跌停
    if code[:3] in ("300", "301", "688", "689"):
        return 20.0
    if code[:1] in ("4", "8"):
        return 30.0
    return 5.0 if "ST" in name.upper() else 10.0


def _page(pn):
    data = _get_json(f"{_CLIST}&pn={pn}&pz={_PAGE}")
    node = (data or {}).get("data") or {}
    return node.get("total") or 0, node.get("diff") or []


def _breadth_snapshot():
    """全市场逐票快照（分页汇总），口径最细但请求较多。"""
    total, first = _page(1)
    if not first:
        return None
    pages = (total + _PAGE - 1) // _PAGE
    rows = list(first)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for _, diff in pool.map(_page, range(2, min(pages, 60) + 1)):
                rows.extend(diff)

    up = down = flat = zt = dt = 0
    for r in rows:
        pct = r.get("f3")
        if not isinstance(pct, (int, float)):
            continue
        if pct > 0:
            up += 1
        elif pct < 0:
            down += 1
        else:
            flat += 1
        lim = _limit_pct(str(r.get("f12", "")), str(r.get("f14", "")))
        if lim is None:
            continue
        if pct >= lim - 0.15:
            zt += 1
        elif pct <= -lim + 0.15:
            dt += 1
    return {"up": up, "down": down, "flat": flat, "zt": zt, "dt": dt, "total": len(rows)}


def _breadth_distribution():
    """涨跌分布聚合，单请求即可得到上涨/下跌/平盘家数。"""
    url = ("https://push2ex.eastmoney.com/getTopicZDFenBu"
           "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt")
    data = _get_json(url)
    buckets = ((data or {}).get("data") or {}).get("fenbu") or []
    up = down = flat = 0
    for bucket in buckets:
        for k, v in bucket.items():
            try:
                key = int(k)
                cnt = int(v)
            except (TypeError, ValueError):
                continue
            if key > 0:
                up += cnt
            elif key < 0:
                down += cnt
            else:
                flat += cnt
    if up + down == 0:
        return None
    return {"up": up, "down": down, "flat": flat}


def fetch_pool_counts(date_str):
    """涨跌停池计数。date_str: YYYYMMDD"""
    out = {}
    for key, api, sort in (("zt", "getTopicZTPool", "fbt%3Aasc"),
                           ("dt", "getTopicDTPool", "fund%3Aasc")):
        out[key] = None
        try:
            url = (f"https://push2ex.eastmoney.com/{api}"
                   "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
                   f"&Pageindex=0&pagesize=1&sort={sort}&date={date_str}")
            data = _get_json(url)
            out[key] = ((data or {}).get("data") or {}).get("tc")
        except Exception:  # noqa: BLE001
            pass
    return out


def fetch_breadth(date_str=None):
    """涨跌家数与涨跌停家数，多源组合取数。

    优先「涨跌分布 + 涨跌停池」（2-3 个请求，稳定）；
    失败时回退到全市场逐票快照。
    """
    breadth = None
    try:
        breadth = _breadth_distribution()
    except Exception:  # noqa: BLE001
        pass

    pools = fetch_pool_counts(date_str) if date_str else {"zt": None, "dt": None}
    if breadth and pools.get("zt") is not None and pools.get("dt") is not None:
        return {**breadth, "zt": pools["zt"], "dt": pools["dt"], "source": "dist+pool"}

    snap = None
    try:
        snap = _breadth_snapshot()
    except Exception:  # noqa: BLE001
        pass
    if snap:
        snap["source"] = "snapshot"
        return snap

    if breadth and pools.get("zt") is not None:
        return {**breadth, "dt": 0, "zt": pools["zt"], "source": "dist+pool(partial)"}
    raise DataError("涨跌家数与涨跌停数据获取失败")


# ------------------------------------------------------------------ 离线

def load_offline(directory):
    """离线模式：读取 index.csv 与 breadth.csv。"""
    idx_path = os.path.join(directory, "index.csv")
    brd_path = os.path.join(directory, "breadth.csv")
    if not os.path.exists(idx_path):
        raise DataError(f"缺少 {idx_path}")
    bars = []
    with open(idx_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            bars.append({"d": r["date"], "o": float(r["open"]), "c": float(r["close"]),
                         "h": float(r["high"]), "l": float(r["low"]),
                         "v": float(r.get("volume") or 0)})
    bars.sort(key=lambda b: b["d"])

    breadth = {}
    if os.path.exists(brd_path):
        with open(brd_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                breadth[r["date"]] = {
                    "up": int(r["up"]), "down": int(r["down"]),
                    "flat": int(r.get("flat") or 0),
                    "zt": int(r["zt"]), "dt": int(r["dt"]),
                    "idx_avg": float(r.get("idx_avg") or 0.0),
                }
    return bars, breadth
