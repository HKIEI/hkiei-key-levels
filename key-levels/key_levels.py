#!/usr/bin/env python3
"""Yahoo daily key-level observations. Python 3.9+, standard library only.

Observation tool only: it lists price levels the market has reacted to in the
past. No trade signals, no predictions. See SKILL.md.
"""
import argparse
import datetime as dt
import html
import json
import math
import re
import base64
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VERSION = "2.2.1"
DISCLAIMER = "僅供教育參考，並非投資建議。數據可能有延遲。"
AI_NOTICE = ("本圖像及當中重要價位由 A.I. 根據 Yahoo Finance 公開數據自動計算及生成，"
             "未經人工逐一核實，或有錯誤或延遲。")
NOTES = {
    "N1": "支持位／阻力位僅屬價格位置，並不代表方向或入市時機。",
    "N2": "多次測試的位置仍可被突破或出現假突破，不代表必定守穩。",
    "N3": "心理關口、前期高低位主要作觀察用途，不應單憑這些位置入市。",
    "N4": "多種方法重疊的位置值得多加留意，但並不代表成功率。",
    "N5": "移動平均線須有明顯斜率，且價格確實出現反應，才具參考價值。",
}


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return float(value)


def normalize_symbol(value):
    symbol = value.strip().upper()
    aliases = {
        "^HSI":"^HSI", "HSI":"^HSI", "恒指":"^HSI", "恆指":"^HSI", "恒生指數":"^HSI", "恆生指數":"^HSI", "恒指現貨":"^HSI",
        "DJI":"^DJI", "DJIA":"^DJI", "DOW":"^DJI", "道指":"^DJI", "道瓊":"^DJI", "道瓊斯":"^DJI",
        "道瓊斯工業平均指數":"^DJI", "道瓊工業平均指數":"^DJI", "道指現貨":"^DJI",
        "GSPC":"^GSPC", "SPX":"^GSPC", "SP500":"^GSPC", "S&P500":"^GSPC", "標普":"^GSPC",
        "標普500":"^GSPC", "標普500指數":"^GSPC", "標準普爾500指數":"^GSPC",
        "IXIC":"^IXIC", "NASDAQ":"^IXIC", "NASDAQCOMPOSITE":"^IXIC", "納指":"^IXIC",
        "納指綜合":"^IXIC", "納斯達克綜合指數":"^IXIC", "納斯達克綜合":"^IXIC", "納斯達克指數":"^IXIC",
    }
    symbol = aliases.get("".join(symbol.split()), symbol)
    if not re.fullmatch(r"\^?[A-Z0-9][A-Z0-9.=-]{0,24}", symbol):
        raise ValueError("無效代號。例：0700.HK、^HSI、TSLA、^IXIC。")
    if re.fullmatch(r"\d{1,5}\.HK", symbol):
        symbol = str(int(symbol[:-3])).zfill(4) + ".HK"
    return symbol


def fetch_chart(symbol, range_name="1y", timeout=20, *, period=None):
    params = {"range": range_name, "interval": "1d"} if period is None else {
        "period1": period[0], "period2": period[1], "interval": "5m", "includePrePost": "false"}
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol, safe="")
           + "?" + urllib.parse.urlencode(params))
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; KeyLevels/1.0; Python urllib)",
        "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise ValueError("Yahoo 回應資料過大。")
            return json.loads(raw.decode("utf-8")), url
        except urllib.error.HTTPError as error:
            if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(attempt + 1)
                continue
            raise ValueError("Yahoo HTTP %s，未能取得行情，請稍後再試。" % error.code)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt < 2:
                time.sleep(attempt + 1)
                continue
            raise ValueError("Yahoo 連線失敗：%s" % error)
    raise ValueError("Yahoo 連線失敗。")


def market_timezone(meta, warnings):
    try:
        return ZoneInfo(meta.get("exchangeTimezoneName", "UTC"))
    except ZoneInfoNotFoundError:
        warnings.append("本機缺少市場時區資料，改用 Yahoo 提供的固定時差，夏令時間或有偏差。")
        offset = number(meta.get("gmtoffset")) or 0
        return dt.timezone(dt.timedelta(seconds=offset))


def parse_chart(payload, now=None):
    now = time.time() if now is None else now
    warnings = []
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError("Yahoo API 錯誤：%s" % chart["error"].get("description", chart["error"]))
    results = chart.get("result")
    if not results:
        raise ValueError("Yahoo 沒有回傳行情。")
    result = results[0]
    meta = result.get("meta", {})
    tz = market_timezone(meta, warnings)
    quotes = result.get("indicators", {}).get("quote", [])
    if not quotes:
        raise ValueError("行情缺少開市、最高、最低及收市價資料。")
    quote = quotes[0]
    bars_by_date = {}
    bad = 0
    for i, stamp in enumerate(result.get("timestamp", [])):
        if number(stamp, True) is None or stamp > now + 300:
            bad += 1
            continue
        values = {}
        for name in ("open", "high", "low", "close", "volume"):
            values[name] = number(quote.get(name, [])[i]) if i < len(quote.get(name, [])) else None
        prices = [values[k] for k in ("open", "high", "low", "close")]
        if (any(p is None or p <= 0 for p in prices)
                or values["low"] > min(values["open"], values["close"])
                or values["high"] < max(values["open"], values["close"])):
            bad += 1
            continue
        if values["volume"] is not None and values["volume"] <= 0:
            values["volume"] = None
        date = dt.datetime.fromtimestamp(stamp, tz).date().isoformat()
        values.update({"timestamp": stamp, "date": date, "complete": True})
        if date in bars_by_date:
            warnings.append("同一交易日有重複紀錄，採用最後一筆：" + date)
        bars_by_date[date] = values
    bars = sorted(bars_by_date.values(), key=lambda b: b["timestamp"])
    if len(bars) < 3:
        raise ValueError("有效交易日少於 3 個，無法分析。")
    if bad:
        warnings.append("已略過 %d 筆無效或未來日期的價格紀錄，移動平均線以其餘紀錄計算。" % bad)
    today = dt.datetime.fromtimestamp(now, tz).date().isoformat()
    regular = meta.get("currentTradingPeriod", {}).get("regular", {})
    end = number(regular.get("end"), True)
    start = number(regular.get("start"), True)
    for bar in bars:
        if bar["date"] == today:
            matching = start and dt.datetime.fromtimestamp(start, tz).date().isoformat() == today
            # Allow the provider 15 minutes after session end to finalize the bar.
            bar["complete"] = bool(matching and end and now >= end + 900)
            if not bar["complete"]:
                quote_time = number(meta.get("regularMarketTime"), True)
                dated_quote = (quote_time and quote_time <= now and
                               dt.datetime.fromtimestamp(quote_time, tz).date().isoformat() == today)
                bar["opening_available"] = bool(bar["timestamp"] <= now and
                                                (now >= start if matching else dated_quote))
    if any(not b["complete"] for b in bars):
        warnings.append("今日尚未收市，股價圖以淡色顯示當日走勢，計算時不計入。")
    completed = [b for b in bars if b["complete"]]
    if len(completed) < 2:
        raise ValueError("已收市交易日少於 2 個。")
    price = number(meta.get("regularMarketPrice"), True)
    stamp = number(meta.get("regularMarketTime"), True)
    kind = "regularMarketPrice（可能延遲，非盤前／盤後價）"
    latest = bars[-1]
    fallback = False
    # Small clock differences between this computer and Yahoo are common; 3 hours is clearly bad data.
    if (price is None or stamp is None or stamp > now + 3 * 3600
            or dt.datetime.fromtimestamp(stamp, tz).date().isoformat() < latest["date"]):
        price, stamp = latest["close"], latest["timestamp"]
        kind = "最新交易日收市價或暫時收市價（非即時價）"
        fallback = True
        warnings.append("Yahoo 報價資料不足或過舊，改用最新交易日收市價。")
    elif stamp > now + 300:
        warnings.append("Yahoo 報價時間較本機時鐘遲約 %d 分鐘，請檢查電腦時間設定。" % round((stamp - now) / 60))
    age_hours = max(0, now - stamp) / 3600
    if age_hours > 96:
        warnings.append("報價已超過 96 小時，可能因假期、停牌或資料過舊，不宜視為即時行情。")
    ref_date = dt.datetime.fromtimestamp(stamp, tz).date().isoformat()
    previous = [b for b in completed if b["date"] < ref_date]
    previous = previous[-1] if previous else None
    if fallback:
        # The bar timestamp is the session open, not the time of the price: show the date only.
        display = ref_date + ("（盤中，未收市）" if not latest["complete"] else "（收市）")
    else:
        display = dt.datetime.fromtimestamp(stamp, tz).strftime("%Y-%m-%d %H:%M")
    return {"meta": meta, "bars": bars, "completed": completed,
            "previous": previous, "warnings": warnings,
            "price": {"value": price, "timestamp": stamp, "date": ref_date,
                      "time": dt.datetime.fromtimestamp(stamp, tz).isoformat(),
                      "time_display": display,
                      "kind": kind, "age_hours": age_hours},
            "timezone": str(tz), "as_of_utc": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()}


def moving_average(values, period, kind="ema"):
    output = [None] * len(values)
    if len(values) < period:
        return output
    mean = sum(values[:period]) / period
    output[period - 1] = mean
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        if kind == "ema":
            mean += alpha * (values[i] - mean)
        else:
            mean += (values[i] - values[i - period]) / period
        output[i] = mean
    return output


def swing_points(bars, window):
    result = []
    for i in range(window, len(bars) - window):
        b = bars[i]
        left, right = bars[i-window:i], bars[i+1:i+window+1]
        # Strictly beat the left side, allow equality on right: one plateau pivot.
        if all(b["high"] > x["high"] for x in left) and all(b["high"] >= x["high"] for x in right):
            result.append((i, "swing_high", b["high"]))
        if all(b["low"] < x["low"] for x in left) and all(b["low"] <= x["low"] for x in right):
            result.append((i, "swing_low", b["low"]))
    return result


def profile(bars, bins):
    valid = [b for b in bars if b["volume"] is not None]
    if not valid:
        return []
    lo, hi = min(b["low"] for b in bars), max(b["high"] for b in bars)
    if lo == hi:
        return [{"low": lo, "high": hi, "price": lo,
                 "volume": sum(b["volume"] for b in valid)}]
    width = (hi - lo) / bins
    volumes = [0.0] * bins
    for b in valid:
        span = b["high"] - b["low"]
        if span == 0:
            volumes[min(bins - 1, int((b["low"] - lo) / width))] += b["volume"]
            continue
        for j in range(bins):
            overlap = max(0, min(b["high"], lo+(j+1)*width) - max(b["low"], lo+j*width))
            volumes[j] += b["volume"] * overlap / span
    return [{"low": lo+j*width, "high": lo+(j+1)*width,
             "price": lo+(j+0.5)*width, "volume": v} for j, v in enumerate(volumes)]


def psychological_step(price, symbol):
    if symbol == "^HSI":
        return 500.0
    # About 4% of price on a 1 / 2.5 / 5 / 10 grid: 109 -> 5, 600 -> 25, 3.5 -> 0.25.
    target = price * 0.04
    exponent = 10 ** math.floor(math.log10(target))
    return min((x * exponent for x in (1, 2.5, 5, 10)), key=lambda x: abs(x-target))


def average_true_range(bars, period=14):
    ranges = []
    for prev, b in zip(bars, bars[1:]):
        ranges.append(max(b["high"]-b["low"], abs(b["high"]-prev["close"]), abs(b["low"]-prev["close"])))
    recent = ranges[-period:] or [b["high"]-b["low"] for b in bars[-period:]]
    return sum(recent) / len(recent) if recent else 0.0


def open_gaps(bars, lookback, min_size):
    """Unfilled price gaps between consecutive daily bars, most recent first."""
    gaps = []
    start = max(1, len(bars) - lookback)
    for i in range(start, len(bars)):
        prev, b = bars[i-1], bars[i]
        later = bars[i+1:]
        if b["low"] > prev["high"]:
            top = min([b["low"]] + [x["low"] for x in later])
            low, high, direction = prev["high"], top, "up"
        elif b["high"] < prev["low"]:
            bottom = max([b["high"]] + [x["high"] for x in later])
            low, high, direction = bottom, prev["low"], "down"
        else:
            continue
        if high - low >= min_size:
            gaps.append({"low": low, "high": high, "direction": direction,
                         "date": b["date"], "complete": b.get("complete", True)})
    return list(reversed(gaps))[:3]


def cluster_candidates(candidates, tolerance):
    groups = []
    for candidate in sorted(candidates, key=lambda c: c["price"]):
        # Bounded full span: prevent chain-merging distant levels.
        if groups and candidate["price"] - groups[-1][0]["price"] <= tolerance:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    zones = []
    for group in groups:
        prices = sorted(c["price"] for c in group)
        center = statistics.median(prices)
        zones.append({"center": center, "low": max(1e-12, prices[0]-tolerance/2),
                      "high": prices[-1]+tolerance/2, "members": group,
                      "families": sorted(set(c["family"] for c in group))})
    for left, right in zip(zones, zones[1:]):
        if left["high"] > right["low"]:
            boundary = (max(m["price"] for m in left["members"]) +
                        min(m["price"] for m in right["members"])) / 2
            left["high"] = boundary
            right["low"] = boundary
    return zones


def overlap_volume(bar, low, high):
    if bar["volume"] is None:
        return 0.0
    span = bar["high"] - bar["low"]
    if span == 0:
        return bar["volume"] if low <= bar["close"] <= high else 0.0
    return bar["volume"] * max(0, min(high, bar["high"])-max(low, bar["low"])) / span


def bar_side(bar, zone):
    return "above" if bar["low"] > zone["high"] else "below" if bar["high"] < zone["low"] else None


def count_reactions(zone, bars, spans, threshold, follow=5):
    """An episode is a reaction when price leaves on the side it came from and moves away by threshold."""
    holds = rejects = crossings = 0
    for start, end in spans:
        if start == 0 or end >= len(bars) - 1:
            continue
        entry, leave = bar_side(bars[start-1], zone), bar_side(bars[end+1], zone)
        if entry is None or leave is None:
            continue
        if entry != leave:
            crossings += 1
            continue
        after = bars[end+1:end+1+follow]
        if entry == "above" and any(b["close"] - zone["high"] >= threshold for b in after):
            holds += 1
        elif entry == "below" and any(zone["low"] - b["close"] >= threshold for b in after):
            rejects += 1
    return holds, rejects, crossings


def score_zones(zones, bars, price, atr=0.0):
    for z in zones:
        episodes = 0
        touches = 0
        touched_before = False
        approaches = set()
        volume = 0.0
        last = None
        spans = []
        for i, b in enumerate(bars):
            touch = b["low"] <= z["high"] and b["high"] >= z["low"]
            if touch:
                touches += 1
                volume += overlap_volume(b, z["low"], z["high"])
                last = b["date"]
                if not touched_before:
                    episodes += 1
                    spans.append([i, i])
                    if i:
                        prev = bars[i-1]["close"]
                        if prev > z["high"]:
                            approaches.add("from_above")
                        elif prev < z["low"]:
                            approaches.add("from_below")
                else:
                    spans[-1][1] = i
            touched_before = touch
        holds, rejects, crossings = count_reactions(z, bars, spans, 0.5 * atr)
        z.update({"touch_days": touches, "test_episodes": episodes,
                  "reactions": holds + rejects, "holds_from_above": holds,
                  "rejections_from_below": rejects, "crossings": crossings,
                  "estimated_overlap_volume": volume, "last_touch": last,
                  "approach_sides": sorted(approaches),
                  "confluence_count": len(z["families"]),
                  "distance_pct": (z["center"] / price - 1) * 100})
        z["side"] = "support" if z["high"] < price else "resistance" if z["low"] > price else "at_price"
        nearest_edge = z["high"] if z["side"] == "support" else z["low"] if z["side"] == "resistance" else price
        z["edge_distance_pct"] = abs(nearest_edge / price - 1) * 100
        z["status"] = "觀察位置，並非買賣訊號"
    max_tests = max((z["reactions"] for z in zones), default=1) or 1
    max_vol = max((z["estimated_overlap_volume"] for z in zones), default=0)
    for z in zones:
        t = z["reactions"] / max_tests
        v = z["estimated_overlap_volume"] / max_vol if max_vol else None
        z["score_components"] = {"reactions_normalized": t, "volume_normalized": v}
        z["statistical_score"] = 100 * (0.5*t + 0.5*v) if v is not None else 100*t
    zones.sort(key=lambda z: (-z["statistical_score"], abs(z["distance_pct"])))
    for rank, z in enumerate(zones, 1):
        z["strength_rank"] = rank
        z["id"] = "Z%02d" % rank
    return zones


STRONG_FAMILIES = {"previous_session", "annual_extreme", "gap"}


def eligible(zone):
    families = set(zone["families"])
    if zone["reactions"] >= 1 or families & STRONG_FAMILIES:
        return True
    return len(families - {"psychological", "moving_average"}) >= 1 and len(families) >= 2


def pick_levels(zones, spacing, limit=3):
    """Nearest eligible zones, at least `spacing` apart; inside that spacing keep the higher score."""
    selected = []
    anchor = None
    for z in sorted(zones, key=lambda z: (z["edge_distance_pct"], -z["statistical_score"])):
        if not eligible(z):
            continue
        # Compete only with the group's first zone, so a chain of close zones cannot drift away from price.
        if anchor is not None and abs(z["center"] - anchor) < spacing:
            if z["statistical_score"] > selected[-1]["statistical_score"]:
                selected[-1] = z
            continue
        if selected and abs(z["center"] - selected[-1]["center"]) < spacing:
            continue
        if len(selected) == limit:
            break
        selected.append(z)
        anchor = z["center"]
    return selected


def calculate_levels(data, symbol, args):
    completed = data["completed"]
    price = data["price"]["value"]
    warnings = list(data["warnings"])
    candidates = []
    def add(p, kind, family, date=None, **extra):
        if p is not None:
            candidates.append(dict(price=p, kind=kind, family=family, date=date, **extra))
    prev = data["previous"]
    if prev:
        for name in ("high", "low", "close"):
            add(prev[name], "previous_"+name, "previous_session", prev["date"])
    else:
        warnings.append("報價日之前沒有已收市的交易日，上日高位、低位及收市價留空。")
    recent = completed[-args.lookback:]
    for i, kind, p in swing_points(recent, args.swing_window):
        add(p, kind, "swing", recent[i]["date"], confirmed_on=recent[i+args.swing_window]["date"])
    closes = [b["close"] for b in completed]
    averages = {}
    for period in [20, 50, 250]:
        series = moving_average(closes, period, args.ma)
        current = series[-1]
        old = series[-6] if len(series) >= 6 else None
        slope = (current / old - 1) * 100 if current is not None and old is not None else None
        averages[str(period)] = {"value": current, "type": args.ma.upper(), "period": period,
                                 "slope_5_sessions_pct": slope,
                                 "status": "僅為數值，不代表有效支持或阻力", "available_sessions": len(completed)}
        add(current, "%s%d" % (args.ma.upper(), period), "moving_average", completed[-1]["date"])
        if current is None:
            warnings.append("%s%d：只有 %d 個已收市交易日，資料不足，不會以較短週期代替。" % (args.ma.upper(), period, len(completed)))
    if args.ma == "ema":
        warnings.append("EMA 數值受所用歷史數據長短影響（尤其 250 天線），與其他圖表平台或有輕微差異。")
    end_date = dt.date.fromisoformat(completed[-1]["date"])
    cutoff = (end_date - dt.timedelta(days=364)).isoformat()
    year = [b for b in completed if b["date"] >= cutoff]
    annual = {"high": max(b["high"] for b in year), "low": min(b["low"] for b in year),
              "requested_start": cutoff, "available_start": year[0]["date"],
              "end": completed[-1]["date"], "sessions": len(year),
              "full_window_available": completed[0]["date"] <= cutoff}
    if not annual["full_window_available"]:
        warnings.append("資料未足 52 週，所列為已取得期間的高低位，可能因新上市或資料缺漏。")
    add(annual["high"], "52w_high", "annual_extreme")
    add(annual["low"], "52w_low", "annual_extreme")
    step = args.psych_step or psychological_step(price, symbol)
    mid = math.floor(price / step)
    for k in range(mid-12, mid+14):
        if k * step > 0:
            add(k*step, "round_number", "psychological")
    volume_profile = profile(recent, args.bins)
    coverage = sum(b["volume"] is not None for b in recent) / len(recent)
    volume_usable = not symbol.startswith("^") or args.index_volume
    if symbol.startswith("^"):
        warnings.append("指數本身不可直接買賣，Yahoo 提供的指數成交量並不準確。")
    if not volume_usable:
        warnings.append("因此指數預設不以成交量評分，亦不計算成交密集區（如需使用可加 --index-volume，但僅屬代理數據）。")
        volume_profile = []
    elif coverage == 0:
        warnings.append("此標的沒有有效成交量數據，因此未計算成交密集區及成交量評分。")
    else:
        warnings.append("成交密集區以每日成交量粗略估算，並非真實逐筆成交分佈。")
        if coverage < 1:
            warnings.append("只有 %.1f%% 交易日有有效成交量，成交量評分或有偏差。" % (coverage*100))
    # Local maxima plus spacing avoid three adjacent bins masquerading as peaks.
    peaks = [i for i, p in enumerate(volume_profile)
             if p["volume"] > 0
             and (i == 0 or p["volume"] > volume_profile[i-1]["volume"])
             and (i == len(volume_profile)-1 or p["volume"] >= volume_profile[i+1]["volume"])]
    selected_peaks = []
    for i in sorted(peaks, key=lambda j: -volume_profile[j]["volume"]):
        if all(abs(i-j) > 1 for j in selected_peaks):
            selected_peaks.append(i)
            p = volume_profile[i]
            add(p["price"], "volume_profile_estimate", "volume_profile", bin_low=p["low"], bin_high=p["high"])
            if len(selected_peaks) == 5:
                break
    atr = average_true_range(completed)
    gaps = open_gaps(data["bars"][-args.lookback:], args.lookback, 0.3 * atr)
    for gap in gaps:
        add(gap["high"], "gap_top", "gap", gap["date"], gap_low=gap["low"], gap_high=gap["high"])
        add(gap["low"], "gap_bottom", "gap", gap["date"], gap_low=gap["low"], gap_high=gap["high"])
    if any(not g["complete"] for g in gaps):
        warnings.append("今日裂口以盤中數據計算，收市前或會改變。")
    # Zone width follows volatility: a fixed 0.25% is far too narrow for a stock moving 3% a day.
    tolerance = max(price * args.merge_pct / 100, args.merge_atr * atr)
    zones = cluster_candidates(candidates, tolerance)
    stat_bars = recent if volume_usable else [dict(b, volume=None) for b in recent]
    score_zones(zones, stat_bars, price, atr)
    spacing = args.min_gap_atr * atr
    supports = pick_levels([z for z in zones if z["side"] == "support"], spacing)
    resistances = pick_levels([z for z in zones if z["side"] == "resistance"], spacing)
    if len(supports) < 3 or len(resistances) < 3:
        warnings.append("其中一方不足 3 個合資格位置，按實際列出，不會虛構價位補足。")
    scored_by = "反應次數及成交量" if volume_usable and coverage > 0 else "反應次數"
    warnings.append("統計評分僅按%s排序，並非任何官方強度公式，亦非守穩的機率。" % scored_by)
    warnings.append("「過往反應次數」以現時價位回看過去計算；移動平均線位置亦以今日數值回看計算，並非該線過往實際被觸及的次數。")
    dense = bool(supports and resistances and atr
                 and resistances[-1]["center"] - supports[-1]["center"] < 3 * atr)
    return {"schema_version": VERSION, "symbol": symbol,
            "instrument_name": data["meta"].get("shortName", symbol),
            "currency": data["meta"].get("currency", "unknown"),
            "unit": "index points" if symbol.startswith("^") else data["meta"].get("currency", "unknown"),
            "interval": "1d", "timezone": data["timezone"], "as_of_utc": data["as_of_utc"],
            "reference_price": data["price"], "previous_session": prev,
            "history": {"sessions": len(completed), "start": completed[0]["date"],
                        "end": completed[-1]["date"], "raw_ohlc": "Yahoo quote OHLC; adjclose not used"},
            "moving_averages": averages, "fifty_two_week": annual,
            "atr": {"period": 14, "value": atr, "pct": atr / price * 100},
            "day_change_pct": (price / prev["close"] - 1) * 100 if prev else None,
            "open_gaps": gaps, "dense_cluster": dense,
            "nearest_supports": supports, "nearest_resistances": resistances,
            "at_price": [z for z in zones if z["side"] == "at_price"],
            "zones_by_strength": zones, "volume_profile": volume_profile,
            "method": {"swing_window": args.swing_window, "lookback_sessions": len(recent),
                       "merge_pct": args.merge_pct, "merge_atr": args.merge_atr, "merge_tolerance": tolerance,
                       "min_gap_atr": args.min_gap_atr, "level_spacing": spacing,
                       "eligibility": "at least one reaction, or a previous-session / 52-week / gap level, or two method families besides round numbers and averages",
                       "psychological_step": step, "profile_bins": args.bins,
                       "volume_coverage": coverage, "index_volume_enabled": args.index_volume,
                       "score": "100*(0.5*reactions/max_reactions + 0.5*volume/max_volume); without volume: 100*reactions/max_reactions",
                       "reaction_definition": "price leaves a touch episode on the side it came from and closes at least 0.5 ATR away within 5 sessions",
                       "score_status": "engineering heuristic, not an official strength formula",
                       "test_definition": "consecutive touching days count as one episode; retrospective, not a backtest",
                       "distance_definition": "signed center distance and absolute nearest-edge distance"},
            "interpretation": {"mode": "observation_only", "notes": NOTES, "predictive_signals": [],
                               "limitations": ["日線不能代替即市或週線判斷", "未計及估值、期權等其他資料"]},
            "warnings": list(dict.fromkeys(warnings)), "disclaimer": DISCLAIMER}


def analyze(data, symbol, args):
    from intraday_reactions import attach
    from reaction_watch import build_watch
    watch = build_watch(data, symbol, args, calculate_levels, average_true_range)
    sessions = attach(watch, data, symbol)
    if symbol == "^HSI":
        from hsi_reactions import build
        watch["hsi_comparison"] = build(watch, data, sessions)
        from hsi_patterns import build as build_patterns
        watch["hsi_patterns"] = build_patterns(watch, data, sessions, average_true_range)
    report = calculate_levels(data, symbol, args)
    report["reaction_watch"] = watch
    from index_tracker import INDICES, track_index
    if symbol in INDICES:
        report["index_tracker"] = track_index(data, symbol, args.reaction_days)
    return report


def fmt(value):
    return "—" if value is None else "{:,.2f}".format(value)


DISCLAIMER_FULL = (
    "⚠️ 免責聲明：本內容僅供教育及資訊參考，並不構成任何投資建議、要約或招攬買賣任何證券。"
    "支持位／阻力位僅為根據過往價格計算的觀察位置，並不代表股價必定在該處反彈或回落。"
    "投資涉及風險，證券價格可升可跌，過往表現不代表將來表現。數據來自 Yahoo Finance 免費公開來源，"
    "可能有延遲或錯誤。投資者應自行判斷，如有需要請諮詢持牌專業人士。")

KIND_LABELS = {
    "previous_high": "上日高位", "previous_low": "上日低位", "previous_close": "上日收市價",
    "swing_high": "前期波段高位", "swing_low": "前期波段低位",
    "52w_high": "52 週高位", "52w_low": "52 週低位",
    "round_number": "心理關口", "volume_profile_estimate": "成交密集區（估算）",
    "gap_top": "裂口上沿", "gap_bottom": "裂口下沿",
}

PLAIN_NOTES = [
    "支持位及阻力位只反映價格所處「位置」，並不預示股價升跌，亦非入市時機。",
    "多次測試的位置仍可能被突破，或出現「假突破」（突破後隨即回到原來區間），不代表必定守穩。",
    "心理關口及前期高低位主要作觀察用途，不應單憑這些位置入市。",
    "多種方法重疊於同一價位，才值得多加留意；但仍不代表成功率。",
]
MA_NOTE = "移動平均線須有明顯斜率，且價格確實在附近出現反應，才具參考價值；並非每條平均線都有效。"

GLOSSARY = [
    "支持位：股價下跌時，過往曾在附近止跌或反彈的位置。",
    "阻力位：股價上升時，過往曾在附近受阻或回落的位置。",
    "距離現價：該位置與現價相差的百分比；正數在現價之上，負數在現價之下。",
    "過往觸及次數：過去 120 個交易日內，股價觸及該區間的次數（連續多日觸及只計一次）。",
    "過往反應次數：觸及該區間後，股價從原來方向離開，並在 5 個交易日內拉開至少半個平均日波幅的次數；穿越而過不計算。",
    "平均日波幅（ATR）：近 14 個交易日每日波動幅度（計及裂口）的平均值，反映股價一日之內一般的上落。",
    "心理關口：例如 200、24,000 等整數價位，較多市場參與者留意。",
    "成交密集區：過往成交量較集中的價位區間（以日線估算，並非逐筆成交）。",
]
MA_GLOSSARY = ("天線（EMA）：即指數移動平均線，以近期收市價計算，數字代表日數（例如 50 天線約為 50 個交易日）。"
               "表內出現某條天線，代表該線現時剛好位於該價位附近。")
GAP_GLOSSARY = "裂口：相鄰兩日股價之間沒有成交的空白價位；尚未被填補的裂口常被視作觀察位置。"
ROLE_GLOSSARY = ("支持阻力互換：股價跌穿前期低位後，該位置可能由支持轉為阻力；"
                 "股價升穿前期高位後，該位置亦可能由阻力轉為支持。")
HIGH_KINDS = {"previous_high", "swing_high", "52w_high"}
LOW_KINDS = {"previous_low", "swing_low", "52w_low"}


def kind_label(kind):
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    match = re.fullmatch(r"(EMA|SMA)(\d+)", kind)
    return "%s 天線（%s）" % (match.group(2), match.group(1)) if match else kind


def zone_sources(zone):
    labels = []
    for kind in sorted(set(m["kind"] for m in zone["members"])):
        label = kind_label(kind)
        if label not in labels:
            labels.append(label)
    return "、".join(labels)


def zone_names(report):
    names = {}
    for i, z in enumerate(report["nearest_resistances"], 1):
        names[z["id"]] = "阻力 %d" % i
    for i, z in enumerate(report["nearest_supports"], 1):
        names[z["id"]] = "支持 %d" % i
    for z in report["at_price"]:
        names.setdefault(z["id"], "現價所處區間")
    return names


def display_unit(report):
    return "點" if report["unit"] == "index points" else report["unit"]


def reference_lines(r):
    """Plain-language list: what sits above / below the price, nearest first. No moving averages (user choice A)."""
    price = r["reference_price"]["value"]
    items = []
    b = r["previous_session"]
    if b:
        items += [("上日高位", b["high"]), ("上日低位", b["low"]), ("上日收市價", b["close"])]
    y = r["fifty_two_week"]
    short = "" if y["full_window_available"] else "（資料未足一年）"
    items += [("52 週高位" + short, y["high"]), ("52 週低位" + short, y["low"])]

    def line(name, value):
        return "- %s %s（%+.1f%%）" % (name, fmt(value), (value / price - 1) * 100)

    above = sorted((i for i in items if i[1] > price), key=lambda i: i[1])
    below = sorted((i for i in items if i[1] <= price), key=lambda i: -i[1])
    out = ["現價之上（由近至遠）："] + ([line(*i) for i in above] or ["- 沒有"])
    out += ["現價之下（由近至遠）："] + ([line(*i) for i in below] or ["- 沒有"])
    return out

def strongest(r):
    shown = r["nearest_resistances"] + r["nearest_supports"]
    return max(shown, key=lambda z: (z["statistical_score"], -z["edge_distance_pct"])) if shown else None


def context_lines(r):
    """Plain-language context: daily range, crowding, open gaps."""
    lines = []
    atr = r["atr"]
    if atr["value"]:
        lines.append("近 14 個交易日平均每日波幅約 %s（%.1f%%）。" % (fmt(atr["value"]), atr["pct"]))
        near = [z for z in r["nearest_resistances"][:1] + r["nearest_supports"][:1] if z["edge_distance_pct"] < atr["pct"]]
        if near and "hsi_patterns" not in (r.get("reaction_watch") or {}):
            lines.append("最近的位置與現價相距少於一日平均波幅，一般日內上落已可觸及。")
    if r["dense_cluster"] and "hsi_patterns" not in (r.get("reaction_watch") or {}):
        lines.append("所列支持位及阻力位相距較近（合共少於 3 個平均日波幅），宜視作同一密集區域觀察。")
    for g in r["open_gaps"][:1]:
        lines.append("%s出現%s裂口 %s–%s，尚未完全填補。" % (
            "今日" if not g["complete"] else g["date"] + " ", "向上" if g["direction"] == "up" else "向下",
            fmt(g["low"]), fmt(g["high"])))
    return lines


def reading_notes(r):
    shown = r["nearest_resistances"] + r["nearest_supports"] + r["at_price"]
    has_ma = any("moving_average" in z["families"] for z in shown)
    role_swap = (any(m["kind"] in LOW_KINDS for z in r["nearest_resistances"] for m in z["members"])
                 or any(m["kind"] in HIGH_KINDS for z in r["nearest_supports"] for m in z["members"]))
    has_gap = any("gap" in z["families"] for z in shown)
    glossary = (GLOSSARY[:6] + ([MA_GLOSSARY] if has_ma else []) + GLOSSARY[6:]
                + ([GAP_GLOSSARY] if has_gap else []) + ([ROLE_GLOSSARY] if role_swap else []))
    rules = PLAIN_NOTES[:2] + ([MA_NOTE] if has_ma else []) + PLAIN_NOTES[2:]
    return glossary, rules


def render_html(report, bars, days=60):
    """Display the same analysis in a portable report; never recompute levels."""
    from html_report import render_report
    glossary, rules = reading_notes(report)
    top = strongest(report)
    return render_report(report, bars, days, {
        "unit": display_unit(report), "names": zone_names(report),
        "sources": {z["id"]: zone_sources(z) for z in report["zones_by_strength"]},
        "context": context_lines(report), "references": reference_lines(report),
        "glossary": glossary, "rules": rules, "ai_notice": AI_NOTICE,
        "disclaimer": DISCLAIMER_FULL, "strongest_id": top["id"] if top else None,
    })


def text_report(report):
    r = report
    p = r["reference_price"]
    names = zone_names(r)
    unit = display_unit(r)
    out = []
    source = r.get("data_source", {})
    if source.get("synthetic"):
        out.append("⚠️ 合成測試數據，非真實行情，不可作市況分析。")
    elif source.get("input_file"):
        out.append("⚠️ 離線重播已保存數據，並非即時行情。")
    price_note = ("Yahoo 報價，可能有延遲，不包括盤前及盤後交易" if "regularMarketPrice" in p["kind"]
                  else "採用最新交易日收市價，並非即時價")
    out += ["📍 %s（%s）日線支持位及阻力位" % (r["symbol"], r["instrument_name"]),
            "現價 %s %s（%s；%s）" % (fmt(p["value"]), unit, p.get("time_display", p["time"]), price_note), ""]
    if r.get("index_tracker"):
        from index_tracker_view import text_lines as tracker_lines
        out.extend(tracker_lines(r["index_tracker"]))
        out.append("")
    res, sup, at = r["nearest_resistances"], r["nearest_supports"], r["at_price"]
    parts = []
    if at:
        parts.append("現價位於 %s–%s 觀察區間內" % (fmt(at[0]["low"]), fmt(at[0]["high"])))
    if res:
        parts.append("上方最近阻力位於 %s–%s（%+.2f%%）" % (fmt(res[0]["low"]), fmt(res[0]["high"]), res[0]["distance_pct"]))
    if sup:
        parts.append("下方最近支持位於 %s–%s（%+.2f%%）" % (fmt(sup[0]["low"]), fmt(sup[0]["high"]), sup[0]["distance_pct"]))
    is_hsi = "hsi_patterns" in (r.get("reaction_watch") or {})
    out.append(("一般天線觀察區 · " if is_hsi else "摘要：") + ("；".join(parts) if parts else "附近沒有足夠觀察位置") + "。")
    out.extend(context_lines(r))
    watch = r.get("reaction_watch")
    if watch:
        from reaction_watch import GROUPS, LAYERS, daily_row, hour_row, plan_rows, ratio
        from intraday_reactions import LABELS
        if is_hsi:
            from hsi_pattern_view import text_lines
            out.extend([""] + text_lines(watch["hsi_patterns"]))
        out.extend(["", "舊版一般天線價區對照" if is_hsi else "支持反彈與阻力回落", "支持由區頂以上回試，阻力由區底以下回試。"])
        for heading, rows in plan_rows(watch):
            out.append(heading)
            out.extend("- %s · %s" % pair for pair in rows)
        hsi = watch.get("hsi_comparison")
        if hsi:
            from hsi_reactions import metric_rows
            out.append("恒指專項 · 首觸後一小時的方向及幅度")
            out.append("下列為 v1.5 舊版選位及上日樞軸的歷史對照。")
            for row in metric_rows(hsi["stats"], split=True):
                out.append("- %s · 先順向 %s · 先逆向 %d · 兩邊未到 %d · 同燭先後不明 %d · 最大順向幅度中位數 %s" % row)
            out.append("順向幅度由觸及後下一支五分鐘燭的開市價計至其後一小時最遠點。")
            for heading, rows in plan_rows(hsi):
                out.append("上日樞軸對照 · " + heading)
                out.extend("- %s · %s" % pair for pair in rows)
            forward = hsi["after_rule"]
            out.append("恒指定稿後已收市 %d 日，本次區間的比較如下。" % forward["days"])
            out.extend("- %s · 先順向 %s" % row[:2] for row in metric_rows(forward))
        out.append("最近 %d 個交易日，可重建 %d 日。每個方向每日最多計一次。" %
                   (watch["requested_days"], watch["scored_days"]))
        hourly = watch.get("hourly")
        if hourly:
            out.append("首次觸及後一小時，以其後下一支五分鐘燭的開市價起計，看哪個方向先行半個上日 ATR。")
            out.append("完整五分鐘行情 %d／%d 日。兩邊未到及同燭先後不明均計入分母。" %
                       (hourly["full_sessions"], hourly["requested_sessions"]))
            for layer, label in LAYERS:
                out.append("%s · 可評 %d 次 · 先順向 %s · 先逆向 %d 次 · 兩邊未到 %d 次 · 先後不明 %d 次" %
                           hour_row(label, hourly["scores"][layer]["both"]))
                for side, direction in (("support", "支持"), ("resistance", "阻力")):
                    out.append("- %s · 選用 %d 次 · 觸及 %d 次 · 收回原側 %s · 收市拉開半個 ATR %s · 日內穿位 %d 次 · 收市仍穿位 %d 次" %
                               daily_row(direction, watch["layer_scores"][layer][side]))
            if hourly["excluded"]:
                out.append("分母外的日數按方向計算，包括" + "、".join("%s %d 次" % (LABELS[k], n)
                           for k, n in hourly["excluded"].items()) + "。")
            after = hourly["after_rule"]
            out.append("%s 定稿後，本次區間有 %d 個已收市交易日。" % (watch["rules_fixed_on"], after["days"]))
            for layer, label in LAYERS[:2]:
                c = after["scores"][layer]["both"]
                out.append("- %s定稿後先順向 %s" % (label, ratio(c["favourable"], c["evaluated"])))
            out.append("第一層歷史對照 · 開市條件與收市反應")
        for key, label in GROUPS:
            group = watch["groups"][key]
            out.append("%s · 保留觸及 %s" % (label, ratio(group["both"]["touches"], watch["groups"]["all"]["both"]["touches"])))
            for side, direction in (("support", "支持"), ("resistance", "阻力")):
                count = group[side]
                out.append("- %s · %s · 觸及 %d 次 · 收回原側 %s · 收市拉開半個 ATR %s" %
                           (label, direction, count["touches"], ratio(count["returned"], count["touches"]),
                            ratio(count["moved_half"], count["atr_touches"])))
        if watch["scored_days"] < watch["requested_days"]:
            out.append("部分日期欠缺 250 個已收市交易日的事前行情。只按可重建日期列示結果。")
    for heading, key in (("⬆️ 最近阻力位（股價上升時可能受阻的位置）", "nearest_resistances"),
                         ("⬇️ 最近支持位（股價下跌時可能獲承接的位置）", "nearest_supports")):
        out.extend(["", heading, "| | 價位區間 | 距離現價 | 過往反應次數 | 過往觸及次數 | 形成原因 |", "|---|---:|---:|---:|---:|---|"])
        for z in r[key]:
            out.append("| %s | %s–%s | %+.2f%% | %d 次 | %d 次 | %s |" % (names[z["id"]], fmt(z["low"]), fmt(z["high"]),
                       z["distance_pct"], z["reactions"], z["test_episodes"], zone_sources(z)))
        if len(r[key]) < 3:
            out.append("（只找到 %d 個，按實際列出，不會虛構補足。）" % len(r[key]))
    if at:
        out.append("\n現價所處區間：%s–%s（%s）。現價位於區間之內，因此不視作支持或阻力。"
                   % (fmt(at[0]["low"]), fmt(at[0]["high"]), zone_sources(at[0])))
    uses_volume = any(z["score_components"]["volume_normalized"] is not None for z in r["zones_by_strength"])
    top = strongest(r)
    if top:
        star = "反應次數及成交量綜合最高的區間" if uses_volume else "反應次數最多的區間"
        out.append("\n⭐ 表內%s：%s–%s（距離現價 %+.2f%%，反應 %d 次；由%s組成）"
                   % (star, fmt(top["low"]), fmt(top["high"]), top["distance_pct"], top["reactions"], zone_sources(top)))
    out.extend(["", "📏 其他參考線（現價 %s）" % fmt(p["value"])] + reference_lines(r))
    glossary, rules = reading_notes(r)
    out.extend(["", "📖 名詞解釋"] + ["- " + g for g in glossary])
    out.extend(["", "🧭 使用提醒"] + ["- " + x for x in rules])
    out.extend(["", "📋 進階資料（供深入參考）"])
    for z in res + sup:
        out.append("- %s：內部編號 %s，統計評分 %.1f（按%s的相對排名，並非守穩機率）"
                   % (names[z["id"]], z["id"], z["statistical_score"], "反應次數及成交量" if uses_volume else "反應次數"))
    out.append("- 完整候選位置排名載於 JSON 輸出（--json）。")
    out.extend(["", "⚠️ 資料限制"] + ["- " + w for w in r["warnings"]])
    out.extend(["", "🤖 " + AI_NOTICE, "", DISCLAIMER_FULL])
    return "\n".join(out)


def render_svg(report, bars, size, days):
    width, height = map(int, size.split("x"))
    portrait = height > width
    shown = bars[-days:]
    supports, resistances = report["nearest_supports"], report["nearest_resistances"]
    marked = supports + resistances + report["at_price"][:1]
    names = zone_names(report)
    price = report["reference_price"]["value"]
    profile = (report.get("reaction_watch") or {}).get("hsi_patterns")
    if profile:
        from hsi_pattern_view import chart_zones
        marked = chart_zones(profile,price)
        supports = [z for z in marked if z["side"] == "support"]
        resistances = [z for z in marked if z["side"] == "resistance"]
        names = {z["id"]:z["short"] for z in marked}
    all_y = [b[k] for b in shown for k in ("low", "high")] + [price]
    all_y += [z[k] for z in marked for k in ("low", "high")]
    bottom, top = min(all_y), max(all_y)
    padding = max(top-bottom, price*0.02) * 0.10
    bottom, top = max(0, bottom-padding), top+padding
    left, right = 85, width-(210 if portrait else 260)
    ytop, ybottom = 275 if portrait else 205, 1235 if portrait else 690
    def y(v):
        return ybottom-(v-bottom)/(top-bottom)*(ybottom-ytop)
    def esc(v):
        return html.escape(str(v), quote=True)
    elements = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img">' % (width,height,width,height),
                '<title>%s daily price chart and key-level candidates</title>' % esc(report["symbol"]),
                '<desc>Yahoo daily OHLC. Statistical observations, not predictions. Data may be delayed.</desc>',
                '<rect width="%d" height="%d" fill="#101b2b"/>' % (width, height),
                '<g font-family="Arial, Noto Sans TC, PingFang TC, sans-serif" fill="#e8eef5">']
    def text(x, yy, value, size=25, fill="#e8eef5", weight="400"):
        elements.append('<text x="%.2f" y="%.2f" font-size="%s" fill="%s" font-weight="%s">%s</text>' % (x,yy,size,fill,weight,esc(value)))
    def line(x1,y1,x2,y2,color,width_=1,dash=""):
        elements.append('<line x1="%.2f" y1="%.2f" x2="%.2f" y2="%.2f" stroke="%s" stroke-width="%s"%s/>' %
                        (x1,y1,x2,y2,color,width_, (' stroke-dasharray="'+dash+'"') if dash else ""))
    text(left,85 if portrait else 62,report["symbol"]+"  |  KEY LEVELS",46 if portrait else 40,weight="700")
    text(left,138 if portrait else 108,"日線 · 現價 %s %s" % (fmt(price),display_unit(report)),30)
    text(left,183 if portrait else 147,report["reference_price"].get("time_display", report["reference_price"]["time"]),24, "#aabbcf")
    source = report.get("data_source", {})
    banner = ("合成測試數據 · 非真實行情" if source.get("synthetic") else
              "OFFLINE SNAPSHOT · 非即時行情" if source.get("input_file") else
              "觀察參考位置 · 並非買賣訊號")
    text(left,224 if portrait else 180,banner,24,"#e7c36d")
    for i in range(6):
        v = bottom+(top-bottom)*i/5
        line(left,y(v),right,y(v),"#273950")
        text(12,y(v)+6,fmt(v),18,"#9cacc0")
    for z in marked:
        color = "#5cd9b0" if z["side"]=="support" else "#fb8f91" if z["side"]=="resistance" else "#d8bb6f"
        elements.append('<rect x="%s" y="%.2f" width="%s" height="%.2f" fill="%s" opacity="0.12"/>' % (left,y(z["high"]),right-left,max(1,y(z["low"])-y(z["high"])),color))
        line(left,y(z["center"]),right,y(z["center"]),color,1.5,"7 5")
    step = (right-left)/len(shown)
    for i,b in enumerate(shown):
        x = left+(i+0.5)*step
        color = "#56d8ad" if b["close"]>=b["open"] else "#f5838a"
        line(x,y(b["low"]),x,y(b["high"]),color,1.5)
        elements.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" fill="%s" opacity="%s"/>' %
                        (x-step*0.32,y(max(b["open"],b["close"])),max(1,step*0.64),max(1,abs(y(b["open"])-y(b["close"]))),color,"1" if b["complete"] else ".45"))
    # Distribute text labels while leader lines preserve their exact price position.
    labels = [(y(z["center"]),names.get(z["id"],z["id"]).replace("現價所處區間","現價區")+" "+fmt(z["center"]),"#5cd9b0" if z["side"]=="support" else "#fb8f91" if z["side"]=="resistance" else "#d8bb6f") for z in marked]
    labels.append((y(price),"現價 "+fmt(price),"#f5da88"))
    labels.sort()
    # Labels never leave the plot: shrink the gap when there are many of them.
    gap = min(30, (ybottom-ytop-20)/max(1,len(labels)-1))
    positions=[]
    for pos,_,_ in labels:
        positions.append(max(pos, positions[-1]+gap if positions else ytop+10))
    if positions and positions[-1] > ybottom-10:
        positions[-1] = ybottom-10
        for i in range(len(positions)-2,-1,-1):
            positions[i] = min(positions[i],positions[i+1]-gap)
    for (actual,label,color),pos in zip(labels,positions):
        line(right,actual,right+22,pos,color,1)
        text(right+28,pos+6,label,21,color)
    line(left,y(price),right,y(price),"#f5da88",1,"3 4")
    for i in sorted(set([0,len(shown)//2,len(shown)-1])):
        x = min(right-100,max(left,left+i*step-40))
        text(x,ybottom+35,shown[i]["date"],19,"#9cacc0")
    if not shown[-1]["complete"]:
        text(left,ybottom+70,"最後交易日尚未收市（淡色）",23,"#e7c36d")
    table_top = ybottom + (120 if portrait else 90)
    groups = [("阻力位（上升時可能受阻）",resistances,"#fb8f91"),("支持位（下跌時可能獲承接）",supports,"#5cd9b0")]
    for group_index,(title,zones,color) in enumerate(groups):
        x = left if portrait else left+group_index*(width/2-30)
        yy = table_top+group_index*205 if portrait else table_top
        text(x,yy,title,28,color,"700")
        for i,z in enumerate(zones):
            text(x,yy+42+i*43,"%s  %s–%s  (%+.2f%%)" % (names.get(z["id"],z["id"]),fmt(z["low"]),fmt(z["high"]),z["distance_pct"]),25)
        if not zones:
            text(x,yy+42,"觀望，暫無合資格價位" if profile else "資料不足",24)
    foot = 1770 if portrait else 950
    text(left,foot,"恒指支持位及阻力位 · 按上日收市劃位" if profile else "反應次數多 ≠ 必定守穩；心理關口僅供觀察。",24,"#e7c36d")
    volume_note = ("成交密集區：以日線估算。" if report["volume_profile"] else
                   "指數沒有真實成交量，未計算成交密集區。" if report["symbol"].startswith("^") else
                   "沒有可用成交量，未計算成交密集區。")
    text(left,foot+38,volume_note,22,"#aabbcf")
    text(left,foot+77,DISCLAIMER,22,"#aabbcf")
    text(left,foot+112,"圖像及價位由 A.I. 自動計算及生成，未經人工核實。",22,"#aabbcf")
    elements.extend(["</g>","</svg>"])
    return "\n".join(elements)


BRAND = {"bg": "#F6F8F9", "panel": "#FFFFFF", "navy": "#131C47", "indigo": "#0C0380",
         "cyan": "#04D9DC", "cyan_soft": "#E3FAFA", "muted": "#5B6478", "grid": "#E3E7EE",
         "up": "#139A68", "down": "#D64545", "support": "#139A68", "resistance": "#D64545"}
CARD_FONT = "PingFang HK, PingFang TC, Noto Sans TC, Microsoft JhengHei, Arial, sans-serif"


NARROW = {".": 0.28, ",": 0.28, " ": 0.28, ":": 0.28, "-": 0.36, "(": 0.34, ")": 0.34,
          "%": 0.88, "+": 0.6, "–": 0.56, "·": 0.3, "/": 0.36, "^": 0.5, "I": 0.3, "W": 0.94, "M": 0.86}


def text_width(value, size, bold=False):
    total = 0.0
    for c in str(value):
        if ord(c) > 0x2E7F:
            total += 1.0
        elif c in NARROW:
            total += NARROW[c]
        elif c.isupper():
            total += 0.68
        elif c.isdigit():
            total += 0.58
        else:
            total += 0.53
    return total * size * (1.06 if bold else 1.0)


def wrap_text(value, size, width):
    """Wrap Chinese per character but keep runs of Latin letters and digits together."""
    tokens = re.findall(r"[A-Za-z0-9.%+\-]+|.", value)
    lines, line = [], ""
    for token in tokens:
        # Closing punctuation never starts a line.
        if line and text_width(line + token, size) > width and token not in "，。、；：）」%":
            lines.append(line.rstrip())
            line = token.lstrip()
        else:
            line += token
    return lines + ([line] if line else [])


def clip_text(value, size, width):
    if text_width(value, size) <= width:
        return value
    while value and text_width(value + "…", size) > width:
        value = value[:-1]
    return value + "…"


def logo_data_uri(filename="hkiei-logo.png"):
    path = Path(__file__).with_name("assets") / filename
    try:
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def short_sources(zone):
    labels = []
    for label in zone_sources(zone).split("、"):
        label = re.sub(r"（.*?）", "", label).replace("前期波段", "前期")
        if label not in labels:
            labels.append(label)
    return "、".join(labels)


def render_card(report, bars, days=40):
    """1080x1350 shareable report card in HKIEI brand colours."""
    W, H, M = 1080, 1350, 56
    c = BRAND
    els = []
    def esc(v):
        return html.escape(str(v), quote=True)
    def text(x, y, value, size, fill=c["navy"], weight="400", anchor="start"):
        els.append('<text x="%.1f" y="%.1f" font-size="%s" fill="%s" font-weight="%s" text-anchor="%s">%s</text>'
                   % (x, y, size, fill, weight, anchor, esc(value)))
    def rect(x, y, w, h, fill, rx=0, opacity=1, stroke=None):
        els.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%s" fill="%s" opacity="%s"%s/>'
                   % (x, y, max(0, w), max(0, h), rx, fill, opacity, (' stroke="%s" stroke-width="2"' % stroke) if stroke else ""))
    def line(x1, y1, x2, y2, color, w=1, dash=""):
        els.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s"%s/>'
                   % (x1, y1, x2, y2, color, w, (' stroke-dasharray="%s"' % dash) if dash else ""))

    p = report["reference_price"]
    price = p["value"]
    unit = display_unit(report)
    names = zone_names(report)
    res, sup, at = report["nearest_resistances"], report["nearest_supports"], report["at_price"]
    patterns = (report.get("reaction_watch") or {}).get("hsi_patterns")
    if patterns:
        from hsi_pattern_view import chart_zones
        projected = chart_zones(patterns,price)
        sup = [z for z in projected if z["side"]=="support"]
        res = [z for z in projected if z["side"]=="resistance"]
        at = []
        names = {z["id"]:z["short"] for z in projected}
    rect(0, 0, W, H, c["bg"])

    # Header: logo, title, data date.
    logo = logo_data_uri()
    if logo:
        els.append('<image x="%d" y="34" width="276" height="94" href="%s" xlink:href="%s"/>' % (M, logo, logo))
    else:
        text(M, 96, "投創教育 HKIEI", 40, c["indigo"], "700")
    text(W-M, 78, "支持位／阻力位觀察", 32, c["navy"], "700", "end")
    text(W-M, 116, "日線 · 數據截至 " + p.get("time_display", p["time"]), 21, c["muted"], anchor="end")
    rect(M, 148, W-2*M, 4, c["cyan"])

    # Title: instrument, price, day change, A.I. badge.
    name = report["instrument_name"]
    name = clip_text(name, 42, 600)
    text(M, 212, name, 42, c["navy"], "700")
    if name != report["symbol"]:
        text(M + text_width(name, 42, True) + 18, 212, report["symbol"], 24, c["muted"])
    price_text = fmt(price)
    text(M, 292, price_text, 68, c["indigo"], "700")
    x = M + text_width(price_text, 68, True) + 8
    text(x, 292, unit, 26, c["muted"])
    change = report.get("day_change_pct")
    if change is not None:
        label = "%+.2f%%" % change
        cx = x + text_width(unit, 26) + 22
        color = c["up"] if change >= 0 else c["down"]
        rect(cx, 252, text_width(label, 26) + 28, 44, color, 22)
        text(cx + 14, 283, label, 26, "#FFFFFF", "700")
        text(cx + text_width(label, 26) + 40, 283, "較上日收市", 20, c["muted"])
    badge = "A.I. 計算及生成"
    bw = text_width(badge, 20) + 32
    rect(W-M-bw, 192, bw, 38, c["cyan_soft"], 19, stroke=c["cyan"])
    text(W-M-bw/2, 218, badge, 20, c["indigo"], "700", "middle")
    source = report.get("data_source", {})
    banner = ("合成測試數據 · 非真實行情" if source.get("synthetic") else
              "離線重播 · 非即時行情" if source.get("input_file") else None)
    if banner:
        text(W-M, 290, banner, 20, c["down"], "700", "end")

    # Panel: mini price chart on the left, ladder of levels on the right.
    top, bottom = 330, 900
    rect(40, top, W-80, bottom-top, c["panel"], 20)
    text(M+16, top+46, "恒指支持位及阻力位" if patterns else "價位階梯（由高至低）", 26, c["navy"], "700")
    text(W-M-16, top+46, "近 %d 個交易日股價" % min(days, len(bars)), 18, c["muted"], anchor="end")
    shown = bars[-days:]
    marked = res + sup + at[:1]
    ys = [b[k] for b in shown for k in ("low", "high")] + [price] + [z[k] for z in marked for k in ("low", "high")]
    lo, hi = min(ys), max(ys)
    pad = max(hi-lo, price*0.02) * 0.06
    lo, hi = lo-pad, hi+pad
    cx0, cx1, cy0, cy1 = 136, 476, top+80, bottom-36
    def y(v):
        return cy1 - (v-lo) / (hi-lo) * (cy1-cy0)
    for i in range(5):
        v = lo + (hi-lo) * i / 4
        line(cx0, y(v), cx1, y(v), c["grid"])
        if 0 < i < 4:
            text(cx0-8, y(v)+5, fmt(v), 14, c["muted"], anchor="end")
    for z in marked:
        color = c["support"] if z["side"] == "support" else c["resistance"] if z["side"] == "resistance" else c["indigo"]
        rect(cx0, y(z["high"]), cx1-cx0, max(2, y(z["low"])-y(z["high"])), color, opacity=0.14)
    atr = report["atr"]["value"]
    if atr:
        rect(cx1+6, y(price+atr), 8, y(price-atr)-y(price+atr), c["cyan"], 4)
    step = (cx1-cx0) / max(1, len(shown))
    for i, b in enumerate(shown):
        bx = cx0 + (i+0.5) * step
        color = c["up"] if b["close"] >= b["open"] else c["down"]
        opacity = "1" if b.get("complete", True) else ".4"
        els.append('<g opacity="%s">' % opacity)
        line(bx, y(b["low"]), bx, y(b["high"]), color, 1.4)
        rect(bx-step*0.32, y(max(b["open"], b["close"])), max(1.5, step*0.64),
             max(1.5, abs(y(b["open"])-y(b["close"]))), color)
        els.append('</g>')
    line(cx0, y(price), cx1, y(price), c["indigo"], 1.6, "5 4")

    rows = [(names[z["id"]], z) for z in reversed(res)] + [("現價", None)] + [(names[z["id"]], z) for z in sup]
    rx0, row_top, row_bottom = 540, cy0, cy1
    row_h = (row_bottom-row_top) / max(1, len(rows))
    for i, (label, z) in enumerate(rows):
        ry = row_top + (i+0.5) * row_h
        actual = y(z["center"]) if z else y(price)
        color = (c["indigo"] if z is None else c["support"] if z["side"] == "support" else c["resistance"])
        line(cx1+18, actual, rx0-8, ry, color, 1.2)
        rect(rx0, ry-19, 104, 38, color, 19)
        text(rx0+52, ry+8, label, 21, "#FFFFFF", "700", "middle")
        if z is None:
            text(rx0+122, ry+10, fmt(price) + " " + unit, 28, c["indigo"], "700")
            if at:
                text(rx0+122, ry+36, clip_text("位於 %s–%s 區間內" % (fmt(at[0]["low"]), fmt(at[0]["high"])), 17, 340), 17, c["muted"])
            continue
        distance = "%+.2f%%" % z["distance_pct"]
        span = "%s–%s" % (fmt(z["low"]), fmt(z["high"]))
        room = W-M-16 - text_width(distance, 22, True) - 18 - (rx0+122)
        size = 26
        while size > 18 and text_width(span, size, True) > room:
            size -= 1
        text(rx0+122, ry+6, span, size, c["navy"], "700")
        text(W-M-16, ry+6, distance, 22, color, "700", "end")
        detail = z["pattern_name"] if patterns else "反應 %d 次 · %s" % (z["reactions"], short_sources(z))
        text(rx0+122, ry+32, clip_text(detail, 17, W-M-16-rx0-122), 17, c["muted"])
    if not res:
        text(rx0, row_top-8, "上方附近未有合資格阻力位", 18, c["muted"])
    if not sup:
        text(rx0, row_bottom+24, "下方附近未有合資格支持位", 18, c["muted"])

    # Key points.
    points = []
    watch = report.get("reaction_watch")
    if watch and "hsi_patterns" in watch:
        from reaction_watch import ratio
        pat = watch["hsi_patterns"]["stats"]["combined"]["both"]
        if pat["evaluated"]:
            points.append("恒指追蹤價位近 %d 日，有效反應 %s，先順向半個 ATR %s。" %
                          (watch["requested_days"], ratio(pat["reaction"],pat["evaluated"]), ratio(pat["half"]["favourable"],pat["evaluated"])))
        else:
            points.append("恒指近 %d 日，開市合資格 %d 次，實際觸及 %d 次。一小時無可評樣本。" %
                          (watch["requested_days"], pat["selected"],pat["touched"]))
    elif watch and "hourly" in watch:
        hours = watch["hourly"]["scores"]
        if hours["combined"]["both"]["evaluated"]:
            rates = []
            for layer in ("primary", "backup"):
                score = hours[layer]["both"]
                rates.append("%d/%d" % (score["favourable"], score["evaluated"]) if score["evaluated"] else "無樣本")
            points.append("近 %d 個交易日，觸及後一小時先順向半個 ATR。第一層 %s，後備 %s。" %
                          (watch["requested_days"], *rates))
        else:
            points.append("近 %d 個交易日，首次觸及後一小時暫無可評樣本。" % watch["requested_days"])
    elif watch:
        recent = watch["groups"]["outside"]["both"]
        if recent["touches"]:
            points.append("近 %d 個交易日，區外回試 %d 次，%d 次收回原側，%d 次收市拉開半個 ATR。" %
                          (watch["requested_days"], recent["touches"], recent["returned"], recent["moved_half"]))
    if atr:
        points.append("近 14 日平均每日波幅約 %s（%.1f%%），股價圖右側青色直條為現價上下一日波幅。" % (fmt(atr), report["atr"]["pct"]))
    best = None if patterns else strongest(report)
    if best:
        points.append("表內反應次數最多：%s %s–%s（%d 次）。" % (names[best["id"]], fmt(best["low"]), fmt(best["high"]), best["reactions"]))
    if report["dense_cluster"] and not patterns:
        points.append("所列位置相距較近，宜視作同一密集區域觀察。")
    elif report["open_gaps"]:
        g = report["open_gaps"][0]
        points.append("未填補裂口：%s–%s。" % (fmt(g["low"]), fmt(g["high"])))
    yy = 950
    for point in points[:3]:
        wrapped = wrap_text(point, 22, W-2*M-30)[:2]
        if yy + 32 * (len(wrapped) - 1) > 1055:
            break
        for j, part in enumerate(wrapped):
            if j == 0:
                els.append('<circle cx="%d" cy="%d" r="5" fill="%s"/>' % (M+6, yy-8, c["cyan"]))
            text(M+24, yy, part, 22, c["navy"])
            yy += 32
        yy += 6

    # A.I. notice and disclaimer.
    box_top = 1080
    notice = wrap_text(AI_NOTICE, 19, W-2*M-150)
    box_h = 28 + 28 * len(notice)
    rect(M, box_top, W-2*M, box_h, c["cyan_soft"], 14, stroke=c["cyan"])
    rect(M+18, box_top+16, 96, 32, c["indigo"], 16)
    text(M+66, box_top+38, "A.I. 生成", 17, "#FFFFFF", "700", "middle")
    for j, part in enumerate(notice):
        text(M+130, box_top+39 + j*28, part, 19, c["navy"])
    disclaimer = DISCLAIMER_FULL.replace("⚠️ ", "")
    dy = box_top + box_h + 34
    for part in wrap_text(disclaimer, 16, W-2*M):
        text(M, dy, part, 16, c["muted"])
        dy += 23
    line(M, H-52, W-M, H-52, c["grid"])
    text(M, H-22, "© 投創教育 HKIEI · hkiei.com", 18, c["navy"], "700")
    text(W-M, H-22, "觀察參考位置 · 並非買賣訊號", 18, c["muted"], anchor="end")

    head = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="%d" height="%d" viewBox="0 0 %d %d" role="img">' % (W, H, W, H))
    return "\n".join([head, '<title>%s key levels report card</title>' % esc(report["symbol"]),
                      '<g font-family="%s">' % CARD_FONT] + els + ["</g>", "</svg>"])


def write_card(path, svg):
    """Try native macOS rasterizers without leaving a failed PNG at the output path."""
    if path.suffix.lower() != ".png":
        write_text(path, svg)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent.resolve()) as td:
        source = Path(td) / "card.svg"
        output = Path(td) / "card.png"
        source.write_text(svg, encoding="utf-8")
        appkit = Path(__file__).with_name("assets") / "card_png.js"
        renderers = [["/usr/bin/sips", "-s", "format", "png", str(source), "--out", str(output)],
                     ["/usr/bin/osascript", "-l", "JavaScript", str(appkit), str(source), str(output)]]
        for command in renderers:
            if not Path(command[0]).is_file():
                continue
            try:
                done = subprocess.run(command, capture_output=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if done.returncode == 0 and output.is_file():
                with output.open("rb") as image:
                    header = image.read(24)
                if (header.startswith(b"\x89PNG\r\n\x1a\n") and int.from_bytes(header[16:20], "big") == 1080
                        and int.from_bytes(header[20:24], "big") == 1350):
                    output.replace(path)
                    return
            if output.exists():
                output.unlink()
    raise ValueError("本機未能輸出 1080×1350 PNG 報告卡，請以 .svg 檔名重試。")


def parser():
    p = argparse.ArgumentParser(description="日線支持位／阻力位觀察工具（非預測、非投資建議）")
    p.add_argument("symbol", help="0700.HK / ^HSI / TSLA / ^IXIC")
    p.add_argument("--size", choices=["1080x1920","1920x1080"], default="1080x1920")
    p.add_argument("--svg", type=Path, help="SVG 儲存位置，預設 SYMBOL_SIZE.svg")
    html_output = p.add_mutually_exclusive_group()
    html_output.add_argument("--html", type=Path, help="HTML 報告位置；預設與 SVG 同名，副檔名 .html")
    html_output.add_argument("--no-html", action="store_true", help="略過預設 HTML 報告（供批次處理）")
    p.add_argument("--json", nargs="?", const="-", metavar="PATH", help="輸出 JSON 至 stdout，或寫入 PATH")
    p.add_argument("--input-json", type=Path, help="離線重播已保存的 Yahoo 原始 JSON（並非最新行情）")
    p.add_argument("--intraday-json", type=Path, help="使用同一代號及市場時區的 Yahoo 五分鐘原始行情")
    p.add_argument("--save-raw", type=Path, help="保存最後使用的原始 Yahoo JSON 供重現")
    p.add_argument("--overwrite", action="store_true", help="明示允許覆寫已存在的輸出檔")
    p.add_argument("--ma", choices=["ema","sma"], default="ema")
    p.add_argument("--lookback", type=int, default=120, help="波段、測試次數及成交量的回看交易日數")
    p.add_argument("--swing-window", type=int, default=3, help="確認波段高低位所需的左右交易日數")
    p.add_argument("--merge-pct", type=float, default=0.25, help="合併相近價位的最小跨度（現價百分比）")
    p.add_argument("--merge-atr", type=float, default=0.3, help="合併跨度至少為平均日波幅（ATR）的倍數")
    p.add_argument("--min-gap-atr", type=float, default=0.5, help="所列位置之間最少相隔多少個 ATR")
    p.add_argument("--card", type=Path, help="輸出 1080x1350 報告卡（.png 或 .svg）")
    p.add_argument("--psych-step", type=float, help="心理關口間距，恒指預設 500")
    p.add_argument("--bins", type=int, default=48)
    p.add_argument("--chart-days", type=int, default=60)
    p.add_argument("--index-volume", action="store_true", help="允許使用 Yahoo 指數代理成交量（並非真實成交）")
    p.add_argument("--no-extend", action="store_true", help="只取一年數據；不足 250 日時 250 天線顯示缺值")
    p.add_argument("--reaction-days", type=int, default=30, help="逐日重建回試成績的已收市交易日數（預設 30）")
    p.add_argument("--timeout", type=float, default=20)
    return p


def validate_args(args):
    if not 1 <= args.reaction_days <= 252:
        raise ValueError("--reaction-days 必須 1–252。")
    if not 10 <= args.lookback <= 1000:
        raise ValueError("--lookback 必須 10–1000。")
    if not 1 <= args.swing_window <= 20 or args.lookback < 2*args.swing_window+1:
        raise ValueError("--swing-window 必須 1–20，且 lookback 至少 2*window+1。")
    if not math.isfinite(args.merge_pct) or not 0 < args.merge_pct <= 5:
        raise ValueError("--merge-pct 必須大於 0 且不超過 5。")
    for name in ("merge_atr", "min_gap_atr"):
        value = getattr(args, name)
        if not math.isfinite(value) or not 0 <= value <= 3:
            raise ValueError("--%s 必須 0–3。" % name.replace("_", "-"))
    if args.psych_step is not None and (not math.isfinite(args.psych_step) or args.psych_step <= 0):
        raise ValueError("--psych-step 必須為正數。")
    if not 8 <= args.bins <= 200 or not 10 <= args.chart_days <= 200:
        raise ValueError("--bins 必須 8–200；--chart-days 必須 10–200。")
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 60:
        raise ValueError("--timeout 必須 1–60 秒。")


def write_text(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def snapshot_time(payload, now):
    """A saved intraday bar cannot become a close as the replay clock advances."""
    captured = number(payload.get("keyLevelsCapturedAt"), True)
    if captured is not None:
        if captured > now + 300:
            raise ValueError("快照記錄的擷取時間晚於目前時間。")
        return captured
    results = (payload.get("chart") or {}).get("result") or []
    if results:
        chart = results[0]
        meta = chart.get("meta", {})
        regular = meta.get("currentTradingPeriod", {}).get("regular", {})
        start, end = (number(regular.get(k), True) for k in ("start", "end"))
        quote = number(meta.get("regularMarketTime"), True)
        stamps = chart.get("timestamp") or []
        last = number(stamps[-1], True) if stamps else None
        if start and end and quote and last and start <= last <= quote < end and quote <= now:
            return quote
    return now


def add_earlier_history(data, extra, symbol):
    """Retain the original quote and all overlapping bars, with earlier bars for replay."""
    from reaction_watch import MIN_HISTORY, year_start
    longer = parse_chart(extra, now=dt.datetime.fromisoformat(data["as_of_utc"]).timestamp())
    if not longer["meta"].get("symbol") or normalize_symbol(longer["meta"]["symbol"]) != symbol:
        raise ValueError("延長歷史回應代號不符。")
    if longer["timezone"] != data["timezone"]:
        raise ValueError("延長歷史的市場時區不符。")
    first = data["bars"][0]["date"]
    earlier = [b for b in longer["completed"] if b["date"] < first]
    data["reaction_history"] = earlier + data["completed"]
    if len(data["completed"]) < MIN_HISTORY and earlier:
        cutoff = year_start(data["completed"][-1]["date"], 2)
        added = [b for b in earlier if b["date"] >= cutoff]
        data["bars"] = added + data["bars"]
        data["completed"] = added + data["completed"]
        if data["previous"] is None:
            prior = [b for b in data["completed"] if b["date"] < data["price"]["date"]]
            data["previous"] = prior[-1] if prior else None
        if added:
            data["warnings"].append("一年行情不足 250 日，現價圖使用最多兩年的已收市行情。")


def load_market_data(symbol, args, tracking=False, with_intraday=True):
    """Load and validate the same quote sources for analysis and pattern tracking."""
    urls = []
    if args.input_json:
        payload = json.loads(args.input_json.read_text(encoding="utf-8"))
    else:
        payload, url = fetch_chart(symbol, timeout=args.timeout)
        urls.append(url)
    received = time.time()
    captured = snapshot_time(payload, received) if args.input_json else received
    data = parse_chart(payload, now=captured)
    returned = data["meta"].get("symbol")
    if returned and normalize_symbol(returned) != symbol:
        raise ValueError("回應代號 %s 與要求 %s 不符。" % (returned,symbol))
    extra = payload.get("keyLevelsEarlierChart")
    if args.input_json and extra is not None:
        add_earlier_history(data, extra, symbol)
    elif not args.input_json and not args.no_extend:
        try:
            # A replay day may itself need two years of warm-up. Keep the normal one-year quote.
            extended, url = fetch_chart(symbol, "5y", args.timeout)
            urls.append(url)
            add_earlier_history(data, extended, symbol)
            if args.save_raw:
                payload = dict(payload, keyLevelsEarlierChart=extended)
        except ValueError as error:
            data["warnings"].append("未能補充較早行情，已使用可核對的日期。" + str(error))
    if args.input_json:
        data["warnings"].append("離線重播：行情來自已保存的檔案，不代表最新市況。")
    if not with_intraday:
        return data, payload, captured, urls
    intraday = payload.get("keyLevelsIntradayChart")
    if args.intraday_json:
        intraday = json.loads(args.intraday_json.read_text(encoding="utf-8"))
        if not isinstance(intraday, dict):
            raise ValueError("指定的五分鐘檔案無法使用。")
        data["intraday_explicit"] = True
    elif intraday is None and not args.input_json:
        stop = int(captured)
        earliest = stop - 59 * 86400
        # Older daily sources can still be scored at the close.
        if data["bars"][-1]["timestamp"] >= earliest:
            warmup = 20 if symbol == "^HSI" or tracking else 0
            first = data["completed"][-min(args.reaction_days + warmup, len(data["completed"]))]
            try:
                intraday, url = fetch_chart(symbol, timeout=args.timeout,
                                            period=(max(earliest, int(first["timestamp"]) - 86400), stop))
                urls.append(url)
            except ValueError as error:
                data["warnings"].append("未能取得五分鐘行情。" + str(error))
    if intraday is not None:
        data["intraday_chart"] = intraday
        if args.save_raw:
            payload = dict(payload, keyLevelsIntradayChart=intraday)
    return data, payload, captured, urls


def export_report(report):
    """Project internal calculations only at the user-facing JSON boundary."""
    output = dict(report)
    if report.get("index_tracker"):
        from index_tracker_view import public_tracker
        output["index_tracker"] = public_tracker(report["index_tracker"])
    watch = report.get("reaction_watch")
    if watch and watch.get("hsi_patterns"):
        from hsi_pattern_view import public_history
        output["reaction_watch"] = dict(watch, hsi_patterns=public_history(watch["hsi_patterns"]))
    return output


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        validate_args(args)
        symbol = normalize_symbol(args.symbol)
        data, payload, captured, urls = load_market_data(symbol, args)
        r = analyze(data,symbol,args)
        r["data_source"] = {"provider": "Yahoo Finance chart API", "requests": urls,
                            "input_file": str(args.input_json) if args.input_json else None,
                            "synthetic": bool(data["meta"].get("synthetic")) if args.input_json else False}
        output = args.svg or Path(symbol.replace("^", "index_")+"_"+args.size+".svg")
        html_output = None if args.no_html else (args.html or output.with_suffix(".html"))
        paths = ([output] + ([Path(args.json)] if args.json and args.json != "-" else [])
                 + ([args.save_raw] if args.save_raw else []) + ([args.card] if args.card else [])
                 + ([html_output] if html_output else []))
        resolved = [p.resolve() for p in paths]
        protected = [Path(__file__).resolve(), Path(__file__).with_name("SKILL.md").resolve(),
                     Path(__file__).with_name("test_key_levels.py").resolve(),
                     Path(__file__).with_name("test_html_report.py").resolve(),
                     Path(__file__).with_name("html_report.py").resolve(),
                     Path(__file__).with_name("reaction_watch.py").resolve(),
                     Path(__file__).with_name("test_reaction_watch.py").resolve(),
                     Path(__file__).with_name("intraday_reactions.py").resolve(),
                     Path(__file__).with_name("hsi_reactions.py").resolve(),
                     Path(__file__).with_name("test_hsi_reactions.py").resolve(),
                     Path(__file__).with_name("test_intraday_reactions.py").resolve()]
        protected += [Path(__file__).with_name(name).resolve() for name in ("hsi_patterns.py", "hsi_pattern_view.py", "test_hsi_patterns.py", "index_tracker.py", "index_tracker_view.py", "index_week.py", "track_indices.py", "test_index_tracker.py", "test_index_week.py")]
        protected += [p.resolve() for p in Path(__file__).with_name("assets").rglob("*") if p.is_file()]
        if args.input_json:
            protected.append(args.input_json.resolve())
        if args.intraday_json:
            protected.append(args.intraday_json.resolve())
        if len(set(resolved)) != len(resolved) or any(p in protected for p in resolved):
            raise ValueError("輸出路徑不可重疊，亦不可覆寫輸入檔或 skill 原始檔。")
        if not args.overwrite and any(p.exists() for p in paths):
            raise ValueError("輸出檔已存在，請選新檔名，或在確認覆寫後加 --overwrite。")
        # Render first so a missing asset or failed card conversion can be retried with the same filenames.
        html_document = render_html(r, data["bars"], args.chart_days) if html_output else None
        chart_document = render_svg(r,data["bars"],args.size,args.chart_days)
        if args.card:
            write_card(args.card, render_card(r, data["bars"]))
            r["card_path"] = str(args.card.resolve())
        write_text(output,chart_document)
        r["svg_path"] = str(output.resolve())
        if html_output:
            write_text(html_output, html_document)
            r["html_path"] = str(html_output.resolve())
        if args.save_raw:
            write_text(args.save_raw,json.dumps(dict(payload, keyLevelsCapturedAt=captured),ensure_ascii=False,allow_nan=False))
        encoded = json.dumps(export_report(r),ensure_ascii=False,indent=2,allow_nan=False) if args.json else None
        if args.json == "-":
            print(encoded)
        else:
            if args.json:
                write_text(Path(args.json),encoded)
            print(text_report(r))
            if html_output:
                print("\nHTML 報告: "+r["html_path"])
            print("\nSVG: "+r["svg_path"])
            if args.card:
                print("報告卡: "+r["card_path"])
        return 0
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError) as error:
        print("key-levels: "+str(error),file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
