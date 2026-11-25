"""Present observed index prices by market, timeframe and support/resistance role."""
import hashlib
import html

SIDE = {"support": "支持位", "resistance": "阻力位", "range": "待確認區間"}
STATE = {"none": "暫無有效價位", "insufficient": "行情不足，暫無法判定",
         "partial": "暫無有效價位，部分所需行情不足", "tracking": "追蹤中"}
QUALITY = {"ok": "已核對收燭", "partial": "最新交易日只納入已完成燭", "missing": "未取得可用行情",
           "gap": "最新時段有缺口", "stale": "五分鐘行情未涵蓋最新交易日"}
STATUS = {
    "platform": "整固中，等候收市突破", "breakout": "已收市突破，等候首次回試",
    "watch_sweep": "曾多次觸及，留意有否穿位", "first_retest": "已首次回試",
    "await_retest": "等候回試", "lifeline_ready": "此前兩次回試後價格均已離開",
    "gap_across": "開市跳過觀察區", "pierced": "燭內曾穿區", "closed_through": "已收穿觀察區",
    "reclaimed": "收市已返回區間中線原側", "structure_broken": "已越過失效邊界",
    "sweep_reclaimed": "穿位後收市已返回區間中線原側", "mirror_ready": "此前由上方及下方回試均有反應",
    "mirror_test": "正在回試價區", "crossed_to_other_side": "已收穿價區，留意另一側的反應",
    "held_from_above": "由上方回試後回升", "rejected_from_below": "由下方回試後回落",
    "ema_test": "已回試當時的參考價位", "lifeline_inactive": "這個參考價位已停止追蹤",
    "trend_ended": "原有趨勢已結束", "expired": "已過觀察期", "session_ended": "即市觀察時段已結束",
    "data_gap": "行情中斷，停止追蹤"
}
ROLE = {"box_low": "區間低位", "box_high": "區間高位", "low": "最低價", "high": "最高價",
        "held_above": "從上方回試後回升", "rejected_below": "從下方回試後回落",
        "counter_open": "當時開市價", "confirmation_close": "其後收市價", "ema_reaction": "當時參考價位"}
OBS = {"touched": "已觸區", "pierced": "曾穿區", "back_across_center": "曾收回中線",
       "above_upper_edge": "曾升穿區頂", "below_lower_edge": "曾跌穿區底"}
FRAMES = (("daily", "天線"), ("intraday", "五分鐘"))
RETEST_STATUS = {"first_retest", "mirror_test", "ema_test"}
ROW_FIELDS = ("timeframe", "side", "level", "zone_low", "zone_high", "active", "formed_at", "as_of", "ended_at",
              "first_touch_at", "first_breach_at", "first_close_through_at", "first_reclaim_at", "breakout_at",
              "structural_level", "level_as_of", "formed_level", "box_low", "box_high", "box_start", "box_end",
              "box_bars", "outer", "trend_at_formation")


def status_text(code, side):
    if code == "swept":
        return "曾跌穿原支持位，等候收回" if side == "support" else "曾升穿原阻力位，等候收回"
    if code == "closed_through":
        return "收市跌穿原支持位" if side == "support" else "收市升穿原阻力位"
    return STATUS[code]


def public_level(raw):
    """Publish measured prices, evidence and event times. The stable ID carries no method label."""
    row = {key: raw[key] for key in ROW_FIELDS if key in raw}
    row["id"] = hashlib.sha256(raw["id"].encode("utf-8")).hexdigest()[:32]
    row["status"] = status_text(raw["status"], raw["side"])
    row["evidence"] = [dict(item, role=ROLE[item["role"]]) for item in raw["evidence"]]
    row["events"] = [dict(event, status=status_text(event["status"], raw["side"])) for event in raw["events"]]
    for event in reversed(raw["events"]):
        if event["status"] in RETEST_STATUS:
            row["last_retest"] = {"at": event["at"], "price": event["price"],
                                  "side": event.get("from_side", raw["side"])}
            if "level_as_of" in event:
                row["last_retest"]["level_as_of"] = event["level_as_of"]
            else:
                row["last_retest"].update(zone_low=raw["zone_low"], zone_high=raw["zone_high"])
            break
    if raw.get("intraday_observation"):
        edges = {"above_platform": "above_upper_edge", "below_platform": "below_lower_edge"}
        row["intraday_observation"] = {edges.get(key, key): value for key, value in raw["intraday_observation"].items()}
    return row


def public_tracker(profile):
    """Convert the calculation record once, including when a saved older record is rendered."""
    if profile.get("error") or "levels" in profile["daily"]:
        return profile
    public = dict(profile)
    ref = profile.get("reference_price")
    for key, _ in FRAMES:
        frame = profile[key]
        levels = {side: {"active": [], "recent": []} for side in SIDE}
        latest = []
        scans = frame["types"].values()
        unavailable = sum(g["state"] == "insufficient" for g in scans)
        for scan in scans:
            for bucket in ("active", "recent"):
                for raw in scan[bucket]:
                    row = public_level(raw)
                    levels[row["side"]][bucket].append(row)
                    touch = row.get("last_retest")
                    if touch and (not latest or touch["at"] >= latest[0]["at"]):
                        if latest and touch["at"] > latest[0]["at"]:
                            latest.clear()
                        latest.append(dict(touch, id=row["id"]))
        for group in levels.values():
            group["active"].sort(key=lambda row: (row["formed_at"], row["id"]), reverse=True)
            if ref is not None:
                group["active"].sort(key=lambda row: max(row["zone_low"] - ref, ref - row["zone_high"], 0))
            group["recent"].sort(key=lambda row: (row["events"][-1]["at"], row["id"]), reverse=True)
            if group["active"]:
                group["state"] = "tracking"
            elif unavailable == len(scans):
                group["state"] = "insufficient"
            else:
                group["state"] = "partial" if unavailable else "none"
        public[key] = {name: value for name, value in frame.items() if name != "types"}
        public[key]["levels"] = levels
        public[key]["latest_retests"] = sorted(latest, key=lambda t: (t["side"], t["price"], t["id"]))
    return public


def esc(x):
    return html.escape(str(x), quote=True)


def price(x):
    return "—" if x is None else f"{x:,.2f}"


def stamp(value):
    return value.replace("T", " ", 1) if value and value[10:11] == "T" else value


def latest_text(frame):
    touches = frame.get("latest_retests", [])
    if not touches:
        return ""
    values = dict.fromkeys(SIDE[t["side"]] + " " + (band(t) if "zone_low" in t else price(t["price"])) + " 點"
                           for t in touches)
    return "最近錄得回試　" + stamp(touches[0]["at"]) + "　" + "、".join(values)


def band(row):
    a, b = row["zone_low"], row["zone_high"]
    return price(a) if a == b else price(a) + "–" + price(b)


def milestones(row):
    for key, name in (("first_touch_at", "首次回試"), ("first_breach_at", "首次穿位"),
                      ("first_reclaim_at", "首次收回")):
        if row.get(key):
            yield name + " " + stamp(row[key])
    for event in row["events"]:
        if "trend_aligned" in event:
            yield "穿位時趨勢" + ("配合" if event["trend_aligned"] else "未配合")


def summary(row):
    parts = [SIDE[row["side"]], band(row), "形成 " + stamp(row["formed_at"])]
    if row.get("level_as_of"):
        parts.append("當時參考位 " + price(row["level"]) + "，更新於 " + stamp(row["level_as_of"]))
    parts += [row["status"] + " " + stamp(row["events"][-1]["at"]), *milestones(row)]
    return " · ".join(parts)


def evidence_lines(row):
    if row.get("box_start"):
        yield f"整固由 {stamp(row['box_start'])} 至 {stamp(row['box_end'])}，共 {row['box_bars']} 支燭。"
    if row.get("breakout_at"):
        yield "收市突破 " + stamp(row["breakout_at"])
    if row.get("structural_level") is not None:
        yield "失效邊界 " + price(row["structural_level"])
    if row.get("level_as_of"):
        yield "形成時參考位 " + price(row["formed_level"]) + "，計於 " + stamp(row["formed_at"])
        yield "最近參考位 " + price(row["level"]) + "，計於 " + stamp(row["level_as_of"]) + "，供下一支燭觀察。"
    for item in row["evidence"]:
        yield f"{stamp(item['bar'])}　{item['role']} {price(item['price'])}"
    if "trend_at_formation" in row:
        trend = {1: "上升", 0: "橫行或未確認", -1: "下降"}[row["trend_at_formation"]]
        yield "形成時趨勢 " + trend + "，觀察區外沿 " + price(row["outer"])


def observation(row):
    return "、".join(OBS[k] + " " + stamp(v) for k, v in row.get("intraday_observation", {}).items() if k in OBS)


def text_lines(report, limit=3):
    report = public_tracker(report)
    out = [f"{report['name']}（{report['symbol']}） 支持位及阻力位　{report['timezone']}"]
    if report.get("reference_price") is not None:
        out.append("參考指數 " + price(report["reference_price"]) + " 點")
    for key, heading in FRAMES:
        frame = report[key]
        out.append(f"{heading}　{stamp(frame['as_of']) or '—'}　{QUALITY[frame['quality']]}")
        if frame["latest_retests"]:
            out.append(latest_text(frame))
        for side, group in frame["levels"].items():
            rows, recent = group["active"], group["recent"]
            if side == "range" and not (rows or recent):
                continue
            if rows:
                out.append(f"  {SIDE[side]}　{len(rows)} 個追蹤中")
                for row in rows[:limit]:
                    out.append("    " + summary(row))
                    if observation(row):
                        out.append("    本日盤中觀察（天線待收市） " + observation(row))
            else:
                out.append(f"  {SIDE[side]}　{STATE[group['state']]}")
            if recent:
                out.append("    最近結束　" + summary(recent[0]))
    if report.get("intraday_note"):
        out.append(report["intraday_note"])
    out.extend(report.get("warnings", []))
    return out


def row_html(row):
    history = "".join("<li>" + esc(stamp(e["at"]) + "　" + e["status"] + "　" + price(e["price"])
                      + ("（參考位計於 " + stamp(e["level_as_of"]) + "）" if e.get("level_as_of") else "")) + "</li>"
                      for e in row["events"])
    proof = "".join("<li>" + esc(x) + "</li>" for x in evidence_lines(row))
    marks = ["狀態更新 " + stamp(row["events"][-1]["at"]), *milestones(row)]
    if row.get("ended_at"):
        marks.insert(0, "結束 " + stamp(row["ended_at"]))
    if observation(row):
        marks.append("盤中觀察，天線待收市　" + observation(row))
    details = "<details><summary>價格紀錄</summary><ul>" + proof + "</ul><ol>" + history + "</ol></details>"
    level_note = ""
    if row["side"] != "range":
        level_note = "<br><small>參考位 " + price(row["level"]) + "</small>"
    if row.get("level_as_of"):
        level_note += "<br><small>計於 " + esc(stamp(row["level_as_of"])) + "</small>"
    return (f'<tr><td>{esc(band(row))}{level_note}</td><td>{esc(stamp(row["formed_at"]))}</td>'
            f'<td>{esc(row["status"])}<br><small>{esc(" · ".join(marks))}</small></td><td>{details}</td></tr>')


def table(rows):
    if not rows:
        return ""
    return ('<div class="table-scroll"><table><thead><tr><th>價位區間</th><th>形成時間</th><th>最近狀態</th>'
            '<th>價格紀錄</th></tr></thead><tbody>' + ''.join(row_html(row) for row in rows) + '</tbody></table></div>')


def section(report):
    report = public_tracker(report)
    out = ['<section class="tracker"><h2>' + esc(report["name"]) + ' · 支持位及阻力位</h2>',
           '<p>市場時區 ' + esc(report["timezone"]) + '。五分鐘價格紀錄標示原燭開始時間，價位形成及狀態更新標示收燭時間。天線日期表示當日收市。</p>']
    if report.get("reference_price") is not None:
        out.append('<p>參考指數 <strong>' + price(report["reference_price"]) + '</strong> 點</p>')
    if report.get("intraday_note"):
        out.append('<p>' + esc(report["intraday_note"]) + '</p>')
    out.extend('<p>' + esc(w) + '</p>' for w in report.get("warnings", []))
    for key, name in FRAMES:
        frame = report[key]
        out.append(f'<h3>{name} · {esc(stamp(frame["as_of"]) or "—")}</h3><p>{esc(QUALITY[frame["quality"]])}</p>')
        if frame["latest_retests"]:
            out.append('<p>' + esc(latest_text(frame)) + '</p>')
        if frame["coverage"].get("gaps"):
            out.append('<p>行情有缺口的交易日　' + esc('、'.join(frame["coverage"]["gaps"])) + '</p>')
        for side, group in frame["levels"].items():
            rows, recent = group["active"], group["recent"]
            if side == "range" and not (rows or recent):
                continue
            out.append(f'<h4>{SIDE[side]}　{esc(STATE[group["state"]])}</h4>' + table(rows))
            if recent:
                out.append(f'<details><summary>最近結束的價位（顯示 {min(6, len(recent))}／{len(recent)} 個）</summary>'
                           + table(recent[:6]) + '</details>')
    out.append('</section>')
    return ''.join(out)


def dashboard(reports):
    from key_levels import AI_NOTICE, DISCLAIMER_FULL
    css = """*{box-sizing:border-box}body{margin:0;background:#f4f5f0;color:#173039;font:16px/1.65 -apple-system,BlinkMacSystemFont,"PingFang HK",sans-serif}main,header>div{max-width:1160px;margin:auto;padding:20px 4vw}header{background:#143d40;color:white}h1{font-size:clamp(28px,5vw,48px);margin:.2em 0}h2{color:#1a5149}h3{border-top:2px solid #c4d6ce;padding-top:10px}h4{font-size:16px;margin:16px 0 4px}article{background:white;border:1px solid #cfdbd6;border-radius:10px;padding:14px 22px;margin:18px 0}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:8px;border-bottom:1px solid #dce5de;text-align:left;vertical-align:top}th{background:#e8efea;white-space:nowrap}small{color:#4a6469}nav a{margin-right:16px;color:#175e50}summary{cursor:pointer;color:#196454}p{margin:7px 0}details{margin:5px 0}ol,ul{padding-left:21px}footer{padding:18px 0;color:#4c626b;font-size:13px}@media(max-width:650px){main{padding:12px}article{padding:12px}td{min-width:85px}}"""
    out = ['<!doctype html><html lang="zh-HK"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>指數支持位及阻力位</title><style>'+css+'</style><header><div><h1>指數支持位及阻力位</h1><p>現貨指數 · 天線及五分鐘圖 · A.I. 計算及生成</p></div></header><main>',
           '<nav>'+''.join(f'<a href="#i{i}">{esc(r["name"])}</a>' for i, r in enumerate(reports))+'</nav>']
    for i, r in enumerate(reports):
        if r.get("error"):
            body = '<h2>'+esc(r["name"])+'</h2><p>行情無法核對　'+esc(r["error"])+'</p>'
        else:
            source = "合成測試行情" if r["synthetic"] else "已保存行情，離線重播" if r.get(
                "offline") else "Yahoo Finance，報價可能延遲"
            body = '<p>'+esc(source)+'　'+esc(r["symbol"])+'</p>'+section(r)
        out.append(f'<article id="i{i}">'+body+'</article>')
    out.append('<footer>'+esc(AI_NOTICE)+'<br>'+esc(DISCLAIMER_FULL)+'</footer></main></html>')
    return ''.join(out)
