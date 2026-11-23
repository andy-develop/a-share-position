#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""决策卡图像渲染（a-share-position）。

纯 Pillow 排版，输出单张 PNG。所有坐标以 786px 基准宽度定义，
内部按 scale 倍绘制，默认 2 倍 => 1572px 宽，便于分享与查看。

本文件只负责版式，不做任何数据计算。
"""

from __future__ import annotations

import os

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None

# ------------------------------------------------------------------ 设计变量

BASE_W = 786
MARGIN = 40
CONTENT_W = BASE_W - MARGIN * 2

BG = (239, 237, 231)
INK = (20, 22, 26)
INK_SOFT = (58, 60, 66)
MUTED = (138, 140, 146)
MUTED_LIGHT = (169, 167, 160)
MUTED_BODY = (92, 95, 102)
HAIR = (219, 216, 206)
RED = (198, 40, 40)
GREEN = (31, 122, 77)
ACCENT = (192, 58, 32)
PILL_BG = (226, 223, 213)

BAR_COLORS = [
    (31, 78, 121), (46, 111, 167), (94, 156, 196), (168, 166, 157),
    (217, 166, 46), (224, 123, 41), (192, 58, 32),
]
BAR_WEIGHTS = [10, 10, 20, 20, 20, 10, 10]          # 与档位边界一致
AXIS_LABELS = "极冰 · 冰点 · 低温 · 常温 · 偏热 · 高温 · 极热"

# ------------------------------------------------------------------ 字体

_CJK_SOURCES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]

_NUM_SOURCES = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]

_STYLE_RANK = {
    "r": ("regular", "w3", "light", "text", "normal", "medium"),
    "b": ("bold", "w6", "semibold", "semi bold", "medium", "heavy", "black"),
    "h": ("black", "heavy", "bold", "condensed black", "semibold"),
}

_scan_cache = {}
_font_cache = {}


def _scan(path):
    """列出 ttc 内的所有字面 (index, family, style)。"""
    if path in _scan_cache:
        return _scan_cache[path]
    faces = []
    if os.path.exists(path):
        for i in range(16):
            try:
                f = ImageFont.truetype(path, 12, index=i)
            except Exception:  # noqa: BLE001
                break
            try:
                family, style = f.getname()
            except Exception:  # noqa: BLE001
                family, style = "", ""
            faces.append((i, family or "", style or ""))
    _scan_cache[path] = faces
    return faces


def _resolve(weight):
    """按权重挑选 (path, index)，优先中文字体。"""
    hints = _STYLE_RANK.get(weight, _STYLE_RANK["r"])
    sources = _CJK_SOURCES if weight != "h" else _NUM_SOURCES + _CJK_SOURCES
    for path in sources:
        faces = _scan(path)
        best = None
        for idx, _fam, style in faces:
            low = style.lower()
            if ".interface" in _fam.lower():
                continue
            for rank, hint in enumerate(hints):
                if hint in low:
                    score = len(hints) - rank
                    if best is None or score > best[0]:
                        best = (score, path, idx)
                    break
        if best:
            return best[1], best[2]
    return None, 0


def _font(size, weight="r", scale=2):
    key = (weight, int(round(size * scale)))
    if key in _font_cache:
        return _font_cache[key]
    path, idx = _resolve(weight)
    if path is None:                                    # 兜底：Pillow 内置
        f = ImageFont.load_default()
    else:
        try:
            f = ImageFont.truetype(path, key[1], index=idx)
        except Exception:  # noqa: BLE001
            f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# ------------------------------------------------------------------ 画布

class Card:
    def __init__(self, scale=2, height=2400):
        self.s = scale
        self.img = Image.new("RGB", (BASE_W * scale, height * scale), BG)
        self.d = ImageDraw.Draw(self.img)
        self.bottom = 0

    # -- 基础 ---------------------------------------------------------
    def _xy(self, x, y):
        return (x * self.s, y * self.s)

    def text(self, x, y, s, size=22, weight="r", color=INK, anchor="ls", tracking=0.0):
        f = _font(size, weight, self.s)
        if not s:
            return 0.0
        if not tracking:
            self.d.text(self._xy(x, y), s, font=f, fill=color, anchor=anchor)
            self._touch(y, size)
            return self.measure(s, size, weight, 0, f)
        width = self.measure(s, size, weight, tracking, f)
        cur = x
        if anchor[0] == "r":
            cur = x - width
        elif anchor[0] == "m":
            cur = x - width / 2
        for ch in s:
            self.d.text(self._xy(cur, y), ch, font=f, fill=color, anchor="ls")
            cur += f.getlength(ch) / self.s + tracking
        self._touch(y, size)
        return width

    def _touch(self, y, size):
        if y + size * 0.4 > self.bottom:
            self.bottom = y + size * 0.4

    def measure(self, s, size, weight="r", tracking=0.0, f=None):
        f = f or _font(size, weight, self.s)
        w = sum(f.getlength(ch) for ch in s) / self.s
        if tracking and len(s) > 1:
            w += tracking * (len(s) - 1)
        return w

    def rect(self, x, y, w, h, color):
        self.d.rectangle([x * self.s, y * self.s, (x + w) * self.s, (y + h) * self.s],
                         fill=color)
        if y + h > self.bottom:
            self.bottom = y + h

    def rrect(self, x, y, w, h, r, fill=None, outline=None, width=1):
        self.d.rounded_rectangle(
            [x * self.s, y * self.s, (x + w) * self.s, (y + h) * self.s],
            radius=r * self.s, fill=fill, outline=outline,
            width=max(1, int(width * self.s)))
        if y + h > self.bottom:
            self.bottom = y + h

    def triangle_down(self, cx, top, bottom, half):
        self.d.polygon([self._xy(cx, bottom), self._xy(cx - half, top),
                        self._xy(cx + half, top)], fill=INK)

    # -- 章节标题 -----------------------------------------------------
    def section(self, y, no, title, size=27):
        w = self.text(MARGIN, y, no, size, "b", ACCENT, tracking=1.2)
        self.text(MARGIN + w + 20, y, title, size, "b", INK)
        return y

    # -- 大号数字（横向略宽，模拟标题字的宽度）-------------------------
    def hero_number(self, x, baseline, s, cap=88, stretch=1.18, color=INK):
        f = _font(100, "h", self.s)
        size = 100
        for _ in range(3):
            bbox = f.getbbox(s)
            h = (bbox[3] - bbox[1]) / self.s
            if h <= 0:
                break
            size = size * (cap / h)
            f = _font(size, "h", self.s)
        bbox = f.getbbox(s)
        pad = 6
        layer = Image.new("RGBA", (int(bbox[2] - bbox[0]) + pad * 2,
                                   int(bbox[3] - bbox[1]) + pad * 2), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.text((pad - bbox[0], pad - bbox[1]), s, font=f, fill=color + (255,),
                stroke_width=int(1.2 * self.s), stroke_fill=color + (255,))
        layer = layer.crop(layer.getbbox())
        w = layer.width / self.s
        h = layer.height / self.s
        if stretch != 1:
            layer = layer.resize((max(1, int(round(layer.width * stretch))),
                                  layer.height), Image.LANCZOS)
            w *= stretch
        self.img.paste(layer, (int(x * self.s), int((baseline - h) * self.s)), layer)
        if baseline > self.bottom:
            self.bottom = baseline
        return w

    # -- 段落 ---------------------------------------------------------
    def _tokens(self, s):
        out, buf = [], ""
        for ch in s:
            if ch.isascii() and (ch.isalnum() or ch in ".,%-+/()"):
                buf += ch
            else:
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(ch)
        if buf:
            out.append(buf)
        return out

    NO_BREAK_BEFORE = set("。，、；：？！）】》」』’”%…")
    NO_BREAK_AFTER = set("（【《「『‘“")

    def wrap(self, s, size, maxw, weight="r"):
        f = _font(size, weight, self.s)
        toks = self._tokens(s)
        lines, cur = [], ""
        for i, tok in enumerate(toks):
            prev = toks[i - 1] if i else ""
            glued = tok and (tok[0] in self.NO_BREAK_BEFORE
                             or (prev and prev[-1] in self.NO_BREAK_AFTER))
            if glued and cur:
                cur += tok                       # 避头尾：标点不换行首
                continue
            trial = cur + tok
            if self.measure(trial, size, weight, 0, f) <= maxw or not cur:
                if not cur and self.measure(tok, size, weight, 0, f) > maxw:
                    for ch in tok:                       # 超长 token 硬切
                        if self.measure(cur + ch, size, weight, 0, f) > maxw and cur:
                            lines.append(cur)
                            cur = ch
                        else:
                            cur += ch
                else:
                    cur = trial
            else:
                lines.append(cur.rstrip())
                cur = tok if not tok.isspace() else ""
        if cur.strip():
            lines.append(cur.rstrip())
        return lines

    def paragraph(self, x, y, s, size, lh, maxw, color, justify=True, weight="r"):
        f = _font(size, weight, self.s)
        lines = self.wrap(s, size, maxw, weight)
        for i, ln in enumerate(lines):
            last = i == len(lines) - 1
            if justify and not last and len(ln) > 1:
                nat = self.measure(ln, size, weight, 0, f)
                slots = sum(1 for i in range(len(ln) - 1)
                            if not ln[i].isspace() and not ln[i + 1].isspace())
                gap = (maxw - nat) / slots if slots else 0.0
                cx = x
                for i, ch in enumerate(ln):
                    self.d.text(self._xy(cx, y), ch, font=f, fill=color, anchor="ls")
                    add = gap if (i < len(ln) - 1 and not ch.isspace()
                                  and not ln[i + 1].isspace()) else 0.0
                    cx += f.getlength(ch) / self.s + add
            else:
                self.d.text(self._xy(x, y), ln, font=f, fill=color, anchor="ls")
            self._touch(y, size)
            y += lh
        return y

    # -- 组块 ---------------------------------------------------------
    def status_bar(self, y, clock="9:41"):
        self.text(MARGIN, y, clock, 31, "b", INK)
        right = BASE_W - MARGIN
        # 电池
        bw, bh = 46, 22
        bx, by = right - bw, y - 20
        self.rrect(bx, by, bw, bh, 5, outline=INK, width=1.6)
        self.rrect(bx + 2.5, by + 2.5, bw - 5, bh - 5, 3, fill=INK)
        self.rrect(bx + bw + 1, by + 8, 3, 6, 1.5, fill=INK)
        # wifi
        wx = bx - 22
        for r in (4, 8.5, 13):
            self.d.arc([(wx - r) * self.s, (y - r) * self.s,
                        (wx + r) * self.s, (y + r) * self.s],
                       205, 335, fill=INK, width=int(1.8 * self.s))
        self.d.ellipse([(wx - 1.6) * self.s, (y - 1.6) * self.s,
                        (wx + 1.6) * self.s, (y + 1.6) * self.s], fill=INK)
        # 信号
        sx = wx - 34
        for i, hgt in enumerate((7, 11, 15, 19)):
            self.rect(sx + i * 7, y - hgt + 9, 5, hgt, INK)

    def temp_bar(self, y, temp):
        x = MARGIN
        total = CONTENT_W
        unit = total / sum(BAR_WEIGHTS)
        edges = [x]
        for i, wgt in enumerate(BAR_WEIGHTS):
            seg = unit * wgt
            self.rect(round(x), y, round(seg) - 1, 40, BAR_COLORS[i])
            x += seg
            edges.append(round(x))
        # 刻度
        for e in edges[1:-1]:
            self.rect(e - 1, y + 42, 2, 9, MUTED_LIGHT)
        pos = MARGIN + total * max(0.0, min(100.0, temp)) / 100.0
        self.triangle_down(pos, y - 20, y - 4, 12.5)
        return edges


# ------------------------------------------------------------------ 组装

def _fmt_money(v):
    return f"{v:.2f}"


def render(data, out_path, scale=2):
    """data 为 decide.py 的结果字典；返回 (path, width, height)。"""
    if Image is None:
        raise RuntimeError("缺少 Pillow，无法生成图片")

    c = Card(scale=scale)
    date_dot = data["date"].replace("-", ".")

    c.status_bar(73)

    # ---- 页头
    c.text(MARGIN, 160, "择时 · 仓位建议", 36, "b", INK)
    c.text(BASE_W - MARGIN, 157, date_dot, 24, "r", INK_SOFT, anchor="rs", tracking=1.8)
    c.text(MARGIN, 197, data["as_of_line"], 22, "r", MUTED)
    c.rect(MARGIN, 206, CONTENT_W, 4, INK)

    # ---- 01 温度
    c.section(256, "01", "A股市场情绪温度计")
    if data.get("prev_temp") is not None:
        delta = data["temp"] - data["prev_temp"]
        txt = "较前日" if data.get("prev_is_prev") else "较 " + data["prev_date"][5:]
        val_w = c.text(BASE_W - MARGIN, 256, f"{delta:+.1f}", 26, "b", RED, anchor="rs")
        c.text(BASE_W - MARGIN - val_w - 8, 256, txt, 21, "r", RED, anchor="rs")

    hero_w = c.hero_number(MARGIN, 371, f"{data['temp']:.1f}", cap=88, stretch=1.12)
    c.text(BASE_W - MARGIN, 349, data["band"], 66, "b", INK, anchor="rs")
    if data.get("prev_temp") is not None:
        c.text(BASE_W - MARGIN, 372,
               f"{data['prev_date'][5:].replace('-', '-')} {data['prev_band']} {data['prev_temp']:.1f}",
               20, "r", MUTED, anchor="rs")

    c.temp_bar(414, data["temp"])
    c.text(MARGIN, 499, AXIS_LABELS, 20, "r", MUTED)
    c.text(BASE_W - MARGIN, 499, "刻度 0 – 100", 20, "r", MUTED, anchor="rs")

    # ---- 02 行情快照
    c.section(562, "02", "行情快照")
    pct_color = RED if data["index_chg"] > 0 else (GREEN if data["index_chg"] < 0 else INK)
    tail = f"({data['index_chg']:+.2f}%)"
    tail_w = c.text(BASE_W - MARGIN, 565, tail, 22, "r", pct_color, anchor="rs")
    pts = f"{data['chg_pts']:+.2f}"
    pts_w = c.text(BASE_W - MARGIN - tail_w - 6, 565, pts, 24, "b", pct_color, anchor="rs")
    c.text(BASE_W - MARGIN - tail_w - pts_w - 14, 565, _fmt_money(data["index_close"]),
           30, "b", INK, anchor="rs")

    col = [MARGIN, 320, 592]
    labels = ("上涨家数", "下跌家数", "涨停 / 跌停")
    c.text(col[0], 601, labels[0], 21, "r", MUTED)
    c.text(col[1], 601, labels[1], 21, "r", MUTED)
    c.text(col[2], 601, labels[2], 21, "r", MUTED)
    c.text(col[0], 641, str(data["up"]), 30, "b", RED)
    c.text(col[1], 641, str(data["down"]), 30, "b", GREEN)
    c.text(col[2], 641, f"{data['zt']} / {data['dt']}", 30, "b", INK)

    c.rect(MARGIN, 654, CONTENT_W, 2, HAIR)

    c.text(col[0], 687, "成交量", 21, "r", MUTED)
    c.text(col[1], 687, "市场广度", 21, "r", MUTED)
    c.text(col[2], 687, "四指数均值", 21, "r", MUTED)
    vol_txt = f"{data['volume_yi']:.2f}"
    vw = c.text(col[0], 728, vol_txt, 30, "b", INK)
    c.text(col[0] + vw + 6, 728, "亿股", 21, "r", INK_SOFT)
    c.text(col[1], 728, f"{data['breadth_pct']:.1f}%", 30, "b", INK)
    idx_color = RED if data["idx_avg"] > 0 else (GREEN if data["idx_avg"] < 0 else INK)
    c.text(col[2], 728, f"{data['idx_avg']:+.2f}%", 30, "b", idx_color)

    # ---- 03 仓位建议
    c.section(798, "03", "今日仓位建议")
    badge_w = c.measure(data["badge"], 22, "b") + 34
    c.rrect(BASE_W - MARGIN - badge_w, 766, badge_w, 41, 6, fill=INK)
    c.text(BASE_W - MARGIN - badge_w / 2, 794, data["badge"], 22, "b", BG, anchor="ms")

    c.rect(MARGIN, 822, 8, 68, INK)
    c.text(MARGIN + 30, 891, data["title"], 74, "b", INK)
    c.text(MARGIN, 931, data["desc"], 24, "r", MUTED_BODY)

    cells = data.get("grid") or []
    for i, cell in enumerate(cells[:4]):
        x = MARGIN if i % 2 == 0 else 452
        y = 979 if i < 2 else 1059
        lab, val = cell[0], cell[1]
        tone = cell[2] if len(cell) > 2 else None
        color = {"pos": RED, "neg": GREEN}.get(tone, INK)
        c.text(x, y, lab, 21, "r", MUTED)
        c.text(x, y + 40, val, 28, "b", color)

    # ---- 04 信号台账
    c.section(1174, "04", "信号台账")
    c.text(BASE_W - MARGIN, 1170, "建议文案仅在触发当日显示", 20, "r", MUTED_LIGHT,
           anchor="rs")

    rows = [(1180, "B1", "尝试性建仓", data["b1_state"], data["b1_desc"], None),
            (1275, "B0", "博弈性建仓", data["b0_state"], data["b0_desc"], None),
            (1367, "退出", None, data["exit_state"], None,
             ("逃顶", "清仓", "止损"))]
    for top, code, name, state, desc, pills in rows:
        base = top + 39
        c.text(MARGIN, base, code, 26, "b", INK)
        c.text(MARGIN, base + 26, state, 20, "r", MUTED)
        if name:
            c.text(153, base, name, 26, "b", INK)
            if desc:
                c.text(153, base + 26, desc, 20, "r", MUTED)
        if pills:
            px = 153
            active = data.get("exit_pill")
            py = base - 26
            for p in pills:
                on = p == active
                pw = c.measure(p, 21, "b" if on else "r") + 34
                c.rrect(px, py, pw, 34, 4, fill=ACCENT if on else PILL_BG)
                c.text(px + pw / 2, py + 23, p, 21, "b" if on else "r",
                       BG if on else MUTED_BODY, anchor="ms")
                px += pw + 14

    # ---- 05 点评
    c.section(1515, "05", "今日市场点评")
    c.text(BASE_W - MARGIN, 1511, data["date"], 20, "r", MUTED, anchor="rs")
    c.paragraph(MARGIN, 1554, data["commentary"], 23, 38, CONTENT_W, INK_SOFT)
    y = c.bottom + 26
    c.paragraph(MARGIN, y, "数据来源：指数择时策略（上证指数）· 情绪温度计 · 仅供参考，"
                           "不构成投资建议", 18, 28, CONTENT_W, MUTED_LIGHT, justify=False)

    height = int(round((y + 30) * scale))
    out = c.img.crop((0, 0, BASE_W * scale, min(height, c.img.height)))
    ensure = os.path.dirname(os.path.abspath(out_path))
    if ensure:
        os.makedirs(ensure, exist_ok=True)
    out.save(out_path, format="PNG", optimize=True)
    return out_path, out.width, out.height


def available():
    return Image is not None
