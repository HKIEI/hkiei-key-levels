"""Prior-close zones, irrevocable opening choices and walk-forward reaction measurements."""
import calendar
import datetime as dt
import statistics
from bisect import bisect_left

SIDES = (("support", "nearest_supports", "支持 1"), ("resistance", "nearest_resistances", "阻力 1"))
GROUPS = (("all", "全部實際觸及"), ("outside", "由區外回試"), ("near", "區外回試，開市距離最多 ¼ ATR"))
REGIMES = (("normal", "一般波幅"), ("high", "較高波幅"))
LAYERS = (("primary", "第一層"), ("backup", "後備第二層"), ("combined", "兩層合計"))
RULE_FIXED_ON = "2026-11-20"
NEAR_ATR = 0.25
MOVE_ATR = 0.5
MIN_HISTORY = 250


def year_start(date, years):
    d = dt.date.fromisoformat(date)
    year = d.year - years
    return d.replace(year=year, day=min(d.day, calendar.monthrange(year, d.month)[1])).isoformat()


def frozen_plan(history, dates, stop, data, symbol, args, levels, daily_atr):
    """Use only bars strictly before stop to freeze both layers at the anchor close."""
    if stop < 1:
        return None
    anchor = history[stop - 1]
    start = bisect_left(dates, year_start(anchor["date"], 1), 0, stop)
    if stop - start < MIN_HISTORY:
        start = bisect_left(dates, year_start(anchor["date"], 2), 0, stop)
    segment = history[start:stop]
    if len(segment) < MIN_HISTORY:
        return None
    snapshot = {"completed": segment, "bars": segment, "previous": segment[-2],
                "price": {"value": anchor["close"], "date": anchor["date"]},
                "meta": data["meta"], "timezone": data["timezone"], "as_of_utc": anchor["date"],
                "warnings": []}
    report = levels(snapshot, symbol, args)
    atr = report["atr"]["value"]
    # Compare the anchor's ATR% to the preceding 60 sessions, excluding the anchor.
    past = [100 * daily_atr(segment[j-14:j+1]) / segment[j]["close"]
            for j in range(len(segment)-61, len(segment)-1)]
    median = statistics.median(past)
    atr_pct = 100 * atr / anchor["close"]
    zones, backups = {}, {}
    for side, key, _ in SIDES:
        if report[key]:
            z = report[key][0]
            zones[side] = {"low": z["low"], "high": z["high"]}
            if len(report[key]) >= 2:
                z = report[key][1]
                backups[side] = {"low": z["low"], "high": z["high"]}
    return {"as_of": anchor["date"], "anchor_close": anchor["close"], "atr": atr,
            "atr_pct": atr_pct, "prior_60_median_atr_pct": median,
            "regime": "high" if atr_pct > median else "normal", "zones": zones, "backup_zones": backups}


def opening_distance(side, zone, opening):
    return opening - zone["high"] if side == "support" else zone["low"] - opening


def choose_at_open(plan, side, opening):
    """Only the frozen bands and the regular-session opening price participate."""
    primary = plan["zones"].get(side)
    if primary is None:
        return {"layer": None, "reason": "no_primary"}
    if opening_distance(side, primary, opening) > 0:
        return {"layer": "primary", "reason": "primary_eligible", "zone": primary}
    backup = plan.get("backup_zones", {}).get(side)
    if backup is None:
        return {"layer": None, "reason": "no_backup"}
    if opening_distance(side, backup, opening) > 0:
        return {"layer": "backup", "reason": "primary_ineligible", "zone": backup}
    return {"layer": None, "reason": "both_ineligible"}


def observe(side, zone, atr, bar):
    """Opening eligibility can be known at the open. Only finalized bars get outcomes."""
    support = side == "support"
    edge = zone["high"] if support else zone["low"]
    direction = 1 if support else -1
    distance = opening_distance(side, zone, bar["open"])
    outside = distance > 0
    open_atr = distance / atr if atr > 0 else None
    opening = "inside"
    if outside:
        opening = "outside"
    elif not zone["low"] <= bar["open"] <= zone["high"]:
        opening = "beyond"
    touched = bar["low"] <= zone["high"] and bar["high"] >= zone["low"]
    complete = bar["complete"]
    away = direction * (bar["close"] - edge)
    return {"opening": opening, "outside": outside, "open_distance_atr": open_atr,
            "near": outside and open_atr is not None and open_atr <= NEAR_ATR, "touch": touched,
            "breach": bar["low"] < zone["low"] if support else bar["high"] > zone["high"],
            "closed_beyond": (bar["close"] < zone["low"] if support else bar["close"] > zone["high"]) if complete else None,
            "gap_skip": complete and opening == "beyond" and not touched,
            "returned": touched and away > 0 if complete else None,
            "moved_half": touched and away >= MOVE_ATR * atr if complete and atr > 0 else None}


def evaluate(plan, bar):
    observations = {side: observe(side, zone, plan["atr"], bar) for side, zone in plan["zones"].items()}
    selected = {}
    for side, _, _ in SIDES:
        choice = choose_at_open(plan, side, bar["open"])
        if choice["layer"] == "primary":
            choice["observation"] = observations[side]
        elif choice["layer"] == "backup":
            choice["observation"] = observe(side, choice["zone"], plan["atr"], bar)
        selected[side] = choice
    return {"date": bar["date"], "open": bar["open"], "low": bar["low"], "high": bar["high"], "complete": bar["complete"],
            "plan": plan, "sides": observations, "selected": selected}


def counts():
    return dict(touches=0, returned=0, atr_touches=0, moved_half=0, gap_skips=0)


def add(count, obs):
    count["gap_skips"] += obs["gap_skip"]
    if obs["touch"]:
        count["touches"] += 1
        count["returned"] += obs["returned"]
        if obs["moved_half"] is not None:
            count["atr_touches"] += 1
            count["moved_half"] += obs["moved_half"]


def layer_groups(factory):
    return {key: {side: factory() for side in ("support", "resistance", "both")} for key, _ in LAYERS}


def tally_layers(events):
    totals = layer_groups(lambda: dict(selected=0, breaches=0, closed_beyond=0, **counts()))
    for event in events:
        for side, choice in event["selected"].items():
            if choice["layer"] is None:
                continue
            for layer in (choice["layer"], "combined"):
                for direction in (side, "both"):
                    count = totals[layer][direction]
                    count["selected"] += 1
                    obs = choice["observation"]
                    count["breaches"] += obs["breach"]
                    count["closed_beyond"] += obs["closed_beyond"]
                    add(count, obs)
    return totals


def build_watch(data, symbol, args, levels, daily_atr):
    history = data.get("reaction_history", data["completed"])
    dates = [b["date"] for b in history]
    latest = data["bars"][-1]
    days = getattr(args, "reaction_days", 30)
    start = max(0, len(history) - days)
    grouped = {group: {side: counts() for side in ("support", "resistance", "both")} for group, _ in GROUPS}
    regimes = {regime: counts() for regime, _ in REGIMES}
    events = []
    latest_session = None
    for i in range(start, len(history)):
        plan = frozen_plan(history, dates, i, data, symbol, args, levels, daily_atr)
        if plan is None:
            continue
        event = evaluate(plan, history[i])
        events.append(event)
        if event["date"] == latest["date"]:
            latest_session = event
        for side, obs in event["sides"].items():
            for group, _ in GROUPS:
                if group == "all" or obs[group]:
                    add(grouped[group][side], obs)
                    add(grouped[group]["both"], obs)
            if obs["outside"]:
                add(regimes[plan["regime"]], obs)
    next_plan = frozen_plan(history, dates, len(history), data, symbol, args, levels, daily_atr)
    if (not latest["complete"] and latest.get("opening_available", True)
            and next_plan is not None and latest["date"] > next_plan["as_of"]):
        latest_session = evaluate(next_plan, latest)
    return {"requested_days": days, "available_days": len(history)-start, "scored_days": len(events),
            "start": dates[start] if dates else None, "end": dates[-1] if dates else None,
            "groups": grouped, "regimes": regimes, "events": events, "latest_session": latest_session,
            "next_plan": next_plan, "primary_filter": "outside", "near_atr": NEAR_ATR, "move_atr": MOVE_ATR,
            "selection_policy": "first_eligible_at_open", "layer_scores": tally_layers(events),
            "selected_by_regime": {key: tally_layers([e for e in events if e["plan"]["regime"] == key])["combined"]
                                   for key, _ in REGIMES},
            "rules_fixed_on": RULE_FIXED_ON,
            "layer_scores_after_rule": tally_layers([e for e in events if e["date"] > RULE_FIXED_ON])}


def ratio(n, d):
    return "%s/%s（%.1f%%）" % (n, d, 100*n/d) if d else "—（分母為 0）"


def band(zone):
    lo, hi = zone["low"], zone["high"]
    return format(lo, ",.2f") if lo == hi else "{:,.2f}–{:,.2f}".format(lo, hi)


def hour_row(label, score):
    return (label, score["evaluated"], ratio(score["favourable"], score["evaluated"]),
            score["adverse"], score["timeout"], score["ambiguous"])


def daily_row(label, score):
    return (label, score["selected"], score["touches"], ratio(score["returned"], score["touches"]),
            ratio(score["moved_half"], score["atr_touches"]), score["breaches"], score["closed_beyond"])


def plan_rows(watch):
    """Shared human-readable rows for text and HTML. Keep observation dates explicit."""
    rows = []
    session, plan = watch["latest_session"], watch["next_plan"]
    if session:
        regime = dict(REGIMES)[session["plan"]["regime"]]
        timing = "已收市" if session["complete"] else "盤中，待收市"
        heading = "{}（{}） · 按 {} 收市劃位 · {}".format(session["date"], timing, session["plan"]["as_of"], regime)
        if "low" in session:
            heading += " · 開市 {:,.2f} · {}範圍 {:,.2f}–{:,.2f}".format(
                session["open"], "全日" if session["complete"] else "截至報價時", session["low"], session["high"])
        lines = []
        for side, _, label in SIDES:
            choice = session.get("selected", {}).get(side)
            zone = session["plan"]["zones"].get(side)
            if choice and choice["layer"] is None:
                state = {"no_primary": "沒有第一層位置", "no_backup": "第一層開市不合資格，沒有後備第二層",
                         "both_ineligible": "兩層在開市均不合資格"}[choice["reason"]]
                if zone:
                    state += "。上日第一層 " + band(zone)
                backup = session["plan"].get("backup_zones", {}).get(side)
                if backup:
                    state += "，後備 " + band(backup)
                lines.append((label[:-2] + " · 本日略過", state))
                continue
            obs = choice["observation"] if choice else session["sides"].get(side)
            if obs is None:
                lines.append((label, "該方向沒有合資格位置"))
                continue
            if choice:
                zone = choice["zone"]
                label = label if choice["layer"] == "primary" else label[:-1] + "2（後備）"
            status = {"outside": "開市符合回試方向", "inside": "僅供參考，開市在區內或邊界",
                      "beyond": "僅供參考，開市已越過整區"}[obs["opening"]]
            if choice and choice["layer"] == "backup":
                status = "第一層 " + band(session["plan"]["zones"][side]) + " 開市不合資格，採用後備"
            distance = obs["open_distance_atr"]
            if distance is not None:
                status += "（距近端 {:+.2f} ATR）".format(distance)
            if obs["near"]:
                status += "，距離符合 ¼ ATR 條件"
            if obs["touch"]:
                status += "，已觸及"
                if session["complete"]:
                    if obs["moved_half"]:
                        status += "，收市向上彈離至少半個 ATR" if side == "support" else "，收市向下回落至少半個 ATR"
                    else:
                        status += "，收市已回到區外原側" if obs["returned"] else "，收市未回到區外原側"
            else:
                status += "，全日跳過整區" if obs["gap_skip"] else "，未觸及"
            if not session["complete"]:
                status += "，收市結果待定"
            if choice and "hour" in choice and session["complete"]:
                from intraday_reactions import LABELS
                status += "。一小時觀察為「" + LABELS[choice["hour"]["status"]] + "」"
            lines.append((label + " " + band(zone), status))
        rows.append((heading, lines))
    if not plan:
        return rows
    if session and not session["complete"] and session["plan"]["as_of"] == plan["as_of"]:
        return rows
    lines = []
    for side, _, label in SIDES:
        zone = plan["zones"].get(side)
        if zone is None:
            lines.append((label, "該方向沒有合資格位置"))
            continue
        support = side == "support"
        edge = zone["high"] if support else zone["low"]
        condition = ("開市須高於 " if support else "開市須低於 ") + format(edge, ",.2f")
        if plan["atr"] > 0:
            limit = edge + (1 if support else -1) * NEAR_ATR * plan["atr"]
            condition += ("，同時不高於約 {:,.2f} 則符合 ¼ ATR 條件" if support else
                          "，同時不低於約 {:,.2f} 則符合 ¼ ATR 條件").format(limit)
        lines.append((label + " " + band(zone), condition))
        backup = plan.get("backup_zones", {}).get(side)
        if backup:
            limit = backup["high"] if support else backup["low"]
            condition = "只在第一層開市不合資格時採用，開市須" + ("高於 " if support else "低於 ") + format(limit, ",.2f")
            lines.append((label[:-1] + "2（後備） " + band(backup), condition))
    rows.append((plan["as_of"] + " 收市後制定 · 待下一交易日的開市價", lines))
    return rows
