"""HSI-only comparison with frozen floor pivots and observed first-touch excursions."""
import statistics

from intraday_reactions import OUTCOMES, STEP, measure, tally
from reaction_watch import RULE_FIXED_ON, SIDES, evaluate, ratio, tally_layers

HALF_WIDTH_ATR = 0.15
MODELS = (("primary", "舊版天線選位"), ("pivot", "上日樞軸對照"))


def pivot_plan(plan, bars_by_date):
    if not plan:
        return None
    previous = bars_by_date[plan["as_of"]]
    high, low, close = (previous[k] for k in ("high", "low", "close"))
    pivot = (high + low + close) / 3
    span, half_width = high - low, HALF_WIDTH_ATR * plan["atr"]
    centers = {"support": (2*pivot-high, pivot-span), "resistance": (2*pivot-low, pivot+span)}
    zones, backup = {}, {}
    for side, (first, second) in centers.items():
        if first - half_width <= 0:
            continue
        zones[side] = {"low": first - half_width, "high": first + half_width}
        second_band = {"low": second - half_width, "high": second + half_width}
        separate = second_band["high"] < zones[side]["low"] if side == "support" else second_band["low"] > zones[side]["high"]
        if second_band["low"] > 0 and separate:
            backup[side] = second_band
    return dict(plan, zones=zones, backup_zones=backup, pivot=pivot)


def excursions(hour, side, atr, session):
    """Measure all 12 subsequent candles, including after an adverse first barrier."""
    if hour["status"] not in OUTCOMES:
        return None
    origin = hour["reference_price"]
    bars = [session["candles"][t] for t in range(hour["reference_at"], hour["window_end"], STEP)]
    up = max(0.0, max(bar[1] for bar in bars) - origin)
    down = max(0.0, origin - min(bar[2] for bar in bars))
    best, worst = (up, down) if side == "support" else (down, up)
    return {"favourable_points": best, "adverse_points": worst,
            "favourable_atr": best / atr, "adverse_atr": worst / atr}


def model_stats(events):
    hours = tally(events)
    daily = tally_layers(events)["combined"]
    by_side = {}
    for direction in ("support", "resistance", "both"):
        moves = [choice["excursion"] for event in events for side, choice in event["selected"].items()
                 if direction in (side, "both") and choice.get("excursion") is not None]
        medians = {key: statistics.median(move[key] for move in moves) if moves else None
                   for key in ("favourable_points", "adverse_points", "favourable_atr", "adverse_atr")}
        by_side[direction] = {"hour": hours["scores"]["combined"][direction], "daily": daily[direction],
                              "median": medians}
    return {"directions": by_side, "excluded": hours["excluded"]}


def build(watch, data, sessions):
    bars = {b["date"]: b for b in data.get("reaction_history", data["completed"])}
    latest = watch["latest_session"]
    if latest and not latest["complete"]:
        bars[latest["date"]] = data["bars"][-1]
    comparison = []
    latest_comparison = None
    events = watch["events"]
    for event in events + ([latest] if latest and not latest["complete"] else []):
        date = event["date"]
        alt = evaluate(pivot_plan(event["plan"], bars), bars[date])
        session = sessions.get(date)
        for side, _, _ in SIDES:
            c = alt["selected"][side]
            c["hour"] = measure(c, side, alt["plan"]["atr"], session, alt["complete"])
            for item in (event, alt):
                selected = item["selected"][side]
                selected["excursion"] = excursions(selected["hour"], side, item["plan"]["atr"], session)
        if event["complete"]:
            comparison.append(alt)
        if latest and date == latest["date"]:
            latest_comparison = alt
    future = [event for event in events if event["date"] > RULE_FIXED_ON]
    future_pivots = [event for event in comparison if event["date"] > RULE_FIXED_ON]
    return {"role": "comparison_only", "rules_fixed_on": RULE_FIXED_ON, "half_width_atr": HALF_WIDTH_ATR,
            "stats": {"primary": model_stats(events), "pivot": model_stats(comparison)},
            "after_rule": {"days": len(future), "primary": model_stats(future), "pivot": model_stats(future_pivots)},
            "events": comparison, "latest_session": latest_comparison, "next_plan": pivot_plan(watch["next_plan"], bars)}


def metric_rows(stats, split=False):
    rows = []
    sides = (("support", "支持"), ("resistance", "阻力")) if split else (("both", ""),)
    for model, name in MODELS:
        for side, label in sides:
            metrics = stats[model]["directions"][side]
            hour, med = metrics["hour"], metrics["median"]
            distance = "—" if med["favourable_points"] is None else (
                "{:,.1f} 點 ／ {:.2f} ATR".format(med["favourable_points"], med["favourable_atr"]))
            rows.append((name + (" · " + label if label else ""), ratio(hour["favourable"], hour["evaluated"]),
                         hour["adverse"], hour["timeout"], hour["ambiguous"], distance))
    return rows
