"""Does COV alone give you a trend database you can trust?

A synthetic BACnet site, driven for 24 hours, collected two ways:

  A  COV subscriptions only          - what "log what YABE gathers" amounts to
  B  COV plus a reconciliation poll  - the same stream with a slow ReadPropertyMultiple behind it

The failure modes injected are the ones the YABE README documents, not invented
ones: unconfirmed notifications can be dropped (README 2.6.3), subscriptions
lapse unless something renews them (README 2.6.2), and devices go quiet.

Ground truth is the simulator's own value at each minute, so coverage is
measured rather than asserted.
"""
import json, math, random
from collections import defaultdict

random.seed(20260408)
DAY = 24 * 60                      # minutes simulated
SUB_LIFETIME = 120                 # seconds - YABE's documented default
RENEW_EVERY = 90                   # collector renews before expiry
COV_LOSS = 0.03                    # unconfirmed notifications are UDP
RENEW_FAIL = 0.04                  # a renewal that doesn't land
POLL_EVERY = 10                    # minutes - reconciliation ReadPropertyMultiple
STALE_LIMIT = 15                   # minutes without data before a point counts as dark

# ---------------------------------------------------------------- the site
def pts(dev, kind, names, lo, hi, band, unit):
    return [{"device": dev, "type": kind, "name": n, "lo": lo, "hi": hi,
             "deadband": band, "unit": unit, "id": f"{dev}:{kind}:{i}"}
            for i, n in enumerate(names)]

POINTS = (
    pts("AHU-1", "ANALOG_INPUT", ["SupplyAirTemp", "ReturnAirTemp", "MixedAirTemp", "OutsideAirTemp"], 52, 78, 0.5, "degF")
  + pts("AHU-1", "ANALOG_OUTPUT", ["SupplyFanSpeed", "OADamperPos", "CoolingValvePos", "HeatingValvePos"], 0, 100, 2.0, "%")
  + pts("AHU-2", "ANALOG_INPUT", ["SupplyAirTemp", "ReturnAirTemp", "StaticPressure"], 52, 78, 0.5, "degF")
  + pts("AHU-2", "ANALOG_OUTPUT", ["SupplyFanSpeed", "OADamperPos"], 0, 100, 2.0, "%")
  + pts("CH-1", "ANALOG_INPUT", ["ChWSupplyTemp", "ChWReturnTemp", "CondenserTemp", "EvapPressure"], 40, 62, 0.4, "degF")
  + pts("CH-1", "ANALOG_VALUE", ["ChillerKW", "PercentLoad"], 0, 320, 1.5, "kW")
  + pts("VAV-201", "ANALOG_INPUT", ["ZoneTemp", "DischargeTemp", "AirflowCFM"], 66, 78, 0.3, "degF")
  + pts("VAV-201", "ANALOG_OUTPUT", ["DamperPos", "ReheatValve"], 0, 100, 2.0, "%")
  + pts("VAV-214", "ANALOG_INPUT", ["ZoneTemp", "DischargeTemp", "AirflowCFM"], 66, 78, 0.3, "degF")
  + pts("VAV-214", "ANALOG_OUTPUT", ["DamperPos", "ReheatValve"], 0, 100, 2.0, "%")
  + pts("MTR-MAIN", "ANALOG_INPUT", ["TotalKW", "PowerFactor", "VoltageAB"], 0, 480, 1.0, "kW")
)
DEVICES = sorted({p["device"] for p in POINTS})

def truth(p, t):
    """A plausible 24h trace.

    The important realism here is the plateaus. Overnight the plant is off and
    every output parks at 0 and genuinely does not move. A deadband-reported
    point that is not moving sends nothing - which is indistinguishable, at the
    collector, from a point that has died.
    """
    occupied = 390 <= t <= 1110
    if p["type"] == "ANALOG_OUTPUT" and not occupied:
        return 0.0                                   # parked, flat for hours
    if p["name"] in ("PowerFactor",):
        return round(0.94 + 0.01 * math.sin(t / 420), 2)
    if p["name"] in ("ChillerKW", "PercentLoad", "TotalKW") and not occupied:
        return round(p["lo"] + 0.04 * (p["hi"] - p["lo"]), 2)   # base load, steady
    occ = 1 / (1 + math.exp(-(t - 390) / 45)) - 1 / (1 + math.exp(-(t - 1110) / 55))
    base = (p["lo"] + p["hi"]) / 2
    span = (p["hi"] - p["lo"]) / 2
    seed = sum(ord(c) for c in p["id"])
    phase = (seed % 97) / 97 * math.tau
    slow = math.sin(t / 190 + phase) * 0.35
    drive = occ * 0.55 if p["type"] != "ANALOG_INPUT" else occ * 0.3
    noise = math.sin(t * 1.7 + phase) * 0.04 + random.gauss(0, 0.012)
    v = base + span * (slow + drive - 0.25 + noise)
    return round(max(p["lo"], min(p["hi"], v)), 2)

# one device drops off the network for a stretch, as they do
OUTAGE_DEV, OUTAGE = "VAV-214", range(602, 646)

# ---------------------------------------------------------------- collection
cov_events, poll_events = defaultdict(list), defaultdict(list)
sub_alive = {p["id"]: True for p in POINTS}
next_renew = {p["id"]: RENEW_EVERY for p in POINTS}
last_sent = {p["id"]: None for p in POINTS}
lapses = dropped = 0

for t in range(DAY):
    for p in POINTS:
        pid, v = p["id"], truth(p, t)
        offline = p["device"] == OUTAGE_DEV and t in OUTAGE

        # subscription upkeep, in seconds within this minute
        for sec in range(0, 60, 10):
            clock = t * 60 + sec
            if clock >= next_renew[pid]:
                if offline or random.random() < RENEW_FAIL:
                    sub_alive[pid] = False          # renewal missed - it lapses
                    lapses += 1
                    next_renew[pid] = clock + SUB_LIFETIME
                else:
                    sub_alive[pid] = True
                    next_renew[pid] = clock + RENEW_EVERY

        # COV fires only on a move past the deadband, and only if subscribed
        moved = last_sent[pid] is None or abs(v - last_sent[pid]) >= p["deadband"]
        if moved and sub_alive[pid] and not offline:
            if random.random() < COV_LOSS:
                dropped += 1                         # unconfirmed - nobody notices
            else:
                cov_events[pid].append((t, v))
                last_sent[pid] = v

        # the reconciliation poll: confirmed, so it either answers or errors
        if t % POLL_EVERY == 0 and not offline:
            poll_events[pid].append((t, v))

# ---------------------------------------------------------------- scoring
def coverage(stream):
    """Walk the day minute by minute and track how stale the stored value is."""
    out = {}
    for p in POINTS:
        pid = p["id"]
        evs = sorted(stream[pid]); i = 0; last = None
        stale, dark, longest, run = [], 0, 0, 0
        for t in range(DAY):
            while i < len(evs) and evs[i][0] <= t:
                last = evs[i][0]; i += 1
            age = DAY if last is None else t - last
            stale.append(age)
            if age > STALE_LIMIT:
                dark += 1; run += 1; longest = max(longest, run)
            else:
                run = 0
        out[pid] = {"dark_minutes": dark, "longest_gap": longest,
                    "mean_staleness": round(sum(stale) / DAY, 2),
                    "coverage_pct": round((DAY - dark) / DAY * 100, 2),
                    "stale": stale}
    return out

merged = {p["id"]: cov_events[p["id"]] + poll_events[p["id"]] for p in POINTS}
A, B = coverage(cov_events), coverage(merged)

def roll(res):
    tot = sum(r["dark_minutes"] for r in res.values())
    return {"coverage_pct": round(sum(r["coverage_pct"] for r in res.values()) / len(res), 2),
            "dark_point_minutes": tot,
            "longest_gap": max(r["longest_gap"] for r in res.values()),
            "points_with_gap": sum(1 for r in res.values() if r["longest_gap"] > STALE_LIMIT),
            "mean_staleness": round(sum(r["mean_staleness"] for r in res.values()) / len(res), 2)}

rA, rB = roll(A), roll(B)
BUCKET = 10                                  # minutes per cell in the coverage strip
strips = []
for p in POINTS:
    def band(res):
        s = res[p["id"]]["stale"]
        return [1 if max(s[i:i + BUCKET]) > STALE_LIMIT else 0 for i in range(0, DAY, BUCKET)]
    strips.append({"id": p["id"], "device": p["device"], "name": p["name"],
                   "unit": p["unit"], "cov": band(A), "both": band(B),
                   "cov_cov": A[p["id"]]["coverage_pct"], "both_cov": B[p["id"]]["coverage_pct"]})

sample = POINTS[0]
trace = {"name": f'{sample["device"]} {sample["name"]}', "unit": sample["unit"],
         "truth": [truth(sample, t) for t in range(0, DAY, 5)],
         "cov": sorted(cov_events[sample["id"]]), "poll": sorted(poll_events[sample["id"]])}

out = {"config": {"points": len(POINTS), "devices": len(DEVICES), "minutes": DAY,
                  "sub_lifetime_s": SUB_LIFETIME, "renew_every_s": RENEW_EVERY,
                  "cov_loss_pct": COV_LOSS * 100, "renew_fail_pct": RENEW_FAIL * 100,
                  "poll_every_min": POLL_EVERY, "stale_limit_min": STALE_LIMIT,
                  "outage_device": OUTAGE_DEV, "outage_minutes": len(OUTAGE),
                  "bucket_min": BUCKET},
       "injected": {"cov_notifications_dropped": dropped, "subscription_lapses": lapses},
       "cov_only": rA, "cov_plus_poll": rB, "strips": strips, "trace": trace,
       "events": {"cov": sum(len(v) for v in cov_events.values()),
                  "poll": sum(len(v) for v in poll_events.values())}}
json.dump(out, open("site/sim.json", "w"), indent=1)

print(f"{len(POINTS)} points across {len(DEVICES)} devices, {DAY} minutes\n")
print(f"injected: {dropped} COV notifications dropped, {lapses} subscription lapses\n")
for nm, r in (("COV only", rA), ("COV + 15-min poll", rB)):
    print(f"{nm:<20} coverage {r['coverage_pct']:>6.2f}%   dark point-minutes {r['dark_point_minutes']:>6}"
          f"   longest gap {r['longest_gap']:>4} min   points with a gap {r['points_with_gap']}/{len(POINTS)}")
