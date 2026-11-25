"""Build the weekly watchlist from the same confirmed daily index patterns."""
import datetime as dt
from zoneinfo import ZoneInfo

from index_tracker import INDICES, MIN_BARS, Scanner, completed_index_history
from index_tracker_view import SIDE, band, esc, price, public_level
from index_week_chart import chart_data, weekly_chart

GROUPS = {"support": "下方支持區", "resistance": "上方阻力區", "inside": "收市所在的關鍵區"}
OBSERVATIONS = {"first_retest", "mirror_test", "ema_test", "swept", "pierced", "closed_through",
                "gap_across", "reclaimed", "sweep_reclaimed", "crossed_to_other_side",
                "held_from_above", "rejected_from_below"}


def weekly_role(row, close):
    side = row["side"]
    if side == "range" or row["status"] in ("swept", "closed_through", "gap_across"):
        return None
    low, high = row["zone_low"], row["zone_high"]
    if low <= close <= high:
        return "inside"
    if (side == "support" and close > high) or (side == "resistance" and close < low):
        return side
    return None


def weekly_profile(data, symbol, week_of=None, quote_snapshot=None):
    history = completed_index_history(data, symbol)
    local_day = dt.datetime.fromisoformat(data["as_of_utc"]).astimezone(ZoneInfo(data["timezone"])).date()
    requested = week_of or local_day
    start = requested - dt.timedelta(days=requested.weekday())
    end = start + dt.timedelta(days=4)
    bars = [b for b in history if b["date"] <= min(end, local_day).isoformat()]
    if not bars:
        raise ValueError("指定週或之前沒有可核對的已收市天線。")
    first = start.isoformat()
    prior_count = sum(b["date"] < first for b in bars)
    opening = {}
    scanner = Scanner(symbol, "1d")
    for i, bar in enumerate(bars, 1):
        scanner.append(dict(bar, start=bar["date"], end=bar["date"]))
        if i == prior_count:
            for row in scanner.live.values():
                if weekly_role(row, bar["close"]):
                    opening[row["id"], row["side"]] = (row["zone_low"], row["zone_high"])
    last = bars[-1]
    cutoff = dt.date.fromisoformat(last["date"])
    prior_date = bars[prior_count-1]["date"] if prior_count else None
    friday = start - dt.timedelta(days=3)
    provisional = local_day <= friday and (not prior_date or prior_date < friday.isoformat())
    levels = {key: [] for key in GROUPS}
    close = last["close"]
    for raw in scanner.live.values():
        group = weekly_role(raw, close)
        if group is None:
            continue
        row = public_level(raw)
        previous = opening.get((raw["id"], raw["side"]))
        reaction = next((e for e, r in zip(reversed(row["events"]), reversed(raw["events"]))
                         if r["status"] in OBSERVATIONS), None)
        observation = {k: reaction[k] for k in ("at", "status", "price")} if reaction else None
        levels[group].append(dict(id=row["id"], side=row["side"], zone_low=row["zone_low"],
                                  zone_high=row["zone_high"], recognized_at=row.get("breakout_at") or row["formed_at"],
                                  status=row["status"], last_observation=observation, evidence=row["evidence"],
                                  opening_zone=previous, level_as_of=row.get("level_as_of"),
                                  distance=max(row["zone_low"]-close, close-row["zone_high"], 0)))
    for rows in levels.values():
        rows.sort(key=lambda r: (r["distance"], r["zone_low"], r["id"]))
    effective = max(start, local_day, cutoff + dt.timedelta(days=1))
    return dict(symbol=symbol, name=INDICES[symbol], timezone=data["timezone"], week_start=first,
                week_end=end.isoformat(), effective_from=effective.isoformat() if effective <= end else None,
                data_through=last["date"], opening_cutoff=prior_date, opening_provisional=provisional,
                reference_price=close, timeframe="1d", levels=levels,
                chart=chart_data(data, symbol, bars, start, end, quote_snapshot),
                limited_history=len(bars) < max(MIN_BARS.values()),
                synthetic=bool(data["meta"].get("synthetic")), warnings=list(data.get("warnings", [])))


def dates(report):
    yield f"觀察週期　{report['week_start']} 至 {report['week_end']}（當地交易時段）"
    yield f"行情截至　{report['data_through']} 收市　{price(report['reference_price'])} 點"
    spot = report.get("chart", {}).get("quote", {})
    if spot.get("kind") == "intraday":
        stamp = dt.datetime.fromisoformat(spot["at"])
        market = "香港時間" if report["timezone"] == "Asia/Hong_Kong" else "美東時間"
        yield f"圖中盤中報價　{stamp:%Y-%m-%d %H:%M}（{market}）　{price(spot['value'])} 點　{spot['source']}"
    if report["opening_provisional"] and report["effective_from"]:
        yield "目前為提前預覽，目標週開始前仍可能出現新的天線資料。"
    elif report["effective_from"]:
        yield f"本次更新適用　{report['effective_from']} 至 {report['week_end']} 的開市時段"
    elif report["data_through"] < report["week_start"]:
        yield "行情未涵蓋目標週的收市，以下只反映上述資料日期。"
    else:
        yield "本週已結束，以下記錄截至該週最後可核對的收市。"
    if report["opening_cutoff"]:
        label = "提前預覽依據" if report["opening_provisional"] else "周初對照依據"
        yield label + "　" + report["opening_cutoff"] + " 收市"
    yield "選位周期　天線，每個交易日一支。價區按每次收市更新，本週到達時觀察有否反彈或回落。"
    if report["limited_history"]:
        yield "可用天線較少，部分形態的歷史不足。"


def level_notes(row, group, report):
    if group == "inside":
        yield "收市已在價區內，原有角色為" + SIDE[row["side"]] + "，留意其後從哪一邊離開。"
    else:
        meaning = "下跌至此時，觀察能否反彈。" if group == "support" else "反彈至此時，觀察會否回落。"
        gap = row["distance"]
        yield f"{meaning} 距離收市 {price(gap)} 點（{100*gap/report['reference_price']:.2f}%）。"
    previous = row["opening_zone"]
    if report["opening_provisional"]:
        timing = "提前預覽，周初仍待更新"
    elif previous:
        timing = "周初已可識別"
    elif report["opening_cutoff"]:
        timing = "本週新增或本週轉為目前角色"
    else:
        timing = "周初資料不足，無法對照"
    yield f"價區識別於 {row['recognized_at']}。{timing}。"
    if row["level_as_of"]:
        yield "價區按 " + row["level_as_of"] + " 收市資料更新。"
        if previous and tuple(previous) != (row["zone_low"], row["zone_high"]):
            yield "周初參考區為 " + band(dict(zone_low=previous[0], zone_high=previous[1])) + " 點。"
    if row["evidence"]:
        proof = "，".join(f"{v['bar']} {v['role']} {price(v['price'])}" for v in row["evidence"][-2:])
        yield "價格紀錄　" + proof + "。"
    last = row["last_observation"]
    if last:
        yield f"最近天線紀錄　{last['at']}　{last['status']}。"
    else:
        yield "形成後暫未錄得天線回試或穿位。"


def empty_text(group, report):
    if report["limited_history"]:
        return "可用天線不足以完整判定。"
    side = "支持區" if group == "support" else "阻力區"
    return "截至本次收市，暫未識別到這個方向仍然有效的天線" + side + "。"


def weekly_text(report):
    yield report["name"] + "　本週關鍵位"
    yield from dates(report)
    for group, name in GROUPS.items():
        rows = report["levels"][group]
        if not rows and group == "inside":
            continue
        yield name
        if not rows:
            yield empty_text(group, report)
        for row in rows:
            yield band(row) + " 點"
            yield from level_notes(row, group, report)


def day_label(stamp, year=None):
    day = dt.date.fromisoformat(stamp)
    prefix = f"{day.year} 年 " if year is None or day.year != year else ""
    return f"{prefix}{day.month} 月 {day.day} 日"


def period_label(start, end, year=None):
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first == last:
        return day_label(start, year)
    if (first.year, first.month) == (last.year, last.month):
        prefix = f"{first.year} 年 " if year is None or first.year != year else ""
        return f"{prefix}{first.month} 月 {first.day} 至 {last.day} 日"
    return f"{day_label(start, year)}至{day_label(end, first.year)}"


def public_date(stamp, year):
    return f'<time datetime="{esc(stamp)}">{esc(day_label(stamp, year))}</time>'


def watch_period(report, year):
    if report["opening_provisional"] and report["effective_from"]:
        return "提前預覽", "目標週開始前，仍可能有新的收市資料。"
    if report["effective_from"]:
        return "本版適用", period_label(report["effective_from"], report["week_end"], year)
    if report["data_through"] < report["week_start"]:
        return "資料未涵蓋本週", "只可參考所列日期的收市紀錄。"
    return "已完結的交易週", "本頁記錄該週最後可核對的收市。"


def nearest_status(row, year):
    event = row["last_observation"]
    if event:
        return public_date(event["at"], year) + " · " + esc(event["status"])
    return "形成後，未錄得回試或穿位。"


def observation_card(report, group, year):
    head = {"resistance": "上方阻力", "support": "下方支持", "inside": "收市所在價區"}[group]
    rows = report["levels"][group]
    if not rows:
        short = "資料不足" if report["limited_history"] else "暫未確認"
        note = ("可用的天線不足以判斷這一側的價區。" if report["limited_history"] else
                "收市位以下，暫未找到可用來觀察反彈的天線價區。" if group == "support" else
                "收市位以上，暫未找到可用來觀察回落的天線價區。")
        return (f'<section class="watch empty {group}"><h3>{head}</h3>'
                f'<p class="empty-value">{short}</p><p class="instruction">{note}</p></section>')
    row = rows[0]
    kind = ("最近" + head) if group != "inside" else head
    if group == "inside":
        gap = ""
        meaning = f"收市位已進入原有{SIDE[row['side']]}範圍，留意往後從哪一邊離開。"
    else:
        support = group == "support"
        move = "跌" if support else "升"
        pct = 100 * row["distance"] / report["reference_price"]
        gap = (f'<p class="distance">從收市位{move} <strong>{price(row["distance"])}</strong> 點'
               f'<span>（{pct:.2f}%）</span>便觸及。</p>')
        meaning = "到達這個範圍，留意能否反彈。" if support else "到達這個範圍，留意會否回落。"
    return (f'<section class="watch active {group}"><h3>{kind}</h3>'
            f'<p class="zone"><bdi>{band(row)}</bdi><span class="unit">點</span></p>'
            f'{gap}<p class="instruction">{esc(meaning)}</p>'
            f'<p class="last"><span>最近紀錄</span> {nearest_status(row, year)}</p></section>')


def evidence_list(row, year):
    items = row["evidence"][-2:]
    if not items:
        return ""
    parts = []
    for item in items:
        parts.append(f'<li>{public_date(item["bar"], year)}<span>{esc(item["role"])}'
                     f' <bdi>{price(item["price"])}</bdi></span></li>')
    return '<ul class="evidence-list">' + ''.join(parts) + '</ul>'


def recognition_note(row, report, year):
    if report["opening_provisional"]:
        availability = "周初仍待更新"
    elif row["opening_zone"]:
        availability = "周初已可識別"
    elif report["opening_cutoff"]:
        availability = "本週新增或於本週轉為目前角色"
    else:
        availability = "周初紀錄不足，未能對照"
    note = f'{public_date(row["recognized_at"], year)} 確認，{availability}。'
    if row["level_as_of"]:
        note += " 價區按 " + public_date(row["level_as_of"], year) + " 收市資料更新。"
        opening = row["opening_zone"]
        if opening and tuple(opening) != (row["zone_low"], row["zone_high"]):
            old_zone = band(dict(zone_low=opening[0], zone_high=opening[1]))
            note += f' 周初價區為 {old_zone} 點。'
    return note


def proof(row, group, report, year):
    name = {"resistance": "最近阻力", "support": "最近支持", "inside": "收市所在價區"}[group]
    items = row["evidence"][-2:]
    lead = ""
    if (len(items) == 2 and items[0]["bar"] != items[1]["bar"]
            and items[0]["role"] == items[1]["role"] and items[0]["role"] in ("最高價", "最低價")):
        noun = "高位" if items[0]["role"] == "最高價" else "低位"
        difference = abs(items[0]["price"] - items[1]["price"])
        lead = f'<p class="proof-line">兩個交易日的{noun}相差 <b>{price(difference)} 點</b>。</p>'
    return (f'<section class="proof"><h3>{name}怎樣得出？</h3>{lead}'
            f'{evidence_list(row, year)}<p class="origin">{recognition_note(row, report, year)}</p></section>')


def further_levels(report, year):
    extra = [(group, row) for group in ("inside", "resistance", "support")
             for row in report["levels"][group][1:]]
    if not extra:
        return ""
    extra.sort(key=lambda item: item[1]["distance"])
    groups = set(group for group, _ in extra)
    if groups == {"resistance"}:
        name = "較遠的上方阻力"
    elif groups == {"support"}:
        name = "較遠的下方支持"
    else:
        name = "其餘已確認的價區"
    rows = []
    for group, row in extra:
        role = GROUPS[group]
        gap = row["distance"]
        distance = (f'距離收市 {price(gap)} 點（{100*gap/report["reference_price"]:.2f}%）' if
                    group != "inside" else "收市位在區內")
        rows.append(f'<section class="extra-row"><h4><span>{role}</span><bdi>{band(row)}</bdi> 點</h4>'
                    f'<p>{distance}</p><p class="extra-last">最近紀錄　{nearest_status(row, year)}</p>'
                    f'{evidence_list(row, year)}<p class="origin">{recognition_note(row, report, year)}</p></section>')
    return (f'<details class="more"><summary>{name}<span class="count">{len(extra)} 個</span></summary>'
            '<div class="more-body">' + ''.join(rows) + '</div></details>')


def weekly_article(report, multiple, brand=""):
    name = esc(report["name"])
    code = esc(report["symbol"].lstrip("^"))
    title_tag = "h2" if multiple else "h1"
    article_id = "market-" + code.lower()
    title = f'<{title_tag} class="market-title">{name}本週關鍵位</{title_tag}>'
    if report.get("error"):
        return (f'<article id="{article_id}"><header class="mast">{title}{brand}</header><p class="notice">行情無法核對　'
                f'{esc(report["error"])}</p></article>')
    year = dt.date.fromisoformat(report["week_start"]).year
    week = period_label(report["week_start"], report["week_end"])
    market = {"Asia/Hong_Kong": "香港時間", "America/New_York": "美東時間"}.get(report["timezone"], report["timezone"])
    status, effective = watch_period(report, year)
    if status == "本版適用" and market.endswith("時間"):
        market = market.removesuffix("時間") + "交易時段"
    as_of = public_date(report["data_through"], year)
    quote_label = "收市（合成測試行情）" if report["synthetic"] else "收市快照" if report.get("offline") else "收市"
    head_date, head_value, basis = as_of, report["reference_price"], ""
    spot = report.get("chart", {}).get("quote", {})
    if spot.get("kind") == "intraday":
        stamp = dt.datetime.fromisoformat(spot["at"])
        head_date = f'<time datetime="{esc(spot["at"])}">{esc(day_label(stamp.date().isoformat(), year))} {stamp:%H:%M}</time>'
        quote_label, head_value = " 盤中報價", spot["value"]
        basis = f'<span class="chart-basis">選位依據　{as_of}收市 <bdi>{price(report["reference_price"])}</bdi> 點</span>'
    quote = (f'<dl class="close"><dt>{head_date}{quote_label}</dt><dd><bdi>{price(head_value)}</bdi>'
             '<span class="unit">點</span></dd></dl>')
    chart = weekly_chart(report)
    style = ' class="charted"' if chart else ""
    out = [f'<article id="{article_id}"{style}><header class="mast"><div class="mast-heading"><p class="market-type">現貨指數'
           f'<span aria-hidden="true"> / </span><b>{code}</b></p>{title}'
           f'<p class="week"><span>觀察週</span>{week}</p></div><div class="mast-meta">{brand}{quote}</div></header>'
           f'<p class="effective"><b>{status}</b> {esc(effective)}<span class="local">（{esc(market)}）</span>{basis}</p>']
    if report["limited_history"]:
        out.append('<p class="notice">可用天線較少，部分價區的歷史不足。</p>')
    for warning in report.get("warnings", []):
        if not warning.startswith("離線重播：") and not (chart and warning == "今日尚未收市，股價圖以淡色顯示當日走勢，計算時不計入。"):
            out.append(f'<p class="notice">{esc(warning)}</p>')
    out.append(chart)
    levels = report["levels"]
    directions = sorted(("resistance", "support"),
                        key=lambda g: levels[g][0]["distance"] if levels[g] else float("inf"))
    if levels["inside"]:
        out.append(observation_card(report, "inside", year))
    both = all(bool(levels[g]) for g in directions)
    neither = not any(levels[g] for g in directions)
    layout = "even" if both or neither else "one"
    out.append(f'<div class="watches {layout}">' +
               ''.join(observation_card(report, g, year) for g in directions) + '</div>')
    if chart:
        out.append(f'<header class="chart-continuation"><h2>{name}｜價區紀錄</h2><p>{week}</p></header>')
    for group in (["inside"] if levels["inside"] else []) + directions:
        if levels[group]:
            out.append(proof(levels[group][0], group, report, year))
    out.append(further_levels(report, year))
    source = ("合成測試行情" if report["synthetic"] else
              f'<a href="https://finance.yahoo.com/quote/%5E{code}/history/">Yahoo Finance 現貨指數天線</a>')
    if report.get("offline") and not report["synthetic"]:
        source += "，按已保存的收市行情整理"
    prior = ""
    if report["opening_cutoff"]:
        label = "提前預覽依據" if report["opening_provisional"] else "周初依據"
        prior = f'<p>{label}　{public_date(report["opening_cutoff"], year)}收市</p>'
    out.append('<details class="method"><summary>資料日期與選位方法</summary><div>'
               f'{prior}<p>選位周期　天線，每支代表一個交易日。價區按每次收市更新。</p>'
               f'<p>行情來源　{source}</p></div></details></article>')
    return ''.join(out)


def weekly_dashboard(reports):
    from pathlib import Path

    from key_levels import AI_NOTICE, DISCLAIMER_FULL, logo_data_uri

    multi = len(reports) != 1
    title = "現貨指數本週關鍵位" if multi else reports[0]["name"] + "本週關鍵位"
    assets = Path(__file__).with_name("assets")
    css = (assets / "week.css").read_text(encoding="utf-8")
    chart_script = (assets / "week-chart.js").read_text(encoding="utf-8")
    navigation = ""
    if multi:
        links = ''.join(f'<a href="#market-{esc(r["symbol"].lstrip("^").lower())}">{esc(r["name"])}</a>'
                        for r in reports)
        navigation = f'<h1 class="all-title">{title}</h1><nav aria-label="選擇指數">{links}</nav>'
    logo = logo_data_uri("hkiei-week-wordmark.png")
    brand = (f'<img class="brand-logo" src="{logo}" width="3570" height="1221" alt="投創教育 HKIEI">'
             if logo else "")
    articles = ''.join(weekly_article(r, multi, brand if i == 0 else "") for i, r in enumerate(reports))
    notices = (f'<footer><details class="method"><summary>資料聲明</summary>'
               f'<div><p>{esc(AI_NOTICE)}</p><p>{esc(DISCLAIMER_FULL)}</p></div></details></footer>')
    print_controls = """<script>
let closedForPrint = [];
window.addEventListener("beforeprint", () => {
  if (closedForPrint.length) return;
  closedForPrint = Array.from(document.querySelectorAll("details:not([open])"));
  closedForPrint.forEach(item => { item.open = true; });
});
window.addEventListener("afterprint", () => {
  closedForPrint.forEach(item => { item.open = false; });
  closedForPrint = [];
});
</script>"""
    return (f'<!doctype html><html lang="zh-HK"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{css}</style>'
            f'</head><body><main>{navigation}{articles}{notices}</main>{print_controls}<script>{chart_script}</script></body></html>')
