"""Display the precomputed HSI pattern plan and first-touch evidence."""
from html import escape
from hsi_patterns import SIDES
from reaction_watch import ratio

MODELS = (("combined","追蹤價位（去重）"),("legacy","舊版價區對照"))
SOURCE_PERIOD = {"range_breakout": "1d", "mirror_60m": "1h", "mirror_week": "1w"}
PERIOD_NAME = {"1d": "天線", "1h": "一小時", "1w": "週線"}
DIRECTIONS = {"support":"支持", "resistance":"阻力", "both":"合計"}
HEADERS = ("選位", "有效反應／可評", "先失守", "未決", "先順向半個 ATR／可評", "最遠順向中位數", "日內穿位／觸及")


def source_name(candidate):
    period = candidate.get("source_timeframe") or SOURCE_PERIOD.get(candidate.get("kind"))
    return PERIOD_NAME.get(period, "價位觀察")


def public_history(value):
    """Keep the selected levels, benchmark and their original observations in historical exports."""
    if isinstance(value, list):
        return [public_history(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: public_history(item) for key, item in value.items() if key not in ("range_breakout", "mirror", "kind")}
    if value.get("kind") in SOURCE_PERIOD:
        result["source_timeframe"] = SOURCE_PERIOD[value["kind"]]
    return result


def rates(stats, split=False):
    for model, name in MODELS:
        for side in (SIDES if split else ("both",)):
            s = stats[model][side]
            yield (name+(" · "+DIRECTIONS[side] if split else ""), ratio(s["reaction"],s["evaluated"]) if s["evaluated"] else "無可評樣本",
                   s["invalid"], s["unresolved"], ratio(s["half"]["favourable"],s["evaluated"]) if s["evaluated"] else "無可評樣本",
                   "—" if s["median_favourable"] is None else f'{s["median_favourable"]:,.1f} 點',
                   ratio(s["breached"],s["touched"]))


FLOW_HEADERS = ("選位","開市前提名","開市合資格","天線觸及","一小時可評觸及")


def opportunity_rows(stats):
    for model,name in MODELS:
        for side in SIDES:
            s = stats[model][side]
            yield (name+" · "+DIRECTIONS[side],s["nominated"],s["selected"],s["touched"],s["evaluated"])


def plan_rows(profile):
    plan = profile["next_plan"]
    if not plan:
        return [("支持及阻力","暫缺事前行情")]
    live = profile.get("latest_session")
    on_open = live is not None and not live["complete"]
    out = []
    for side in SIDES:
        c = plan["nominees"]["combined"].get(side)
        if c is None:
            value = "觀望，暫無合資格價位"
        else:
            z = c["zone"]
            status = ("開市合資格" if live["models"]["combined"][side]["selected"] else "開市不合資格，觀望") if on_open else "待下次開市核對"
            value = "%s · %s · %s–%s 點" % (status,source_name(c),format(z["low"],",.1f"),format(z["high"],",.1f"))
            if c.get("breakout"):
                value += " · 整固期 %s 至 %s，%s 突破" % (c["box_start"],c["box_end"],c["breakout"])
            else:
                value += " · 過往兩面反應為支持 %d、阻力 %d 個日期" % (len(c["holds"]),len(c["rejects"]))
            value += " · 失效邊界 %s 點" % format(c["invalidation"],",.1f")
        out.append((DIRECTIONS[side],value))
    if not plan["hourly_ready"]:
        out.append(("一小時行情","近 20 個交易日取得 %d 個完整時段，未足 15 日。" % plan["hourly_days"]))
    if not plan["weekly_ready"]:
        out.append(("週線行情","取得 %d 根已完成週線，未足 52 根。" % plan["weekly_bars"]))
    return out


RULE = ("首次觸及的五分鐘燭收市，至其後連續一小時，先有收市價向預期方向離開近端至少 0.15 個上日 ATR，"
        "便算有效反應。先收穿遠端計失守。一小時仍未達標計未決。另以舊版同一小時正負半個 ATR 比較大幅反應。")
PLAN_RULE = "價位按上日收市鎖定。每日每邊最多一個，支持須由區上、阻力須由區下開市。開市不合資格便觀望。"


def text_lines(profile):
    return (["恒指價位觀察 · 支持反彈與阻力回落", PLAN_RULE]
            + ["- %s · %s"%r for r in plan_rows(profile)]
            + ["- %s · 提名 %d · 開市合資格 %d · 觸及 %d · 一小時可評 %d"%r for r in opportunity_rows(profile["stats"])]
            + [RULE] + ["- %s · 有效反應 %s · 先失守 %d · 未決 %d · 順向半個 ATR %s · 最遠順向中位數 %s · 日內穿位 %s"%r
                         for r in list(rates(profile["stats"]))+list(rates(profile["stats"],True))]
            + ["兩邊均觀望 %d 日。定稿後可評 %d 次。" % (profile["stats"]["combined"]["abstain_days"],profile["after_rule"]["stats"]["combined"]["both"]["evaluated"])])


def html_section(profile, table):
    p = profile["next_plan"]
    plans = "".join("<li><b>"+escape(k)+"</b> · "+escape(v)+"</li>" for k,v in plan_rows(profile))
    stats = profile["stats"]
    dates = profile["events"]
    interval = (dates[0]["date"]+" 至 "+dates[-1]["date"]) if dates else "暫無可重建交易日"
    return ('<section aria-labelledby="hsi-pattern-title"><h2 id="hsi-pattern-title">恒指價位觀察 · 支持反彈與阻力回落</h2><p>'
            + escape(PLAN_RULE)+'</p><h3>'+('按 '+escape(p["as_of"])+' 收市劃位' if p else '價位觀察')+'</h3><ul>'+plans
            +'</ul><h3>'+escape(interval)+'</h3><p>'+escape(RULE)+'</p>'+table(HEADERS,list(rates(stats)))
            +table(FLOW_HEADERS,list(opportunity_rows(stats)))
            +'<p>兩邊均觀望 %d / %d 日。期內一小時行情有 %d 日達到歷史要求，週線行情有 %d 日。'
             '每個交易日、每個方向只取一次。可評觸及為零時顯示「無可評樣本」。</p>' %
            (stats["combined"]["abstain_days"],len(dates),profile["hourly_ready_days"],profile["weekly_ready_days"])
            +'<details><summary>按支持與阻力查看</summary>'+table(HEADERS,list(rates(stats,True)))+'</details>'
            +'<details><summary>定稿後已收市的交易日</summary><p>'+escape(profile["rule_date"])+' 定稿，期內共有 %d 個其後交易日。</p>'%profile["after_rule"]["days"]
            +table(HEADERS,list(rates(profile["after_rule"]["stats"])))+'</details></section>')


def chart_zones(profile, price):
    """Use the current session's locked choices while open, or next-session nominees after close."""
    p = profile["next_plan"]
    if not p:
        return []
    live = profile.get("latest_session")
    zones = []
    for side, c in p["nominees"]["combined"].items():
        if live and not live["complete"] and not live["models"]["combined"][side]["selected"]:
            continue
        z = c["zone"]
        center = (z["low"]+z["high"])/2
        zones.append(dict(z,side=side,center=center,distance_pct=100*(center/price-1),
                          id="HSI_"+side,short=DIRECTIONS[side],label=source_name(c)+" "+DIRECTIONS[side],
                          pattern_name=source_name(c)))
    return zones
