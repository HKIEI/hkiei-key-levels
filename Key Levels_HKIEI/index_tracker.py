"""Point-in-time identification and observation of cash-index price zones."""
import datetime as dt
import math
from collections import defaultdict
from statistics import mean
from zoneinfo import ZoneInfo

from intraday_reactions import STEP, read_sessions

INDICES = {"^HSI": "恒生指數", "^DJI": "道瓊斯工業平均指數", "^GSPC": "標普 500 指數", "^IXIC": "納斯達克綜合指數"}
PERIODS = {"1d": (10, 22, 50, 100, 250), "5m": (10, 20, 58, 100, 250)}
SIDES = {1: "support", -1: "resistance", 0: "range"}
TYPES = ("range_breakout", "horizontal", "directional", "average")
MIN_BARS = {"range_breakout": 14, "horizontal": 14, "directional": 29, "average": 19}


def evidence(bar, role, price):
    return {"bar": bar["start"], "role": role, "price": price}


def outside(price, level, half, side):
    return side * (price - level) > half


def reaches(bar, level, half):
    return bar["low"] <= level + half and bar["high"] >= level - half


class Scanner:
    """Consume one already-closed candle at a time. Only emitted events change status."""

    def __init__(self, symbol, frame):
        self.symbol, self.frame = symbol, frame
        self.periods = PERIODS[frame]
        self.bars, self.ranges, self.atrs, self.directions = [], [], [], []
        self.ema = {p: [] for p in self.periods}
        self.live, self.finished = {}, []
        self.taps = defaultdict(list)
        self.swings = {1: [], -1: []}
        self.segment_start = 0
        self.last_breakout = -1
        self.directional_seeds = set()

    def indicators(self, bar):
        i = len(self.bars) - 1
        previous = self.bars[max(0, i - 1)]["close"]
        true_range = max(bar["high"] - bar["low"], abs(bar["high"] - previous), abs(bar["low"] - previous))
        self.ranges.append(true_range)
        self.atrs.append(mean(self.ranges[-14:]) if i >= 13 else None)
        for p, values in self.ema.items():
            if i + 1 < p:
                values.append(None)
            elif i + 1 == p:
                values.append(mean(b["close"] for b in self.bars[-p:]))
            else:
                values.append(values[-1] + 2 / (p + 1) * (bar["close"] - values[-1]))
        fast, slow = self.ema[10][-1], self.ema[self.periods[1]]
        side = 0
        if i >= self.periods[1] + 4:
            delta = slow[-1] - slow[-6]
            for s in (1, -1):
                if (s * (bar["close"] - slow[-1]) > 0 and s * (fast - slow[-1]) > 0
                        and s * delta >= .15 * self.atrs[-1] and s * delta > 0):
                    side = s
        self.directions.append(side)

    def new(self, typ, subtype, side, level, half, proof, status, identity, **extra):
        b = self.bars[-1]
        key = "|".join((self.symbol, self.frame, subtype, identity))
        if key in self.live:
            return self.live[key]
        row = dict(id=key, type=typ, subtype=subtype, timeframe=self.frame, side=SIDES[side],
                   level=level, zone_low=level-half, zone_high=level+half, formed_at=b["end"],
                   as_of=b["end"], evidence=proof, status=status, active=True, events=[],
                   first_touch_at=None, first_breach_at=None, first_reclaim_at=None, **extra)
        row["_sign"], row["_born"], row["_last"], row["_used"] = side, len(
            self.bars)-1, len(self.bars)-1, False
        self.live[key] = row
        self.event(row, status, b, level)
        return row

    def event(self, row, status, b, price, **values):
        row["status"], row["_last"] = status, len(self.bars)-1
        row["events"].append(dict(status=status, at=b["end"], bar=b["start"], price=price, **values))

    def close(self, row, status=None):
        if status:
            self.event(row, status, self.bars[-1], self.bars[-1]["close"])
        row["active"] = False
        row["ended_at"] = row["events"][-1]["at"]
        self.finished.append(self.live.pop(row["id"]))

    def zone(self, row, center, half):
        row.update(level=center, zone_low=center-half, zone_high=center+half)

    def neighbors(self, subtype, level, half, side=None):
        return [r for r in self.live.values() if r["subtype"] == subtype
                and (side is None or r["_sign"] == side)
                and r["zone_low"]-half <= level <= r["zone_high"]+half]

    def box(self, i):
        available = i - self.segment_start + 1 if self.frame == "5m" else i+1
        atr = self.atrs[i]
        for size in range(min(available, 20), 4, -1):
            bars = self.bars[i-size+1:i+1]
            low, high = min(b["low"] for b in bars), max(b["high"] for b in bars)
            width = high-low
            if (not 0 < width <= 4*atr or (self.frame == "1d" and width >= .10*low)
                    or abs(bars[-1]["close"]-bars[0]["close"]) > .5*width):
                continue
            if sum(b["low"] <= low+.25*width for b in bars) < 2 or sum(b["high"] >= high-.25*width for b in bars) < 2:
                continue
            visits = []
            for b in bars:
                end = 1 if b["close"] >= high-.35*width else -1 if b["close"] <= low+.35*width else 0
                if end and (not visits or visits[-1] != end):
                    visits.append(end)
            kind = None
            if size >= 8 and len(visits) >= 4:
                kind = "steak"
            else:
                spans = sum(b["low"] <= low+.35*width and b["high"] >= high-.35*width for b in bars)
                if width <= 2*atr and spans >= math.ceil(.60*size) and len(visits) <= 2:
                    kind = "sausage"
            if kind:
                return kind, bars, low, high
        return None

    def platform(self, i):
        if any(r["status"] == "platform" for r in self.live.values()):
            return
        result = self.box(i)
        if result is None:
            return
        kind, bars, low, high = result
        # Keep one physical unbroken box per frame. Do not reuse the same source after a breakout.
        if self.last_breakout >= i-len(bars)+1:
            return
        proof = [evidence(min(bars, key=lambda b: b["low"]), "box_low", low),
                 evidence(max(bars, key=lambda b: b["high"]), "box_high", high)]
        self.new("range_breakout", kind, 0, (high+low)/2, (high-low)/2, proof, "platform", bars[0]["start"],
                 box_low=low, box_high=high, box_start=bars[0]["start"], box_end=bars[-1]["end"], box_bars=len(bars))

    def update_platform(self, row, b, i):
        if row["status"] != "platform":
            self.update_retest(row, b, i)
            return
        side = 1 if b["close"] > row["box_high"] else -1 if b["close"] < row["box_low"] else 0
        if side:
            self.last_breakout = i
            edge = row["box_high" if side == 1 else "box_low"]
            row.update(side=SIDES[side], _sign=side, _born=i, breakout_at=b["end"],
                       structural_level=row["box_low" if side == 1 else "box_high"],
                       directional_body=side*(b["close"]-b["open"]) > 0)
            self.zone(row, edge, .10*self.atrs[i-1])
            self.event(row, "breakout", b, b["close"])
        elif i-row["_born"] >= 60:
            self.close(row, "expired")

    def update_retest(self, row, b, i):
        s = row["_sign"]
        lo, hi = row["zone_low"], row["zone_high"]
        far = lo if s == 1 else hi
        if not row["_used"]:
            skipped = s*(b["open"]-far) < 0
            touched = reaches(b, row["level"], (hi-lo)/2)
            if skipped:
                row["_used"] = True
                if row["first_breach_at"] is None:
                    row["first_breach_at"] = b["end"]
                self.event(row, "gap_across", b, b["open"])
            elif touched:
                row["_used"] = True
                row["first_touch_at"] = b["end"]
                self.event(row, "first_retest", b, row["level"])
        extreme = b["low" if s == 1 else "high"]
        if row["_used"] and s*(extreme-far) < 0 and row["first_breach_at"] is None:
            row["first_breach_at"] = b["end"]
            self.event(row, "pierced", b, extreme)
        if row["_used"] and s*(b["close"]-far) < 0:
            if row.get("_position") != "through":
                row.setdefault("first_close_through_at", b["end"])
                self.event(row, "closed_through", b, b["close"])
            row["_position"] = "through"
        elif row["first_breach_at"] and s*(b["close"]-row["level"]) > 0:
            if row.get("_position") != "recovered":
                if row["first_reclaim_at"] is None:
                    row["first_reclaim_at"] = b["end"]
                self.event(row, "reclaimed", b, b["close"])
            row["_position"] = "recovered"
        if s*(extreme-row["structural_level"]) < 0:
            self.close(row, "structure_broken")
        elif i-row["_born"] >= 20:
            self.close(row, "expired")

    def pivots(self, i):
        if i < 4 or (self.frame == "5m" and i-self.segment_start < 4):
            return
        p = i-2
        for s, k in ((1, "low"), (-1, "high")):
            if all(s*(self.bars[j][k]-self.bars[p][k]) > 0 for j in (p-2, p-1, p+1, p+2)):
                self.swings[s].append(p)

    def multiples(self, i):
        b = self.bars[i]
        atr = self.atrs[i]
        for s, k in ((1, "low"), (-1, "high")):
            pairs = []
            if (self.frame == "1d" or i > self.segment_start) and abs(b[k]-self.bars[i-1][k]) <= .20*atr:
                pairs.append((i-1, i))
            pivots = self.swings[s]
            if pivots and pivots[-1] == i-2:
                p = pivots[-1]
                for q in reversed(pivots[:-1]):
                    if p-q > 30 or (self.frame == "5m" and q < self.segment_start):
                        break
                    if abs(self.bars[p][k]-self.bars[q][k]) <= .20*atr:
                        pairs.append((q, p))
                        break
            for q, p in pairs:
                vals = [self.bars[j][k] for j in (q, p)]
                low, high = sorted(vals)
                z = (low+high)/2
                half = (high-low)/2
                outer = low if s == 1 else high
                if s*(b["close"]-z) <= 0 or any(s*(self.bars[j][k]-outer) < 0 for j in range(q+1, i+1)):
                    continue
                if any(r["status"] == "platform" and r["box_start"] <= self.bars[q]["start"]
                       and abs(outer-r["box_low" if s == 1 else "box_high"]) <= .20*atr for r in self.live.values()):
                    continue
                if self.neighbors("multiple", z, .20*atr, s):
                    continue
                proof = [evidence(self.bars[j], k, self.bars[j][k]) for j in (q, p)]
                self.new("horizontal", "multiple", s, z, half, proof, "watch_sweep", self.bars[q]["start"]+"/"+k+"/"+self.bars[p]["start"],
                         outer=outer, trend_at_formation=self.directions[i])

    def update_multiple(self, r, b, i):
        s = r["_sign"]
        extreme = b["low" if s == 1 else "high"]
        if r["first_breach_at"] is None:
            if reaches(b, r["level"], (r["zone_high"]-r["zone_low"])/2) and r["first_touch_at"] is None:
                r["first_touch_at"] = b["end"]
                self.event(r, "first_retest", b, r["level"])
            if s*(extreme-r["outer"]) < 0:
                r["first_breach_at"] = b["end"]
                self.event(r, "swept", b, extreme, trend_aligned=(self.directions[i-1] == s))
        if r["first_breach_at"] and s*(b["close"]-r["level"]) > 0:
            r["first_reclaim_at"] = b["end"]
            self.event(r, "sweep_reclaimed", b, b["close"])
            self.close(r)
        elif i-r["_born"] >= 20:
            self.close(r, "expired")

    def mirror(self, i):
        b = self.bars[i]
        half = .10*self.atrs[i]
        for edge in ("low", "high"):
            level = b[edge]
            side = 1 if b["close"] > level+half else -1 if b["close"] < level-half else 0
            if not side or self.neighbors("mirror", level, half):
                continue
            # The bar that completes the two-sided evidence must have a genuine rejection.
            span = b["high"]-b["low"]
            away = min(b["open"], b["close"])-level - \
                half if edge == "low" else level-half-max(b["open"], b["close"])
            if span <= 0 or away < .25*span:
                continue
            matches = {1: [], -1: []}
            proof = []
            for j in range(max(0, i-79), i+1):
                v = self.bars[j]
                width = v["high"]-v["low"]
                if width <= 0:
                    continue
                for s, k in ((1, "low"), (-1, "high")):
                    if abs(v[k]-level) > half or (matches[s] and j-matches[s][-1] < 3):
                        continue
                    dist = min(v["open"], v["close"])-level - \
                        half if s == 1 else level-half-max(v["open"], v["close"])
                    if dist >= .25*width:
                        matches[s].append(j)
                        proof.append(evidence(v, "held_above" if s == 1 else "rejected_below", v[k]))
            if min(map(len, matches.values())) >= 2 and i in (matches[1][-1], matches[-1][-1]):
                self.new("horizontal", "mirror", side, level, half, proof, "mirror_ready", b["start"]+"/"+edge,
                         approach=side, retesting=False)

    def update_mirror(self, r, b, i):
        lo, hi = r["zone_low"], r["zone_high"]
        z = r["level"]
        s = r["approach"]
        near = hi if s == 1 else lo
        far = lo if s == 1 else hi
        touch = reaches(b, z, (hi-lo)/2)
        if touch and not r["retesting"]:
            r["retesting"] = True
            if r["first_touch_at"] is None:
                r["first_touch_at"] = b["end"]
            self.event(r, "mirror_test", b, z, from_side=SIDES[s])
        if s*(b["low" if s == 1 else "high"]-far) < 0 and r["first_breach_at"] is None:
            r["first_breach_at"] = b["end"]
            r["_first_breach_side"] = s
            self.event(r, "pierced", b, b["low" if s == 1 else "high"])
        if s*(b["close"]-far) < 0:
            self.event(r, "crossed_to_other_side", b, b["close"], from_side=SIDES[s])
            r.update(approach=-s, _sign=-s, side=SIDES[-s], retesting=False)
        elif r["retesting"] and s*(b["close"]-near) > 0:
            self.event(r, "held_from_above" if s == 1 else "rejected_from_below", b, b["close"])
            r["retesting"] = False
        if r["first_breach_at"] and r["first_reclaim_at"] is None and r["_first_breach_side"]*(b["close"]-z) > 0:
            r["first_reclaim_at"] = b["end"]
            self.event(r, "reclaimed", b, b["close"])
        if i-r["_last"] >= 80:
            self.close(r, "expired")

    def reversals(self, i):
        if i < 5 or (self.frame == "5m" and i-self.segment_start < 4):
            return
        b = self.bars[i]
        s = self.directions[i]
        if not s:
            return
        for kind, p in (("momentum", i-1), ("wave", i-2)):
            v = self.bars[p]
            key = (s, p)
            if key in self.directional_seeds or self.directions[p-1] != s:
                continue
            body = s*(v["open"]-v["close"])
            span = v["high"]-v["low"]
            if body <= .10*span or not outside(b["close"], v["open"], .10*self.atrs[i], s):
                continue
            if kind == "wave":
                if not self.swings[s] or self.swings[s][-1] != p:
                    continue
            else:
                pa = self.atrs[p-1]
                if (pa is None or body > .60*pa or s*(b["close"]-b["open"]) < max(2*body, .80*pa)
                        or s*(b["close"]-v["high" if s == 1 else "low"]) <= 0
                        or sum(s*(j["close"]-j["open"]) > 0 for j in self.bars[p-3:p]) < 2):
                    continue
            proof = [evidence(v, "counter_open", v["open"]), evidence(b, "confirmation_close", b["close"])]
            self.new("directional", kind, s, v["open"], .10*self.atrs[i], proof, "await_retest", v["start"],
                     structural_level=v["low" if s == 1 else "high"])
            self.directional_seeds.add(key)

    def lifelines(self, i):
        b = self.bars[i]
        prev = self.bars[i-1]
        atr = self.atrs[i-1]
        if not atr:
            return
        half = .10*atr
        for p in self.periods:
            ma = self.ema[p]
            if i < p+5:
                continue
            level = ma[i-1]
            slope = level-ma[i-6]
            next_tilt = ma[i]-ma[i-5]
            for s in (1, -1):
                key = p, s
                points = [entry for entry in self.taps[key] if i-entry[0] <= 40]
                current = next((r for r in self.live.values()
                               if r["type"] == "average" and r["period"] == p and r["_sign"] == s), None)
                wrong_close = outside(b["close"], level, half, -s)
                edge = b["low" if s == 1 else "high"]
                if current:
                    if (points and i-max(points[-1][0], current.get("_last_test", -3)) >= 3
                            and outside(prev["close"], level, half, s) and reaches(b, level, half)):
                        if not current["first_touch_at"]:
                            current["first_touch_at"] = b["end"]
                        current["_last_test"] = i
                        self.event(current, "ema_test", b, level, level_as_of=prev["end"])
                    if s*(edge-level) < -half and current["first_breach_at"] is None:
                        current["first_breach_at"] = b["end"]
                        self.event(current, "pierced", b, edge)
                    if current["first_breach_at"] and current["first_reclaim_at"] is None and s*(b["close"]-level) > 0:
                        current["first_reclaim_at"] = b["end"]
                        self.event(current, "reclaimed", b, b["close"])
                    if wrong_close or s*next_tilt < .30*self.atrs[i] or not points or i-points[-1][0] > 16:
                        self.close(current, "closed_through" if wrong_close else "lifeline_inactive")
                        current = None
                if wrong_close or s*next_tilt <= 0:
                    self.taps[key] = []
                    continue
                good = (s*slope >= .30*atr and outside(prev["close"], level, half, s)
                        and not outside(b["open"], level, half, -s) and abs(edge-level) <= half
                        and s*(b["close"]-level) >= .25*atr)
                if good and (not points or i-points[-1][0] >= 3):
                    points.append((i, level))
                self.taps[key] = points
                if (not current and len(points) >= 2 and points[-1][0] == i and s*next_tilt >= .30*self.atrs[i]):
                    proof = [evidence(self.bars[j], "ema_reaction", value) for j, value in points]
                    current = self.new("average", "ema", s, ma[i], .10*self.atrs[i], proof, "lifeline_ready",
                                       str(p)+"/"+str(s)+"/"+b["start"], period=p, formed_level=ma[i])
                if current:
                    self.zone(current, ma[i], .10*self.atrs[i])
                    current["as_of"] = b["end"]
                    current["level_as_of"] = b["end"]
                    current["previous_ema"] = level

    def append(self, b):
        i = len(self.bars)
        if i and self.frame == "5m" and b["session"] != self.bars[-1]["session"]:
            self.segment_start = i
        self.bars.append(b)
        self.indicators(b)
        self.pivots(i)
        if not self.atrs[i]:
            return
        for row in list(self.live.values()):
            row["as_of"] = b["end"]
            if row["type"] == "range_breakout":
                self.update_platform(row, b, i)
            elif row["subtype"] == "multiple":
                self.update_multiple(row, b, i)
            elif row["subtype"] == "mirror":
                self.update_mirror(row, b, i)
            elif row["type"] == "directional":
                if self.directions[i-1] == row["_sign"]:
                    self.update_retest(row, b, i)
                if row["id"] in self.live and self.directions[i] != row["_sign"]:
                    self.close(row, "trend_ended")
        self.platform(i)
        self.multiples(i)
        self.mirror(i)
        self.reversals(i)
        self.lifelines(i)
        if b.get("ends_segment"):
            for row in list(self.live.values()):
                if row["type"] in ("range_breakout", "directional") or row["subtype"] == "multiple":
                    self.close(row, "session_ended")

    def finish_gap(self, missing_start, known_at):
        for row in list(self.live.values()):
            self.event(row, "data_gap", {"start": missing_start, "end": known_at}, None)
            row["as_of"] = known_at
            self.close(row)

    def records(self):
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in
                list(self.live.values())+self.finished]


def frame_result(rows, bars_count, last, since, quality, coverage):
    groups = {}
    for typ in TYPES:
        active = [r for r in rows if r["type"] == typ and r["active"]]
        past = [r for r in rows if r["type"] == typ and not r["active"]
                and r["events"][-1]["at"][:10] >= since]
        active.sort(key=lambda r: (r["formed_at"], r["id"]), reverse=True)
        past.sort(key=lambda r: (r["events"][-1]["at"], r["id"]), reverse=True)
        state = "tracking" if active else "insufficient" if bars_count < MIN_BARS[typ] or quality in (
            "missing", "stale", "gap") else "none"
        groups[typ] = dict(state=state, active=active, recent=past)
    return dict(as_of=last, coverage=coverage, quality=quality, bars_since_gap=bars_count, types=groups)


def read_intraday(data, symbol, history, since):
    raw = data.get("intraday_chart")
    empty = dict(full_sessions=0, partial_sessions=[], gaps=[], last_complete_bar=None)
    if not raw:
        return frame_result([], 0, None, since, "missing", empty), []
    envelope = raw["chart"]
    if envelope.get("error") or not envelope.get("result"):
        raise ValueError("五分鐘走勢缺少有效回應。")
    chart = envelope["result"][0]
    meta = chart["meta"]
    if (meta.get("symbol", "").upper() != symbol or meta.get("dataGranularity") != "5m"
            or meta.get("exchangeTimezoneName") != data["timezone"] or meta.get("instrumentType") != "INDEX"):
        raise ValueError("五分鐘走勢的現貨指數、周期或市場時區不符。")
    zone = ZoneInfo(data["timezone"])
    clock = dt.datetime.fromisoformat(data["as_of_utc"]).timestamp()
    ticks = sorted(t for t in chart.get("timestamp", []) if isinstance(
        t, (float, int)) and math.isfinite(t) and 0 < t <= clock)
    if not ticks:
        return frame_result([], 0, None, since, "missing", empty), []

    def market_date(t): return dt.datetime.fromtimestamp(t, zone).date().isoformat()
    def moment(t): return dt.datetime.fromtimestamp(t, zone).isoformat(timespec="minutes")
    quote = meta.get("regularMarketTime")
    # Historical sessions use full bars. A delayed live quote sets the last usable close.
    observed = min(clock, quote) if isinstance(quote, (int, float)
                                               ) and math.isfinite(quote) and quote > 0 else clock
    first = market_date(ticks[0])
    dates = sorted({market_date(t) for t in ticks} |
                   {b["date"] for b in data["bars"] if b["date"] >= first and b["timestamp"] <= clock})
    sessions = read_sessions(raw, symbol, data["timezone"], dates, clock)
    rows = []
    scanner = Scanner(symbol, "5m")
    coverage = empty
    last = None
    quality = "missing"
    last_segment = []
    last_known_day = history[-1]["date"]

    def gap(slot):
        nonlocal scanner
        if scanner.bars:
            scanner.finish_gap(moment(slot), moment(slot+STEP))
            rows.extend(scanner.records())
            scanner = Scanner(symbol, "5m")
    for day in dates:
        sess = sessions[day]
        slots = sess["slots"]
        if not slots or slots[0]+STEP > clock:
            continue
        if market_date(observed) < day:
            gap(slots[0])
            quality = "stale"
            coverage["gaps"].append(day)
            last_segment = []
            continue
        expected = [t for t in slots if t+STEP <= observed]
        if not expected:
            continue
        found_gap = False
        last_segment = []
        for k, t in enumerate(expected):
            if t not in sess["candles"] or t in sess["bad_slots"]:
                gap(t)
                found_gap = True
                quality = "gap"
                last_segment = []
                continue
            o, h, l = sess["candles"][t]
            segment = day + \
                ("/AM" if (data["timezone"] ==
                 "Asia/Hong_Kong" and dt.datetime.fromtimestamp(t, zone).hour < 12) else "/regular")
            b = dict(open=o, high=h, low=l, close=sess["closes"][t], start=moment(t), end=moment(t+STEP),
                     session=segment, ends_segment=(k+1 == len(slots) or slots[k+1] != t+STEP))
            scanner.append(b)
            if day > last_known_day:
                last_segment.append(b)
            last = b["end"]
            quality = "ok"
        if found_gap:
            coverage["gaps"].append(day)
            last_segment = []
            quality = "gap"
        if len(expected) < len(slots):
            coverage["partial_sessions"].append(day)
            if quality == "ok":
                quality = "partial"
        elif not found_gap:
            coverage["full_sessions"] += 1
    rows.extend(scanner.records())
    coverage["last_complete_bar"] = last
    return frame_result(rows, len(scanner.bars), last, since, quality, coverage), last_segment


def live_daily_observations(frame, bars):
    # Intraday contact with a pre-existing daily level is provisional until the daily close.
    if not bars:
        return
    for group in frame["types"].values():
        for r in group["active"]:
            lo, hi = r["zone_low"], r["zone_high"]
            obs = dict(through=bars[-1]["end"])
            for b in bars:
                if r["side"] == "range":
                    if b["low"] < lo:
                        obs.setdefault("below_platform", b["end"])
                    if b["high"] > hi:
                        obs.setdefault("above_platform", b["end"])
                else:
                    s = 1 if r["side"] == "support" else -1
                    if reaches(b, r["level"], (hi-lo)/2):
                        obs.setdefault("touched", b["end"])
                    if (b["low"] < lo if s == 1 else b["high"] > hi):
                        obs.setdefault("pierced", b["end"])
                    if "pierced" in obs and outside(b["close"], r["level"], 0, s):
                        obs.setdefault("back_across_center", b["end"])
            if len(obs) > 1:
                r["intraday_observation"] = obs


def completed_index_history(data, symbol):
    if symbol not in INDICES:
        raise ValueError("形態追蹤只適用於四個指定現貨指數。")
    zone = "Asia/Hong_Kong" if symbol == "^HSI" else "America/New_York"
    if data["timezone"] != zone or data["meta"].get("symbol") != symbol:
        raise ValueError("現貨指數代號或市場時區與走勢不符。")
    if data["meta"].get("instrumentType") not in (None, "INDEX"):
        raise ValueError("走勢並非指定現貨指數。")
    history = data.get("reaction_history") or data["completed"]
    history = [b for b in history if b.get("complete", True)]
    if not history:
        raise ValueError("沒有已收市天線。")
    return history


def track_index(data, symbol, history_days=30):
    if not 1 <= history_days <= 252:
        raise ValueError("追蹤交易日數須為 1 至 252。")
    history = completed_index_history(data, symbol)
    since = history[-min(history_days, len(history))]["date"]
    days = Scanner(symbol, "1d")
    for b in history:
        days.append(dict(b, start=b["date"], end=b["date"]))
    daily = frame_result(days.records(), len(history),
                         history[-1]["date"], since, "ok", dict(sessions=len(history)))
    note = None
    try:
        minute, overlay = read_intraday(data, symbol, history, since)
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, OSError) as error:
        if data.get("intraday_explicit"):
            raise ValueError("指定的五分鐘走勢無法核對。"+str(error)) from error
        note = "五分鐘走勢無法核對。"+str(error)
        minute, overlay = frame_result([], 0, None, since, "missing", {}), []
    live_daily_observations(daily, overlay)
    return dict(symbol=symbol, name=INDICES[symbol], timezone=data["timezone"], as_of_utc=data["as_of_utc"],
                reference_price=data.get("price", {}).get("value"), history_from=since,
                daily=daily, intraday=minute, intraday_note=note,
                synthetic=bool(data["meta"].get("synthetic")), warnings=list(data.get("warnings", [])))
