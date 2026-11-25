"""Render the weekly index chart from the same dated bars and confirmed zones as the report."""
import datetime as dt
import math
from bisect import bisect_left
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from index_tracker_view import band, esc, price
from key_levels import number

TRADING_DAYS = 30
ROLE = {"resistance": "阻力", "support": "支持", "inside": "收市所在區"}


def safe_source(source, url):
    link = urlsplit(url) if isinstance(url, str) else None
    return (isinstance(source, str) and bool(source.strip()) and link
            and link.scheme == "https" and bool(link.hostname) and not link.username and not link.password)


def chart_data(data, symbol, bars, start, end, supplement=None):
    candles = [{k: bar[k] for k in ("date", "open", "high", "low", "close")} for bar in bars[-TRADING_DAYS:]]
    last = candles[-1]
    page = "https://finance.yahoo.com/quote/%5E" + symbol.lstrip("^") + "/"
    quote = dict(kind="close", at=last["date"], value=last["close"], source="Yahoo Finance", url=page)
    if data["meta"].get("synthetic"):
        quote.update(source="合成測試走勢", url=None)
        return dict(bars=candles, quote=quote)

    tz = ZoneInfo(data["timezone"])
    captured = dt.datetime.fromisoformat(data["as_of_utc"]).astimezone(tz)
    primary = data.get("price", {})
    candidates = []
    if (primary.get("kind") or "").startswith("regularMarketPrice"):
        candidates.append((primary.get("timestamp"), primary.get("value"), "Yahoo Finance", page))
    if isinstance(supplement, dict) and supplement.get("symbol") == symbol:
        source, url = supplement.get("source"), supplement.get("url")
        try:
            observed = dt.datetime.fromisoformat(supplement.get("at", ""))
            if observed.tzinfo is not None and safe_source(source, url):
                candidates.append((observed.timestamp(), supplement.get("value"), source, url))
        except (TypeError, ValueError, OverflowError):
            pass
    newest = None
    for stamp, value, source, url in candidates:
        if number(stamp, True) is None or number(value, True) is None or stamp > captured.timestamp():
            continue
        local = dt.datetime.fromtimestamp(stamp, tz)
        day = local.date()
        clock = local.time()
        if (day != captured.date() or day.weekday() >= 5 or not start <= day <= end
                or day.isoformat() <= last["date"] or clock < dt.time(9, 30) or clock >= dt.time(16)
                or (symbol == "^HSI" and dt.time(12) < clock < dt.time(13))):
            continue
        session_end = number(data["meta"].get("currentTradingPeriod", {}).get("regular", {}).get("end"), True)
        if session_end and dt.datetime.fromtimestamp(session_end, tz).date() == day and stamp >= session_end:
            continue
        if newest is None or stamp > newest:
            newest = stamp
            quote = dict(kind="intraday", at=local.isoformat(timespec="seconds"),
                         value=value, source=source, url=url)
    return dict(bars=candles, quote=quote)


def zone_labels(levels):
    result = []
    for group, role in ROLE.items():
        for i, row in enumerate(levels[group], 1):
            result.append(dict(kind=group, label=role + (f" {i}" if len(levels[group]) > 1 else ""),
                               value=(row["zone_low"] + row["zone_high"]) / 2, bounds=band(row), row=row))
    return result


def tick_values(low, high):
    raw = (high - low) / 4
    scale = 10 ** math.floor(math.log10(raw))
    step = next(n * scale for n in (1, 2, 2.5, 5, 10) if n * scale >= raw)
    first, last = math.ceil(low / step), math.floor(high / step)
    return [i * step for i in range(first, last + 1)]


def spread_labels(labels, height):
    """Keep the leader's origin at the true price when neighbouring labels need space."""
    ordered = sorted(labels, key=lambda v: v["y"])
    last = -12
    for tag in ordered:
        tag["label_y"] = max(tag["y"], 12, last + 24)
        last = tag["label_y"]
    last = height + 12
    for tag in reversed(ordered):
        tag["label_y"] = min(tag["label_y"], last - 24)
        last = tag["label_y"]


def trading_plot(candles, quote, zones):
    width = 600
    height = max(255, 26 * (len(zones) + 1) + 24)
    dates = [b["date"] for b in candles]
    intraday = quote["kind"] == "intraday"
    n = len(dates) + int(intraday)
    spacing = width / (n + 1)
    def x(i):
        return (i + 1) * spacing

    low = min([quote["value"]] + [b["low"] for b in candles] + [z["row"]["zone_low"] for z in zones])
    high = max([quote["value"]] + [b["high"] for b in candles] + [z["row"]["zone_high"] for z in zones])
    pad = max((high-low) * .09, abs(high) * .001, .01)
    low, high = low - pad, high + pad
    def y(val):
        return height * (high - val) / (high - low)

    axis, shapes, leaders, tags = [], [], [], []
    for value in tick_values(low, high):
        py = y(value)
        label = f"{value:,.0f}" if value == round(value) else f"{value:,.2f}".rstrip("0").rstrip(".")
        axis.append(f'<span style="top:{100*py/height:.4f}%">{label}</span>')
        shapes.append(f'<path class="gridline" d="M0 {py:.3f}H{width}"/>')

    labels = [dict(z, y=y(z["value"])) for z in zones]
    for z in labels:
        row = z["row"]
        since = max(row["recognized_at"], row.get("level_as_of") or row["recognized_at"])
        left = 0 if since < dates[0] else x(bisect_left(dates, since))
        upper, lower = y(row["zone_high"]), y(row["zone_low"])
        shapes.append(f'<g class="price-band {z["kind"]}" data-low="{row["zone_low"]}" '
                      f'data-high="{row["zone_high"]}" data-from="{esc(since)}">'
                      f'<title>{esc(z["label"])}　{esc(z["bounds"])} 點</title>'
                      f'<rect x="{left:.3f}" y="{upper:.3f}" width="{width-left:.3f}" height="{lower-upper:.3f}"/>'
                      f'<path d="M{left:.3f} {upper:.3f}H{width} M{left:.3f} {lower:.3f}H{width}"/></g>')
    for i, b in enumerate(candles):
        cx = x(i)
        half = spacing * .29
        op, hi, lo, cl = (y(b[k]) for k in ("open", "high", "low", "close"))
        direction = "rising" if b["close"] > b["open"] else "falling" if b["close"] < b["open"] else "flat"
        tip = (f'{b["date"]}　開 {price(b["open"])}　高 {price(b["high"])}　'
               f'低 {price(b["low"])}　收 {price(b["close"])}')
        body = (f'<rect class="candle-body" x="{cx-half:.3f}" y="{min(op,cl):.3f}" width="{2*half:.3f}" '
                f'height="{abs(op-cl):.3f}"/>' if op != cl else
                f'<path d="M{cx-half:.3f} {cl:.3f}h{2*half:.3f}"/>')
        shapes.append(f'<g class="candle {direction}" data-day="{b["date"]}"><title>{esc(tip)}</title>'
                      f'<path d="M{cx:.3f} {hi:.3f}V{lo:.3f}"/>{body}'
                      f'<rect class="candle-hit" x="{cx-spacing/2:.3f}" y="0" width="{spacing:.3f}" height="{height}"/></g>')

    quote_y, quote_x = y(quote["value"]), x(n-1)
    shapes.append(f'<path class="quote-line" d="M0 {quote_y:.3f}H{width}"/>')
    labels.append(dict(kind="position", label="盤中" if intraday else "收市", bounds=price(quote["value"]), y=quote_y))
    spread_labels(labels, height)
    for item in labels:
        src, dest, kind = item["y"], item["label_y"], item["kind"]
        leaders.append(f'<path class="{kind}" d="M0 {src:.3f}L10 {dest:.3f}H15"/>')
        tags.append(f'<span class="plot-tag {kind}" style="top:{dest/height*100:.4f}%">'
                    f'<span>{esc(item["label"])}</span> <bdi>{esc(item["bounds"])}</bdi></span>')

    ticks = []
    days = dates + ([quote["at"][:10]] if intraday else [])
    for idx in sorted({0, (n-1)//2, n-1}):
        d = dt.date.fromisoformat(days[idx])
        side = "first" if idx == 0 else "end" if idx == n-1 else "mid"
        ticks.append(f'<time class="{side}" style="left:{100*x(idx)/width:.3f}%" datetime="{d.isoformat()}">{d.month}/{d.day}</time>')
    return (f'<div class="chart-grid" style="--plot-height:{height}px"><div class="chart-axis" aria-hidden="true">'
            + ''.join(axis) + f'</div><div class="chart-field" tabindex="0" role="group" aria-label="逐日陰陽燭，可用左右方向鍵讀取走勢">'
            f'<svg class="candle-plot" role="img" aria-label="天線陰陽燭、已確認價區與報價位置" '
            f'viewBox="0 0 {width} {height}" preserveAspectRatio="none">' + ''.join(shapes) + '</svg>'
            f'<i aria-hidden="true" class="quote-diamond" style="top:{quote_y/height*100:.4f}%;left:{quote_x/width*100:.4f}%"></i></div>'
            f'<svg class="chart-leaders" aria-hidden="true" viewBox="0 0 15 {height}" preserveAspectRatio="none">'
            + ''.join(leaders) + '</svg><div class="chart-tags">' + ''.join(tags) +
            '</div><div class="chart-dates">' + ''.join(ticks) + '</div></div>')


def position_note(report, value):
    parts = []
    for group, noun in (("resistance", "阻力"), ("support", "支持")):
        rows = report["levels"][group]
        if not rows:
            parts.append(noun + ("區資料不足" if report["limited_history"] else "區暫未確認"))
            continue
        row = rows[0]
        low, high = row["zone_low"], row["zone_high"]
        if value < low:
            parts.append(f'低於最近{noun}區下緣 {price(low-value)} 點')
        elif value > high:
            parts.append(f'高於最近{noun}區上緣 {price(value-high)} 點')
        else:
            parts.append(f'位於最近{noun}區內')
    return "。".join(parts) + "。"


def weekly_chart(report):
    chart = report.get("chart")
    if not chart or not chart.get("bars"):
        return ""
    bars, quote = chart["bars"], chart["quote"]
    zones = zone_labels(report["levels"])
    intraday = quote["kind"] == "intraday"
    if intraday:
        stamp = dt.datetime.fromisoformat(quote["at"])
        zone = "香港時間" if report["timezone"] == "Asia/Hong_Kong" else "美東時間"
        label = f"{stamp.year} 年 {stamp.month} 月 {stamp.day} 日 {stamp:%H:%M}（{zone}）"
        kind = "盤中報價"
    else:
        stamp = dt.date.fromisoformat(quote["at"])
        label = f"{stamp.year} 年 {stamp.month} 月 {stamp.day} 日收市"
        kind = "收市位置"
    source = esc(quote["source"])
    if quote.get("url"):
        source = f'<a href="{esc(quote["url"])}">{source}</a>'
    caption = '每支燭代表一個已收市交易日。色帶從價區確認或更新後開始。' if zones else '每支燭代表一個已收市交易日。'
    if intraday:
        caption += "菱形標示當日盤中報價，該日天線尚未納入選位。"
    else:
        caption += "菱形標示最後一支燭的收市位置。"
    mobile = ''.join(f'<li><span>{esc(z["label"])}</span> <bdi>{esc(z["bounds"])}</bdi></li>' for z in zones)
    small_key = f'<ul class="chart-mobile-key" aria-label="圖中價區（點）">{mobile}</ul>' if mobile else ""
    start, last = (dt.date.fromisoformat(b["date"]) for b in (bars[0], bars[-1]))
    date_range = f"{start.year}/{start.month}/{start.day} 至 {last.year}/{last.month}/{last.day}"
    return (f'<figure class="week-chart" aria-label="{esc(report["name"])}天線圖"><figcaption class="chart-period">'
            f'最近 {len(bars)} 個交易日　{date_range}</figcaption>'
            f'<p class="chart-position"><span>{kind}</span> <bdi>{price(quote["value"])}</bdi> 點'
            f' <time datetime="{esc(quote["at"])}">{esc(label)}</time></p>'
            f'<p class="chart-compare">{esc(position_note(report, quote["value"]))}</p>'
            + trading_plot(bars, quote, zones) + small_key +
            '<div class="candle-key"><span><i class="up-key"></i>陽燭（收高於開）</span>'
            '<span><i class="down-key"></i>陰燭（收低於開）</span><span><i class="diamond-key"></i>指數位置</span></div>'
            f'<p class="chart-note">{caption}</p><p class="chart-source">{kind}來源　{source}</p>'
            '<output class="candle-readout" aria-live="polite"></output></figure>')
