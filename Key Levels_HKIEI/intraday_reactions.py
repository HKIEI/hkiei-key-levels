"""One-hour first-move measurement from complete Yahoo regular-session five-minute bars."""
import datetime as dt
import math
from collections import Counter, defaultdict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reaction_watch import MOVE_ATR, RULE_FIXED_ON, layer_groups, opening_distance

STEP = 300
WINDOW = 12
OUTCOMES = ("favourable", "adverse", "timeout", "ambiguous")
LABELS = {
    "favourable": "先順向", "adverse": "先逆向", "timeout": "兩邊未到", "ambiguous": "同燭先後不明",
    "not_selected": "開市沒有合資格位置", "missing_session": "欠完整五分鐘走勢",
    "opening_disagreement": "兩種走勢的開市方向不一致", "jumped_past_first": "首次觸及前跳過整區",
    "short_window": "午休或收市前不足一小時", "no_observed_touch": "未見觸及",
    "invalid_atr": "上日波幅無法評分", "pending": "交易日進行中", "unsupported_session": "未能核實連續交易時段",
}


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def regular_periods(raw, tz):
    """Extract provider-declared regular sessions only. Preserve separate segments."""
    if isinstance(raw, dict):
        raw = raw.get("regular", [])
    periods = defaultdict(list)
    def collect(node):
        if isinstance(node, list):
            for child in node:
                collect(child)
        elif isinstance(node, dict):
            start, end = node.get("start"), node.get("end")
            if positive(start) and positive(end) and start < end:
                local = dt.datetime.fromtimestamp(start, tz)
                if dt.datetime.fromtimestamp(end-1, tz).date() == local.date():
                    periods[local.date().isoformat()].append((int(start), int(end)))
    collect(raw)
    return periods


def expected_slots(date, tz, instrument, periods):
    day = dt.date.fromisoformat(date)
    midnight = dt.datetime.combine(day, dt.time(), tzinfo=tz)
    def stamp(minutes):
        return int((midnight + dt.timedelta(minutes=minutes)).timestamp())
    kind = instrument in ("INDEX", "EQUITY", "ETF", "MUTUALFUND")
    if str(tz) == "Asia/Hong_Kong" and kind:
        official = [(stamp(570), stamp(720)), (stamp(780), stamp(960))]
    elif str(tz) == "America/New_York" and kind:
        official = [(stamp(570), stamp(960))]
    elif instrument == "CRYPTOCURRENCY" and str(tz) in ("UTC", "Etc/UTC", "GMT"):
        official = [(stamp(0), stamp(1440))]
    else:
        # Unverified single spans can conceal lunch, auctions or an overnight session.
        return []
    if not periods:
        periods = official
    slots = set()
    for a, b in official:
        for start, end in periods:
            lo, hi = max(start, a), min(end, b)
            if lo < hi and lo % STEP == 0:
                slots.update(range(lo, hi - STEP + 1, STEP))
    return sorted(slots)


def read_sessions(payload, symbol, tz_name, dates, cutoff):
    """Reject a damaged session rather than infer a first touch across a missing bar."""
    sessions = {}
    if payload is None:
        return sessions
    chart = payload["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError("五分鐘走勢缺少有效回應。")
    result = chart["result"][0]
    meta = result["meta"]
    if meta.get("symbol", "").upper() != symbol or meta.get("dataGranularity") != "5m":
        raise ValueError("五分鐘走勢的代號或時間間距不符。")
    if meta.get("exchangeTimezoneName") != tz_name:
        raise ValueError("五分鐘走勢的市場時區不符。")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("五分鐘走勢欠缺可核對的市場時區。")
    periods = regular_periods(meta.get("tradingPeriods"), tz)
    quote = result["indicators"]["quote"][0]
    fields = ("open", "high", "low", "close")
    if not all(isinstance(quote.get(k), list) for k in fields):
        raise ValueError("五分鐘走勢缺少開高低收欄位。")
    wanted = {}
    for date in dates:
        expected = expected_slots(date, tz, meta.get("instrumentType"), periods.get(date))
        for t in expected:
            wanted[t] = date
        sessions[date] = {"slots": expected, "candles": {}, "damaged": False, "bad_slots": set()}
        sessions[date]["closes"] = {}
    quote_time = meta.get("regularMarketTime")
    if positive(quote_time):
        cutoff = min(cutoff, quote_time)
    for i, t in enumerate(result.get("timestamp") or []):
        if not positive(t) or t + STEP > cutoff:
            continue
        slot = int(t // STEP) * STEP
        date = wanted.get(slot)
        if date is None:
            continue
        s = sessions[date]
        candles = s["candles"]
        if t != slot or slot in candles or any(i >= len(quote[k]) for k in fields):
            s["damaged"] = True
            s["bad_slots"].add(slot)
            continue
        op, hi, lo, cl = (quote[k][i] for k in fields)
        if not all(positive(v) for v in (op, hi, lo, cl)) or not lo <= min(op, cl) <= max(op, cl) <= hi:
            s["damaged"] = True
            s["bad_slots"].add(slot)
            continue
        candles[slot] = (op, hi, lo)
        s["closes"][slot] = cl
    return sessions


def complete(session):
    return bool(session and session["slots"] and not session["damaged"]
                and len(session["candles"]) == len(session["slots"]))


def first_direction(bars, origin, side, atr):
    lower, upper = origin - MOVE_ATR * atr, origin + MOVE_ATR * atr
    up, down = ("favourable", "adverse") if side == "support" else ("adverse", "favourable")
    for op, high, low in bars:
        if op >= upper:
            return up
        if op <= lower:
            return down
        if high >= upper and low <= lower:
            return "ambiguous"
        if high >= upper:
            return up
        if low <= lower:
            return down
    return "timeout"


def measure(choice, side, atr, session, closed):
    def result(status, **values):
        return dict(status=status, **values)
    if choice["layer"] is None:
        return result("not_selected")
    if not closed:
        return result("pending")
    if session is not None and not session["slots"]:
        return result("unsupported_session")
    if not complete(session):
        return result("missing_session")
    if not positive(atr):
        return result("invalid_atr")
    slots, candles, zone = session["slots"], session["candles"], choice["zone"]
    if opening_distance(side, zone, candles[slots[0]][0]) <= 0:
        return result("opening_disagreement")
    for i, t in enumerate(slots):
        op, hi, lo = candles[t]
        if (side == "support" and op < zone["low"]) or (side == "resistance" and op > zone["high"]):
            return result("jumped_past_first")
        if lo > zone["high"] or hi < zone["low"]:
            continue
        following = slots[i + 1:i + 1 + WINDOW]
        if len(following) != WINDOW or following[-1] != t + WINDOW * STEP:
            return result("short_window", touch_at=t)
        bars = [candles[slot] for slot in following]
        origin = bars[0][0]
        return result(first_direction(bars, origin, side, atr), touch_at=t, reference_at=following[0],
                      reference_price=origin, window_end=following[-1]+STEP)
    return result("no_observed_touch")


def tally(events):
    totals = layer_groups(lambda: dict(evaluated=0, **dict.fromkeys(OUTCOMES, 0)))
    excluded = Counter()
    for event in events:
        for side, choice in event["selected"].items():
            status = choice["hour"]["status"]
            if status not in OUTCOMES:
                excluded[status] += 1
                continue
            for layer in (choice["layer"], "combined"):
                for direction in (side, "both"):
                    totals[layer][direction]["evaluated"] += 1
                    totals[layer][direction][status] += 1
    return {"scores": totals, "excluded": dict(sorted(excluded.items()))}


def attach(watch, data, symbol):
    events = watch["events"]
    cutoff = dt.datetime.fromisoformat(data["as_of_utc"]).timestamp()
    dates = [e["date"] for e in events]
    if symbol == "^HSI":
        history = [b["date"] for b in data.get("reaction_history", data.get("completed", []))]
        days = watch.get("requested_days",len(dates))
        dates = history[-(days+20):] if history else dates
    try:
        sessions = read_sessions(data.get("intraday_chart"), symbol, data["timezone"], dates, cutoff)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, OSError) as error:
        if data.get("intraday_explicit"):
            raise ValueError("指定的五分鐘檔案無法使用。" + str(error)) from error
        sessions = {}
        data["warnings"].append("五分鐘走勢未能核實。" + str(error))
    latest = watch["latest_session"]
    records = events + ([latest] if latest and not latest["complete"] else [])
    for event in records:
        for side, choice in event["selected"].items():
            choice["hour"] = measure(choice, side, event["plan"]["atr"], sessions.get(event["date"]), event["complete"])
    future = [e for e in events if e["date"] > RULE_FIXED_ON]
    watch["hourly"] = dict(tally(events), full_sessions=sum(complete(sessions.get(e["date"])) for e in events),
                           requested_sessions=len(events), interval_minutes=5, window_minutes=60,
                           barrier_atr=MOVE_ATR, after_rule=dict(tally(future), days=len(future)),
                           by_regime={regime: tally([e for e in events if e["plan"]["regime"] == regime])
                                      for regime in ("normal", "high")})
    return sessions
