"""Prior-close price-zone selection and first-retest observations for the Hang Seng cash index."""
from bisect import bisect_left, bisect_right
from collections import Counter
import datetime as dt
from itertools import groupby
from statistics import median
from zoneinfo import ZoneInfo

from hsi_reactions import excursions
from intraday_reactions import OUTCOMES, STEP, complete, measure
from reaction_watch import observe, opening_distance

FROZEN_ON = "2026-11-20"
SIDES = ("support", "resistance")
MODELS = ("combined", "mirror", "range_breakout", "legacy")
RESPONSE_ATR = 0.15
MAX_DISTANCE_ATR = 2
HK = ZoneInfo("Asia/Hong_Kong")
NAMES = {"range_breakout": "日線區間首次回試", "mirror_60m": "一小時 MIRROR", "mirror_week": "週線 MIRROR"}


def week_key(day):
    return dt.date.fromisoformat(day).isocalendar()[:2]


def weekly_bars(history):
    weeks = []
    for _, batch in groupby(history, key=lambda b: week_key(b["date"])):
        bars = list(batch)
        weeks.append(dict(date=bars[-1]["date"], open=bars[0]["open"], close=bars[-1]["close"],
                          high=max(b["high"] for b in bars), low=min(b["low"] for b in bars)))
    if weeks and dt.date.fromisoformat(weeks[-1]["date"]).weekday() < 4:
        weeks.pop()
    # A Monday start includes the whole trading week. Other leading weeks may be partial.
    if history and dt.date.fromisoformat(history[0]["date"]).weekday() != 0:
        weeks = weeks[1:]
    return weeks[-156:]


def hourly_bars(prior_dates, sessions):
    bars, good_days = [], 0
    for day in prior_dates[-20:]:
        sess = sessions.get(day)
        if not complete(sess) or not sess.get("closes"):
            continue
        day_bars = []
        for t in sess["slots"]:
            local = dt.datetime.fromtimestamp(t, HK)
            if (local.hour, local.minute) not in ((9,30), (10,30), (13,0), (14,0), (15,0)):
                continue
            slots = range(t, t+3600, STEP)
            if not all(k in sess["candles"] for k in slots):
                continue
            candles = [sess["candles"][k] for k in slots]
            day_bars.append(dict(date=day, time=local.strftime("%H:%M"), open=candles[0][0],
                             high=max(b[1] for b in candles), low=min(b[2] for b in candles),
                             close=sess["closes"][t+3600-STEP]))
        if len(day_bars) == 5:
            good_days += 1
            bars.extend(day_bars)
    return bars, good_days


def mirror_candidates(bars, half, kind, anchor, atr):
    # Price indexes avoid comparing every candidate with all bars from several years.
    lows = sorted((b["low"], i) for i,b in enumerate(bars))
    highs = sorted((b["high"], i) for i,b in enumerate(bars))
    centers = sorted({p for p,_ in lows+highs if abs(p-anchor) <= MAX_DISTANCE_ATR*atr+half})
    for center in centers:
        lo, hi = center-half, center+half
        evidence = {}
        for side, index in (("support", lows), ("resistance", highs)):
            matches = {}
            window = index[bisect_left(index, (lo,-1)):bisect_right(index, (hi,len(bars)))]
            for _, i in sorted(window, key=lambda pair:pair[1]):
                b = bars[i]
                span = b["high"]-b["low"]
                away = (min(b["open"],b["close"])-hi if side=="support"
                        else lo-max(b["open"],b["close"]))
                if span > 0 and away >= .25*span:
                    stamp = b["date"] + (" " + b["time"] if "time" in b else "")
                    matches.setdefault(b["date"], stamp)
            evidence[side] = matches
        holds, rejects = evidence["support"], evidence["resistance"]
        if min(len(holds), len(rejects)) < 2 or len(holds.keys() | rejects.keys()) < 4:
            continue
        yield dict(kind=kind, zone=dict(low=lo, high=hi), holds=list(holds.values()),
                   rejects=list(rejects.values()), balance=min(len(holds),len(rejects)))


def range_breakout_candidates(history, daily_atr):
    for i in range(max(15,len(history)-20), len(history)):
        br = history[i]
        atr = daily_atr(history[i-15:i])
        if atr <= 0 or br["close"] == br["open"]:
            continue
        side = "support" if br["close"] > br["open"] else "resistance"
        for size in range(10, 4, -1):
            box = history[i-size:i]
            lo, hi = min(b["low"] for b in box), max(b["high"] for b in box)
            height = hi-lo
            if not 0 < height <= 2*atr or height >= .10*lo:
                continue
            if abs(box[-1]["close"] - box[0]["close"]) > height/2:
                continue
            if (sum(b["high"] >= hi-height/4 for b in box) < 2 or
                    sum(b["low"] <= lo+height/4 for b in box) < 2):
                continue
            edge = hi if side == "support" else lo
            zone = {"low":edge-.10*atr, "high":edge+.10*atr}
            cleared = br["close"] > zone["high"] if side=="support" else br["close"] < zone["low"]
            if not cleared:
                continue
            since = history[i+1:]
            tested = (any(b["low"] <= zone["high"] for b in since) if side=="support"
                      else any(b["high"] >= zone["low"] for b in since))
            if not tested:
                yield dict(kind="range_breakout", side=side, zone=zone, box_low=lo, box_high=hi,
                           box_start=box[0]["date"], box_end=box[-1]["date"], box_bars=size,
                           breakout=br["date"], breakout_close=br["close"], prebreak_atr=atr,
                           invalidation=lo if side=="support" else hi)
            break  # A breakout belongs to the longest qualified platform on that date.


def freeze(history, dates, sessions, atr, daily_atr):
    anchor = history[-1]
    hourly, covered = hourly_bars(dates[-20:], sessions)
    weeks = weekly_bars(history[-810:])
    candidates = list(range_breakout_candidates(history[-45:], daily_atr))
    for bars, ready, half, kind in ((hourly, covered>=15,.10,"mirror_60m"),
                                     (weeks, len(weeks)>=52,.20,"mirror_week")):
        if ready:
            candidates.extend(mirror_candidates(bars, half*atr, kind, anchor["close"], atr))
    nominees = {model:{} for model in ("range_breakout","mirror","combined")}
    for side in SIDES:
        ranked = []
        for c in candidates:
            if c.get("side", side) != side:
                continue
            dist = opening_distance(side,c["zone"],anchor["close"])
            if not 0 < dist <= MAX_DISTANCE_ATR*atr:
                continue
            kind = c["kind"]
            priority = ("range_breakout","mirror_60m","mirror_week").index(kind)
            ranked.append(((dist, priority, -c.get("balance",0), -int(c.get("breakout","0000-00-00").replace("-",""))), c))
        for _, cand in sorted(ranked,key=lambda x:x[0]):
            model = "range_breakout" if cand["kind"]=="range_breakout" else "mirror"
            if side in nominees[model]:
                continue
            c = dict(cand)
            c.setdefault("invalidation",c["zone"]["low" if side=="support" else "high"])
            nominees[model][side] = c
            nominees["combined"].setdefault(side,c)
    return dict(as_of=anchor["date"], anchor_close=anchor["close"], atr=atr, nominees=nominees,
                hourly_days=covered, weekly_bars=len(weeks), hourly_ready=covered>=15,
                weekly_ready=len(weeks)>=52)


def close_response(hour, side, zone, invalidation, atr, session):
    if hour["status"] not in OUTCOMES:
        return None
    sign = 1 if side=="support" else -1
    near = zone["high" if side=="support" else "low"]
    far = zone["low" if side=="support" else "high"]
    status, first_at, structural_at = "timeout", None, None
    for t in range(hour["touch_at"],hour["window_end"],STEP):
        close = session["closes"][t]
        if structural_at is None and sign*(close-invalidation) < 0:
            structural_at = t+STEP
        if first_at is None:
            if sign*(close-far) < 0:
                status, first_at = "invalid", t+STEP
            elif sign*(close-near) >= RESPONSE_ATR*atr:
                status, first_at = "reaction", t+STEP
    return {"status":status,"at":first_at,"structural_invalid_at":structural_at}


def score_choice(candidate, side, bar, atr, session, legacy=None):
    if legacy is None:
        selected = bool(candidate and opening_distance(side,candidate["zone"],bar["open"]) > 0)
        choice = {"layer":"primary" if selected else None}
        if selected:
            choice["zone"] = candidate["zone"]
    else:
        choice = legacy
        selected = bool(choice["layer"])
    hour = (legacy["hour"] if legacy is not None else measure(choice,side,atr,session,bar["complete"]))
    out = {"nominated":bool(candidate) if legacy is None else selected,
           "selected":selected,"hour":hour,"response":None,"excursion":None}
    if not selected:
        return out
    zone = choice["zone"]
    boundary = candidate["invalidation"] if candidate else zone["low" if side=="support" else "high"]
    out.update(kind=candidate["kind"] if candidate else "legacy", zone=zone,
               daily=observe(side,zone,atr,bar), excursion=excursions(hour,side,atr,session),
               response=close_response(hour,side,zone,boundary,atr,session))
    return out


def summarize(events):
    stats = {}
    for model in MODELS:
        by_side = {}
        for side in (*SIDES,"both"):
            choices = [c for e in events for key,c in e["models"][model].items() if side in (key,"both")]
            hours = Counter(c["hour"]["status"] for c in choices)
            scored = [c for c in choices if c["response"] is not None]
            reactions = Counter(c["response"]["status"] for c in scored)
            daily = [c["daily"] for c in choices if c["selected"]]
            def middle(field):
                return median(c["excursion"][field] for c in scored) if scored else None
            by_side[side] = dict(evaluated=len(scored), reaction=reactions["reaction"], invalid=reactions["invalid"],
                                 unresolved=reactions["timeout"], structural_invalid=sum(c["response"]["structural_invalid_at"] is not None for c in scored),
                                 half={k:hours[k] for k in OUTCOMES}, excluded={k:v for k,v in hours.items() if k not in OUTCOMES},
                                 nominated=sum(c["nominated"] for c in choices), selected=len(daily), touched=sum(o["touch"] for o in daily), breached=sum(o["breach"] for o in daily),
                                 median_favourable=middle("favourable_points"), median_adverse=middle("adverse_points"))
        by_side["abstain_days"] = sum(not any(c["selected"] for c in e["models"][model].values()) for e in events)
        stats[model] = by_side
    return stats


def build(watch, data, sessions, daily_atr):
    history = data.get("reaction_history", data["completed"])
    dates = [b["date"] for b in history]
    old_events = {e["date"]:e for e in watch["events"]}
    if watch["latest_session"]:
        e = watch["latest_session"]
        old_events[e["date"]] = e
    last_bar = data["bars"][-1]
    backlog = history[-watch["requested_days"]:]
    pending = not last_bar["complete"] and last_bar.get("opening_available",True)
    records = backlog + ([last_bar] if pending else [])
    events, latest, current_plan = [], None, None
    for bar in records:
        stop = bisect_left(dates,bar["date"])
        if stop < 15:
            continue
        atr = daily_atr(history[stop-15:stop])
        if atr <= 0:
            continue
        old = old_events.get(bar["date"])
        plan = freeze(history[:stop],dates[:stop],sessions,atr,daily_atr)
        models = {}
        for model in ("range_breakout","mirror"):
            models[model] = {side:score_choice(plan["nominees"][model].get(side),side,bar,atr,sessions.get(bar["date"])) for side in SIDES}
        models["combined"] = {}
        for side in SIDES:
            main = plan["nominees"]["combined"].get(side)
            kind = "range_breakout" if main and main["kind"]=="range_breakout" else "mirror"
            models["combined"][side] = models[kind][side]
        if old:
            models["legacy"] = {side:score_choice(None,side,bar,atr,sessions.get(bar["date"]),old["selected"][side]) for side in SIDES}
            for side in SIDES:
                models["legacy"][side]["nominated"] = side in old["plan"]["zones"]
        else:
            models["legacy"] = {side:{"nominated":False,"selected":False,"hour":{"status":"missing_legacy_history"},
                                      "response":None,"excursion":None} for side in SIDES}
        event = dict(date=bar["date"],open=bar["open"],complete=bar["complete"],
                     regime=old["plan"]["regime"] if old else None,plan=plan,models=models)
        if bar["complete"]:
            events.append(event)
        if bar["date"] == last_bar["date"]:
            latest = event
            if pending:
                current_plan = plan
    if current_plan is None and len(history)>=15:
        atr = daily_atr(history[-15:])
        if atr > 0:
            current_plan = freeze(history,dates,sessions,atr,daily_atr)
    after = [e for e in events if e["date"]>FROZEN_ON]
    return dict(role="primary_hsi_reversal", rule_date=FROZEN_ON, response_atr=RESPONSE_ATR, events=events,
                latest_session=latest, next_plan=current_plan, stats=summarize(events),
                skipped_warmup_days=len(backlog)-len(events),
                after_rule={"days":len(after),"stats":summarize(after)},
                hourly_ready_days=sum(e["plan"]["hourly_ready"] for e in events),
                weekly_ready_days=sum(e["plan"]["weekly_ready"] for e in events))
