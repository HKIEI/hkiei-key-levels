"""Self-contained HTML presentation for Key Levels (standard library only).

Receives computed levels from key_levels.py. No prices are fetched here and no
level selection, score, reaction count or backtest is calculated by this module.
"""
import base64
import html
import json
from pathlib import Path


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return "—" if value is None else "{:,.2f}".format(value)


def pct(value):
    return "—" if value is None else "%+.2f%%" % value


def band(zone):
    if zone["low"] == zone["high"]:
        return fmt(zone["low"])
    return fmt(zone["low"]) + "–" + fmt(zone["high"])


def list_html(items):
    return "<ul>" + "".join("<li>" + esc(item) + "</li>" for item in items) + "</ul>"


def safe_json(value):
    # A JSON script element is still parsed as HTML: neutralize closing tags.
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def hsi_section(profile, table):
    from hsi_reactions import metric_rows
    from reaction_watch import plan_rows
    columns = ("恒指選位方法", "先順向／可評觸及", "先逆向", "兩邊未到", "同燭不明", "最大順向幅度中位數")
    forward = profile["after_rule"]
    future = (table(columns, metric_rows(forward)) if forward["days"]
              else '<p>期內尚無定稿後已收市的交易日。</p>')
    plans = []
    for heading, entries in plan_rows(profile):
        items = ["<li><b>" + esc(label) + "</b> · " + esc(status) + "</li>" for label, status in entries]
        plans.append("<h4>" + esc(heading) + "</h4><ul>" + "".join(items) + "</ul>")
    return ('<h3>恒指專項 · 首次觸及後的方向與點數</h3>'
            '<p>下列保留舊版天線價區作對照。另用上日恒指的最高、最低及收市點數計算樞軸，作獨立對照。'
            '兩者均以上日半個 ATR 為目標，由首觸後下一支五分鐘燭的開市點數起計一小時。</p>'
            + table(columns, metric_rows(profile["stats"]))
            + '<p class="small muted">最大順向幅度量度一小時內最遠的反彈或回落。即使先逆向，仍記錄其後順向幅度。'
              '點數及上日 ATR 倍數各自取中位數。</p>'
            + '<details><summary>恒指支持與阻力的表現</summary>' + table(columns, metric_rows(profile["stats"], split=True))
            + '</details><details><summary>核對上日樞軸的區間及開市選擇</summary>'
            + (''.join(plans) or '<p>事前行情不足，暫無可核對的位置。</p>')
            + '</details><details><summary>只看恒指規則定稿後的新交易日</summary><p>'
            + esc(profile["rules_fixed_on"]) + ' 定稿。本次區間共有 %d 個其後已收市交易日。' % forward["days"]
            + '按較早行情逐日重建。</p>' + future + '</details>')


def reaction_section(watch):
    from reaction_watch import GROUPS, REGIMES, LAYERS, daily_row, hour_row, plan_rows, ratio
    from intraday_reactions import LABELS
    if not watch:
        return ""
    plans = []
    for heading, entries in plan_rows(watch):
        content = "".join("<li><strong>" + esc(label) + "</strong> · " + esc(status) + "</li>"
                          for label, status in entries)
        plans.append('<div class="reference-card"><h3>' + esc(heading) + "</h3><ul>" + content + "</ul></div>")
    if not plans:
        plans.append('<p>可用事前行情少於 250 個交易日，暫無可核對的回試位置。</p>')

    def table(headers, rows):
        titles = "".join('<th scope="col">' + esc(h) + "</th>" for h in headers)
        cells = "".join('<tr><th scope="row">' + esc(row[0]) + "</th>" +
                        "".join("<td>" + esc(v) + "</td>" for v in row[1:]) + "</tr>" for row in rows)
        return ('<div class="table-scroll" tabindex="0" aria-label="歷史回試成績，窄螢幕可橫向捲動">'
                '<table><thead><tr>' + titles + "</tr></thead><tbody>" + cells + "</tbody></table></div>")

    def results(c):
        return (c["touches"], ratio(c["returned"], c["touches"]), ratio(c["moved_half"], c["atr_touches"]))

    pattern_html = ""
    if "hsi_patterns" in watch:
        from hsi_pattern_view import html_section
        pattern_html = html_section(watch["hsi_patterns"], table)
    groups = watch["groups"]
    total = groups["all"]["both"]["touches"]
    overall, split = [], []
    for key, label in GROUPS:
        count = groups[key]["both"]
        overall.append((label, *results(count), ratio(count["touches"], total)))
        for side, direction in (("support", "支持反彈"), ("resistance", "阻力回落")):
            split.append((label, direction, *results(groups[key][side])))
    regimes = [(label, *results(watch["regimes"][key])) for key, label in REGIMES]
    metrics = ("實際觸及", "收回原側／觸及", "收市拉開半個 ATR／可評觸及")
    hourly = watch.get("hourly")
    main = ""
    if hourly:
        h_cols = ("開市選位", "可評觸及", "先順向／可評觸及", "先逆向", "兩邊未到", "同燭先後不明")
        d_cols = ("開市選位", "選用次數", *metrics, "日內穿越遠端", "收市仍在遠端外")
        def hour_table(scores):
            return table(h_cols, [hour_row(label, scores[key]["both"]) for key, label in LAYERS])
        directions = (("support", "支持"), ("resistance", "阻力"))
        excluded = "、".join("%s %d 次" % (LABELS[k], n) for k, n in hourly["excluded"].items()) or "沒有"
        forward = hourly["after_rule"]
        future_content = (hour_table(forward["scores"]) if forward["days"]
                          else "<p>期內尚無定稿後已收市的交易日。</p>")
        main = ('<h3>首次觸及後一小時 · 哪個方向先行半個 ATR</h3>'
                '<p>由觸及後下一支五分鐘燭的開市價起計。支持向上、阻力向下為順向。'
                '連續觀察十二支完整五分鐘燭。兩邊未到或同燭先後不明均計入分母。</p>'
                + hour_table(hourly["scores"])
                + '<p class="small muted">可重建的 %d 日之中，%d 日有完整五分鐘行情。未列入一小時分母的情況按日及方向計算，包括%s。</p>'
                % (hourly["requested_sessions"], hourly["full_sessions"], esc(excluded))
                + '<details><summary>按支持、阻力及事前波幅查看一小時結果</summary>'
                + table(h_cols, [hour_row(label+" · "+direction, hourly["scores"][key][side])
                                  for key, label in LAYERS for side, direction in directions])
                + table(("事前波幅（兩層合計）", *h_cols[1:]), [
                    hour_row(label, hourly["by_regime"][key]["scores"]["combined"]["both"]) for key, label in REGIMES])
                + '<p>較高波幅指上日 ATR 佔收市價的百分比高於此前 60 個交易日的中位數。此分組可比較差異，單次穿位的原因仍無法確定。</p></details>'
                + '<h3>同一批開市位置 · 全日觸及與收市結果</h3>'
                + table(d_cols, [daily_row(label, watch["layer_scores"][key]["both"]) for key, label in LAYERS])
                + '<details><summary>查看支持跌穿、阻力升穿及不同波幅的穿位次數</summary>'
                + table(d_cols, [daily_row(label+" · "+direction, watch["layer_scores"][key][side])
                                 for key, label in LAYERS for side, direction in directions])
                + table(("事前波幅（兩層合計）", *d_cols[1:]), [
                    daily_row(label, watch["selected_by_regime"][key]["both"]) for key, label in REGIMES])
                + '<p>跌穿支持採用全日最低價低於區底，升穿阻力採用全日最高價高於區頂。剛好到邊界只計觸及。</p></details>'
                + '<details><summary>規則定稿後的新交易日</summary><p>定稿日為 ' + esc(watch["rules_fixed_on"])
                + '。本次區間有 %d 個交易日在定稿日之後，只按當時已知的較早行情逐日重建。</p>' % forward["days"]
                + future_content + '</details>')
    introduction = ('上日收市固定兩層支持及阻力。每邊先看第一層，只有開市已不合資格才啟用合資格的後備第二層。'
                    '全日沿用該邊的開市選擇。' if hourly else '上日收市固定最近支持 1 及阻力 1。')
    return (
        pattern_html + '<section aria-labelledby="reaction-title"><h2 id="reaction-title">'
        + ('舊版天線位置與樞軸對照' if pattern_html else '支持反彈與阻力回落') + '</h2>'
        '<p>' + introduction + '支持從區頂以上回試，阻力從區底以下回試。'
        '¼ ATR 是額外觀察條件，以劃位時的 14 日平均真實波幅計算。</p>'
        '<div class="cols">' + "".join(plans) + '</div><h3>最近 %d 個已收市交易日</h3>' % watch["requested_days"]
        + '<p class="small muted">%s 至 %s。取得 %d 日，可逐日重建 %d 日。</p>'
        % (esc(watch["start"]), esc(watch["end"]), watch["available_days"], watch["scored_days"])
        + (hsi_section(watch["hsi_comparison"], table) if watch.get("hsi_comparison") else "")
        + main + '<details><summary>第一層歷史對照 · 開市條件與收市反應</summary>'
        + '<p>第一層全日跳過整區 %d 次，另列於觸及分母之外。</p>' % groups["all"]["both"]["gap_skips"]
        + table(("開市條件", *metrics, "保留觸及／全部觸及"), overall)
        + table(("開市條件", "方向", *metrics), split)
        + '<h3>由區外回試 · 按事前波幅</h3>'
        + table(("劃位時的波幅", *metrics), regimes)
        + '<p class="small muted">較高波幅指上日 ATR 佔收市價的百分比高於此前 60 個交易日的中位數。兩組均計入總成績。</p>'
        + '<p class="stat-note">實際觸及後，支持要收在區頂以上，阻力要收在區底以下，才計作收回原側。'
          '第二項另要求收市順向距離達半個上日 ATR。每日每邊最多一次。'
          '這張第一層對照表的歷史行情重建只衡量收市反應。表內比例屬歷史結果。</p></details></section>'
    )


def render_report(r, bars, days, copy):
    assets = Path(__file__).with_name("assets")
    css = (assets / "report.css").read_text(encoding="utf-8")
    script = (assets / "report.js").read_text(encoding="utf-8")
    logo = assets / "hkiei-logo.png"
    brand_image = ('<img alt="投創教育 HKIEI" width="124" height="32" src="data:image/png;base64,'
                   + base64.b64encode(logo.read_bytes()).decode("ascii") + '">') if logo.exists() else '<b>投創教育 HKIEI</b>'
    price = r["reference_price"]
    watch = r.get("reaction_watch")
    reaction_html = reaction_section(watch)
    supports, resistances, at = r["nearest_supports"], r["nearest_resistances"], r["at_price"]
    unit = copy["unit"]
    quote_time = price.get("time_display", price["time"])
    source = r.get("data_source", {})
    mode = "日線觀察報告"
    banner = ""
    if source.get("synthetic"):
        mode = "合成數據測試"
        banner = '<div class="status caution" role="note"><b>合成測試數據</b> · 非真實行情，不可作市況分析。</div>'
    elif source.get("input_file"):
        mode = "歷史快照報告"
        banner = '<div class="status caution" role="note"><b>離線歷史快照</b> · 此報告使用已保存的行情，不代表最新市況。</div>'
    quote_note = ("Yahoo 一般交易時段報價；可能有延遲，不包括盤前及盤後交易。"
                  if "regularMarketPrice" in price["kind"] else "採用最新交易日收市價，並非即時價。")

    def tile(label, value, note, cls=""):
        value_html = esc(value)
        if cls in ("support", "resistance") and "–" in value:
            low, high = value.split("–", 1)
            value_html = '<span>' + esc(low) + '</span><span class="range-end">–' + esc(high) + '</span>'
        return (f'<div class="tile {esc(cls)}"><div class="tile-label">{esc(label)}</div>'
                f'<div class="tile-value">{value_html}</div><div class="tile-note">{esc(note)}</div></div>')

    tiles = tile("參考價格 · " + unit, fmt(price["value"]), "較上日收市 " + pct(r["day_change_pct"]), "price")
    patterns = (watch or {}).get("hsi_patterns")
    if patterns:
        from hsi_pattern_view import chart_zones
        projected = chart_zones(patterns,price["value"])
        for side, title in (("resistance","追蹤阻力位"),("support","追蹤支持位")):
            z = next((z for z in projected if z["side"]==side),None)
            tiles += tile(title, band(z) if z else "觀望", z["pattern_name"] if z else "暫無合資格價位",side)
    else:
        for label, zones, cls in (("最近阻力 · R1", resistances, "resistance"), ("最近支持 · S1", supports, "support")):
            tiles += tile(label, band(zones[0]) if zones else "暫無合資格位置",
                          "區間中心距離現價 " + pct(zones[0]["distance_pct"]) if zones else "按實際數據列出", cls)
    tiles += tile("平均日波幅 · ATR 14", fmt(r["atr"]["value"]) + " " + unit,
                  "佔參考價格 %.2f%%" % r["atr"]["pct"], "atr")
    context = list(copy["context"])
    if at:
        context.insert(0, "現價位於 " + "、".join(band(z) for z in at)
                       + " 觀察區間內；不列作現價上方阻力或下方支持。")
    context_html = '<div class="event">' + list_html(context) + '</div>' if context else ""

    def zone_table(zones, heading, cls):
        rows = []
        for z in zones:
            name = copy["names"][z["id"]]
            star = ' <span class="star" title="表內統計排名最高">★</span>' if z["id"] == copy["strongest_id"] else ""
            rows.append(f'<tr><th scope="row">{esc(name)}{star}</th><td class="number">{esc(band(z))}</td>'
                        f'<td class="number">{pct(z["distance_pct"])}</td><td>{z["reactions"]} 次</td>'
                        f'<td>{z["test_episodes"]} 次</td><td class="source-cell">{esc(copy["sources"][z["id"]])}</td></tr>')
        missing = f'<p class="small muted">只找到 {len(zones)} 個合資格位置，按實際列出。</p>' if len(zones) < 3 else ""
        return (f'<div class="level-table {cls}"><h3>{heading}</h3><div class="table-scroll" tabindex="0" aria-label="{heading}，窄螢幕可橫向捲動">'
                '<table><thead><tr><th scope="col">位置</th><th scope="col">價位區間</th><th scope="col">距離現價</th>'
                '<th scope="col">過往反應</th><th scope="col">過往觸及</th><th scope="col">形成原因</th></tr></thead><tbody>'
                + ("".join(rows) or '<tr><td colspan="6">附近沒有合資格位置。</td></tr>')
                + '</tbody></table></div>' + missing + '</div>')

    tables = zone_table(resistances, "上方阻力位", "resistance") + zone_table(supports, "下方支持位", "support")
    uses_volume = any(z["score_components"]["volume_normalized"] is not None for z in r["zones_by_strength"])
    top = next((z for z in resistances + supports if z["id"] == copy["strongest_id"]), None)
    star_note = ""
    if top:
        metric = "反應次數及成交量綜合最高的區間" if uses_volume else "反應次數最多的區間"
        star_note = '<p class="star-note">★ 表內' + metric + '：<b>' + esc(band(top)) + '</b>。這是相對排名，並非守穩機率。</p>'
    at_detail = ""
    if at:
        at_detail = '<div class="event"><b>現價所處區間</b>' + list_html(
            [band(z) + "：" + copy["sources"][z["id"]] for z in at]) + '</div>'

    reference_groups = []
    group = []
    for line in copy["references"]:
        if line.endswith("："):
            if group:
                reference_groups.append(group)
            group = [line.rstrip("：")]
        else:
            group.append(line.removeprefix("- "))
    if group:
        reference_groups.append(group)
    references = ''.join('<div class="reference-card"><h3>' + esc(g[0]) + '</h3>' + list_html(g[1:]) + '</div>' for g in reference_groups)
    glossary = [g.replace("120 個交易日", str(r["method"]["lookback_sessions"]) + " 個交易日") for g in copy["glossary"]]
    warnings = list(dict.fromkeys(r["warnings"] + r["interpretation"]["limitations"]))
    ranking = ''.join('<tr><th scope="row">' + esc(copy["names"][z["id"]]) + '</th><td>' + esc(z["id"])
                      + '</td><td>' + fmt(z["statistical_score"]) + '</td></tr>' for z in resistances + supports)
    advanced = (f'<dl class="meta-grid"><dt>計算版本</dt><dd>Key Levels {esc(r["schema_version"])}</dd>'
                f'<dt>資料來源</dt><dd>{esc(source.get("provider", "Yahoo Finance chart API"))}</dd>'
                f'<dt>市場時區</dt><dd>{esc(r["timezone"])}</dd>'
                f'<dt>報告計算時間（UTC）</dt><dd>{esc(r["as_of_utc"])}</dd>'
                f'<dt>已完成日線</dt><dd>{r["history"]["sessions"]} 個交易日 · {esc(r["history"]["start"])} 至 {esc(r["history"]["end"])}</dd>'
                f'<dt>統計回看期</dt><dd>{r["method"]["lookback_sessions"]} 個交易日</dd>'
                f'<dt>合併跨度</dt><dd>{fmt(r["method"]["merge_tolerance"])} {esc(unit)}</dd>'
                f'<dt>同向位置最小間距</dt><dd>{fmt(r["method"]["level_spacing"])} {esc(unit)}</dd></dl>'
                '<p class="small">現價表的統計評分只用作排序。回試成績須按當日開市前已能計算的位置逐日核對。</p>'
                '<div class="table-scroll"><table class="ranking"><thead><tr><th>位置</th><th>內部編號</th><th>統計評分</th></tr></thead><tbody>'
                + ranking + '</tbody></table></div>')

    marked = supports + resistances + at
    chart_data = {
        "bars": [{k: b[k] for k in ("date", "open", "high", "low", "close", "complete")} for b in bars[-200:]],
        "zones": [dict(low=z["low"], high=z["high"], center=z["center"], side=z["side"],
                       label=copy["names"][z["id"]], short=("S" + str(supports.index(z) + 1) if z in supports else
                       "R" + str(resistances.index(z) + 1) if z in resistances else "現價區間")) for z in marked],
        "price": price["value"], "days": days, "unit": unit,
    }
    if patterns:
        chart_data["zones"] = projected
    level_heading = "一般天線觀察區（舊版對照）" if patterns else "支持位及阻力位一覽"
    tracker_html = ""
    if r.get("index_tracker"):
        from index_tracker_view import section
        tracker_html = section(r["index_tracker"])
    page_title = r["symbol"] + " 支持位與阻力位｜投創教育"
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{esc(page_title)}</title><style>{css}</style></head><body>
<header><div class="wrap"><div class="brandrow">{brand_image}<span>KEY LEVELS <span class="brand-divider">/</span> {esc(mode)}</span><button id="print" class="print-button" type="button">列印 / 儲存 PDF</button></div>
<div class="eyebrow">SUPPORT &amp; RESISTANCE</div><h1>{esc(r["symbol"])} <span>支持位與阻力位</span></h1>
<p class="instrument">{esc(r["instrument_name"])}</p><div class="header-meta"><span>報價時間 · {esc(quote_time)}</span><span>{esc(r["timezone"])}</span><span>A.I. 計算及生成</span></div></div></header>
<main class="wrap">{banner}{tracker_html}
<section aria-labelledby="summary-title"><div class="section-heading"><span class="section-number">01</span><h2 id="summary-title">價位摘要</h2><span class="section-caption">日線 · {esc(unit)}</span></div>
<div class="tiles">{tiles}</div><p class="small muted">{esc(quote_note)}</p>{context_html}</section>
{reaction_html}
<section aria-labelledby="chart-title"><div class="section-heading"><span class="section-number">02</span><h2 id="chart-title">股價與關鍵區間</h2></div>
<div class="chart-toolbar"><div id="periods" class="periods" role="group" aria-label="圖表顯示交易日數"></div><div class="layer-controls">
<label class="support"><input type="checkbox" data-layer="support" checked>支持位</label><label class="resistance"><input type="checkbox" data-layer="resistance" checked>阻力位</label>
<label class="at-price"><input type="checkbox" data-layer="at_price" checked>現價區間</label></div></div>
<div class="chart-card"><div id="readout" class="readout" aria-live="polite"></div><div id="chart" role="group" aria-label="互動股價圖"></div>
<noscript><p>互動股價圖需要啟用 JavaScript。支持阻力數值仍可在下方表格閱讀。</p></noscript></div>
<div class="chart-foot"><span><i class="legend-dot up"></i>收市不低於開市 <i class="legend-dot down"></i>收市低於開市</span><span>移動游標、點按或使用左右方向鍵查看每日價格</span></div>
<p class="small muted">色帶是本次計算的關鍵區間，僅供對照歷史走勢；並非當時已發出的價位。未收市日線以淡色及「未收市」標示，未計入統計。</p></section>
<section aria-labelledby="levels-title"><div class="section-heading"><span class="section-number">03</span><h2 id="levels-title">{level_heading}</h2></div>
<p class="section-intro">各方向均由近至遠排列；距離現價按區間中心計算。</p><p class="mobile-hint">左右滑動表格，查看次數及形成原因。</p>{tables}{star_note}{at_detail}
<div class="stat-note"><b>如何閱讀次數？</b> 現價表按本次價位回看歷史，連續多日觸及只計一次。支持反彈與阻力回落的逐日成績使用上日收市固定的位置。</div></section>
<section aria-labelledby="references-title"><div class="section-heading"><span class="section-number">04</span><h2 id="references-title">其他參考線</h2></div><div class="cols">{references}</div></section>
<section aria-labelledby="reading-title"><div class="section-heading"><span class="section-number">05</span><h2 id="reading-title">閱讀說明</h2></div><h3>使用提醒</h3>{list_html(copy["rules"])}
<details><summary>名詞解釋</summary>{list_html(glossary)}</details>
<div class="limitations"><h3>資料限制</h3>{list_html(warnings)}</div>
<details><summary>進階資料與計算設定</summary>{advanced}</details></section>
<footer><div class="ai-notice"><b>A.I. 生成聲明</b><p>{esc(copy["ai_notice"])}</p></div><div class="disclaimer">{esc(copy["disclaimer"])}</div>
<div class="footer-line"><span>投創教育 HKIEI · KEY LEVELS {esc(r["schema_version"])}</span><span>單一 HTML 檔 · 可離線閱讀</span></div></footer>
</main><script id="chart-data" type="application/json">{safe_json(chart_data)}</script><script>{script}</script></body></html>'''
