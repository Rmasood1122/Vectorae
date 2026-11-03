"""Pure-Python reference implementation of the native agent cores.

The production cores are native: Rust in native/rust (lead priority,
follow-up timing, supervisor) and C++ in native/cpp (deliverability guard,
reply triage, claim check). This module is the same arithmetic, line for line,
for two jobs:

1. FALLBACK. The sealed eval workflow installs requirements.txt only, so the
   offline suite must pass where the native wheels were never built. The
   Docker image builds them and sets VECTORAE_REQUIRE_NATIVE=1, so production
   never silently runs this module (app/agents/native.py).
2. ORACLE. tests/test_agents_native_parity.py runs both on the same inputs and
   requires equal answers, and equal phrase tables.

Text handling is ASCII-only on purpose (lowercase and "alphanumeric" mean A-Z,
a-z, 0-9), matching the C++ byte-wise code exactly. Rounding is
floor(x*10 + 0.5)/10 in every language, never Python's banker's round().
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------- Rust: scoring
ENGAGEMENT_HALF_LIFE_DAYS = 7.0
FATIGUE_TOUCHES = 6.0


def _round1(x: float) -> float:
    return math.floor(x * 10.0 + 0.5) / 10.0


def _engagement(opens: int, clicks: int, days_since_last_engagement: float) -> float:
    raw = 1.0 - math.exp(-(0.35 * opens + 1.2 * clicks))
    if raw > 0.0 and days_since_last_engagement >= 0.0:
        return raw * math.exp(-math.log(2) * days_since_last_engagement
                              / ENGAGEMENT_HALF_LIFE_DAYS)
    return raw


def prioritize_leads(leads):
    """[(fit, opens, clicks, replied, bounced, unsubscribed, touches,
    days_since_last_touch, days_since_last_engagement)] ->
    [(priority, action, engagement, fatigue)]"""
    out = []
    for (fit, opens, clicks, replied, bounced, unsub, touches,
         since_touch, since_engagement) in leads:
        if unsub or bounced:
            out.append((0.0, "do_not_contact", 0.0, 0.0))
            continue
        fit = min(1.0, max(0.0, fit))
        eng = _engagement(opens, clicks, since_engagement)
        fatigue = min(1.0, touches / FATIGUE_TOUCHES)
        z = -2.0 + 3.0 * fit + 2.5 * eng - 1.5 * fatigue + (1.5 if replied else 0.0)
        p = 1.0 / (1.0 + math.exp(-z))
        if replied:
            action = "respond_now"
        elif touches == 0:
            action = "start_sequence"
        elif fatigue >= 1.0:
            action = "rest"
        elif eng > 0.5 and since_touch >= 2.0:
            action = "follow_up_now"
        else:
            action = "nurture"
        out.append((_round1(100.0 * p), action, eng, fatigue))
    return out


# ----------------------------------------------------------------- Rust: timing
HOURS_PER_WEEK = 168
PRIOR_BUSINESS = 1.0
PRIOR_OTHER = 0.1
WAIT_PENALTY_PER_HOUR = 0.002
CONFIDENCE_HALF_N = 20.0


def _is_business(weekday: int, hour: int) -> bool:
    return weekday < 5 and 8 <= hour < 18


def _slot_weights(replies) -> list[float]:
    w = [PRIOR_BUSINESS if _is_business(i // 24, i % 24) else PRIOR_OTHER
         for i in range(HOURS_PER_WEEK)]
    for d, h in replies:
        if 0 <= d < 7 and 0 <= h < 24:
            w[d * 24 + h] += 1.0
    top = max(w)
    return [v / top for v in w]


def next_followup_slot(replies, now_weekday, now_hour, min_gap_hours, business_only):
    """-> (hours_ahead, weekday, hour, weight, confidence) or None."""
    w = _slot_weights(replies)
    start = (now_weekday % 7) * 24 + (now_hour % 24)
    conf = len(replies) / (len(replies) + CONFIDENCE_HALF_N)
    best = None
    for ahead in range(min_gap_hours, min_gap_hours + HOURS_PER_WEEK):
        idx = (start + ahead) % HOURS_PER_WEEK
        d, h = idx // 24, idx % 24
        if business_only and not _is_business(d, h):
            continue
        score = w[idx] - WAIT_PENALTY_PER_HOUR * ahead
        if best is None or score > best[0]:
            best = (score, (ahead, d, h, w[idx], conf))
    return best[1] if best else None


def top_reply_slots(replies, k):
    w = _slot_weights(replies)
    order = sorted(range(HOURS_PER_WEEK), key=lambda i: (-w[i], i))
    return [(i // 24, i % 24, w[i]) for i in order[:k]]


# ------------------------------------------------------------- Rust: supervisor
def _p95(latencies) -> float:
    if not latencies:
        return 0.0
    v = sorted(latencies)
    rank = math.ceil(0.95 * len(v))
    return v[max(rank, 1) - 1]


def evaluate_agent(runs, failure_threshold=3, cooldown_s=300.0, latency_budget_ms=5000.0,
                   ewma_alpha=0.3, degraded_error_rate=0.3) -> dict:
    """runs: [(ok, latency_ms, age_s)], newest first."""
    if not runs:
        return {"circuit": "closed", "status": "idle", "consecutive_failures": 0,
                "error_rate": 0.0, "p95_latency_ms": 0.0, "runs": 0, "may_run": True,
                "reason": "no runs yet"}
    consecutive = 0
    for ok, _, _ in runs:
        if ok:
            break
        consecutive += 1
    ewma = 0.0
    for ok, _, _ in reversed(runs):
        ewma = ewma_alpha * (0.0 if ok else 1.0) + (1.0 - ewma_alpha) * ewma
    p = _p95([r[1] for r in runs])
    newest_age = runs[0][2]
    if consecutive >= failure_threshold:
        circuit, may_run = ("open", False) if newest_age < cooldown_s else ("half_open", True)
    else:
        circuit, may_run = "closed", True
    if circuit == "open":
        status = "down"
        reason = (f"{consecutive} consecutive failures; retry after "
                  f"{cooldown_s - newest_age:.0f}s cooldown")
    elif circuit == "half_open":
        status, reason = "degraded", f"{consecutive} consecutive failures; next run is a trial"
    elif ewma > degraded_error_rate:
        status, reason = "degraded", f"error rate {ewma * 100:.0f}% (smoothed)"
    elif p > latency_budget_ms:
        status = "degraded"
        reason = f"p95 latency {p:.0f} ms over the {latency_budget_ms:.0f} ms budget"
    else:
        status, reason = "healthy", "ok"
    return {"circuit": circuit, "status": status, "consecutive_failures": consecutive,
            "error_rate": ewma, "p95_latency_ms": p, "runs": len(runs), "may_run": may_run,
            "reason": reason}


# ------------------------------------------------------------ C++: text helpers
_UPPER_TO_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_DIGITS = frozenset("0123456789")


def _lower(s: str) -> str:
    return s.translate(_UPPER_TO_LOWER)


def _take_phrase(text: list[str], phrase: str) -> int:
    """text is a list of 1-char strings so matches can be blanked in place."""
    if not phrase:
        return 0
    need_before = phrase[0] in _ALNUM
    need_after = phrase[-1] in _ALNUM
    joined = "".join(text)
    count, pos = 0, 0
    while True:
        pos = joined.find(phrase, pos)
        if pos < 0:
            return count
        end = pos + len(phrase)
        ok_before = not need_before or pos == 0 or joined[pos - 1] not in _ALNUM
        ok_after = not need_after or end == len(joined) or joined[end] not in _ALNUM
        if ok_before and ok_after:
            count += 1
            for i in range(pos, end):
                text[i] = " "
            joined = joined[:pos] + " " * (end - pos) + joined[end:]
            pos = end
        else:
            pos += 1


def _trim(s: str) -> str:
    return s.strip(" \t\r")


def _lines(text: str) -> list[str]:
    """std::getline semantics: no trailing empty line, none at all for ''."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


# ------------------------------------------------------- C++: deliverability
_MIN_SAMPLE = 20
_BOUNCE_ELEVATED, _BOUNCE_CRITICAL = 0.02, 0.05
_UNSUB_ELEVATED, _UNSUB_CRITICAL = 0.005, 0.01


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    # Same guard as native/cpp/src/deliverability.cpp: no successes is exactly 0.
    if n <= 0 or k <= 0:
        return 0.0
    p = k / n
    z2 = z * z
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return max(0.0, (centre - margin) / (1.0 + z2 / n))


def _warmup_cap(age_days: float, current_cap: int) -> int:
    warm = current_cap
    if age_days < 14.0:
        warm = 20
    elif age_days < 30.0:
        warm = 40
    elif age_days < 60.0:
        warm = 70
    return min(current_cap, warm)


def assess_mailbox(sent, bounced, unsubscribed, replies, age_days, current_cap) -> dict:
    def rate(k):
        return k / sent if sent > 0 else 0.0

    v = {"bounce_rate": rate(bounced), "unsub_rate": rate(unsubscribed),
         "reply_rate": rate(replies), "bounce_lower": wilson_lower(bounced, sent),
         "unsub_lower": wilson_lower(unsubscribed, sent), "reasons": []}
    cap = _warmup_cap(age_days, current_cap)
    if cap < current_cap:
        v["reasons"].append("mailbox is still warming up")
    if sent < _MIN_SAMPLE:
        if bounced >= 3 or unsubscribed >= 2:
            risk = "elevated"
            v["reasons"].append("several bounces or opt-outs in a small sample")
        else:
            risk = "insufficient_data"
    elif v["bounce_lower"] > _BOUNCE_CRITICAL or v["unsub_lower"] > _UNSUB_CRITICAL:
        risk = "critical"
        if v["bounce_lower"] > _BOUNCE_CRITICAL:
            v["reasons"].append("bounce rate is confidently above 5%")
        if v["unsub_lower"] > _UNSUB_CRITICAL:
            v["reasons"].append("opt-out rate is confidently above 1%")
    elif v["bounce_rate"] > _BOUNCE_ELEVATED or v["unsub_rate"] > _UNSUB_ELEVATED:
        risk = "elevated"
        if v["bounce_rate"] > _BOUNCE_ELEVATED:
            v["reasons"].append("bounce rate above 2%")
        if v["unsub_rate"] > _UNSUB_ELEVATED:
            v["reasons"].append("opt-out rate above 0.5%")
    else:
        risk = "healthy"
    v["risk"] = risk
    v["recommended_cap"] = 0 if risk == "critical" else (cap // 2 if risk == "elevated" else cap)
    health = (100.0 - 1000.0 * v["bounce_rate"] - 3000.0 * v["unsub_rate"]
              + min(10.0, 100.0 * v["reply_rate"]))
    v["health"] = _round1(min(100.0, max(0.0, health)))
    return v


# ------------------------------------------------------------ C++: reply triage
TRIAGE_GROUPS: list[tuple[str, float, list[str]]] = [
    ("bounce", 3.0, ["delivery status notification", "undeliverable", "mailbox not found",
                     "address not found", "message not delivered", "delivery has failed",
                     "mail delivery subsystem", "recipient address rejected"]),
    ("out_of_office", 3.0, ["out of office", "out of the office", "on vacation",
                            "on annual leave", "on leave until", "away until", "automatic reply",
                            "auto-reply", "autoreply", "limited access to email",
                            "i will be back", "i'll be back on", "returning on"]),
    ("opt_out", 4.0, ["unsubscribe", "remove me", "take me off", "stop emailing",
                      "stop contacting", "stop sending", "do not contact", "don't contact",
                      "do not email", "don't email", "opt out", "opt-out", "remove my email",
                      "no more emails", "leave me alone"]),
    ("negative", 2.0, ["not interested", "no thanks", "no thank you", "not a fit", "not for us",
                       "we're all set", "we are all set", "already have a solution",
                       "please don't", "not looking"]),
    ("not_now", 2.0, ["not right now", "not at the moment", "maybe later", "next quarter",
                      "next year", "in a few months", "circle back", "reach back out",
                      "not a priority", "check back", "too busy"]),
    ("referral", 2.0, ["the right person", "better person", "reach out to",
                       "contact my colleague", "speak with my colleague", "cc'ing", "looping in",
                       "forwarding this to", "in charge of"]),
    ("interested", 2.0, ["interested", "tell me more", "sounds good", "sounds interesting",
                         "let's talk", "lets talk", "let's chat", "happy to chat", "book a call",
                         "schedule a call", "set up a call", "set up a time",
                         "when are you free", "what times work", "send me more",
                         "more information", "more info", "send over", "pricing", "demo",
                         "calendar link"]),
]


def strip_quoted(text: str) -> str:
    kept = []
    for line in _lines(text):
        t = _lower(_trim(line))
        if t.startswith(">"):
            continue
        if ((t.startswith("on ") and t.endswith("wrote:"))
                or t.startswith("-----original message") or t.startswith("from:")):
            break
        kept.append(line)
    return "\n".join(kept)


def triage_reply(subject: str, body: str) -> dict:
    text = list(_lower(subject + "\n" + strip_quoted(body)))
    scores: dict[str, float] = {}
    matched: list[tuple[str, str]] = []
    total = 0.0
    for category, weight, phrases in TRIAGE_GROUPS:
        score = 0.0
        for phrase in phrases:
            n = _take_phrase(text, phrase)
            if n > 0:
                score += weight * n
                matched.append((category, phrase))
        scores[category] = score
        total += score
    opt_out = scores["opt_out"] > 0.0
    top = 0.0
    if opt_out:
        label, top = "opt_out", scores["opt_out"]
    elif scores["bounce"] > 0.0:
        label, top = "bounce", scores["bounce"]
    elif scores["out_of_office"] > 0.0:
        label, top = "out_of_office", scores["out_of_office"]
    else:
        label = "unclear"
        for c in ("negative", "not_now", "referral", "interested"):
            if scores[c] > top:
                label, top = c, scores[c]
    return {"label": label, "confidence": top / (total + 1.0) if total > 0.0 else 0.0,
            "opt_out": opt_out, "scores": scores, "matched": matched}


# ------------------------------------------------------------- C++: claim check
CLAIM_RULES: list[tuple[str, str, list[str]]] = [
    ("unverifiable_guarantee", "high", ["guaranteed", "guarantees", "guarantee", "100%",
                                        "risk-free", "risk free", "no risk", "zero risk"]),
    ("superlative", "medium", ["best in class", "best-in-class", "#1", "number one",
                               "world-class", "world class", "industry-leading",
                               "industry leading", "market leader", "the only", "unbeatable"]),
    ("false_urgency", "medium", ["act now", "limited time", "last chance", "urgent",
                                 "expires today", "only today", "don't miss out",
                                 "before it's too late", "final notice"]),
    ("spam_trigger", "low", ["click here", "free money", "winner", "no obligation", "cash bonus",
                             "$$$", "earn money", "double your"]),
]
_POINTS = {"high": 30, "medium": 12}


def severity_points(severity: str) -> int:
    return _POINTS.get(severity, 4)


def _numeric_claims(t: str, out: list[dict]) -> None:
    n, i = len(t), 0
    while i < n:
        if t[i] in _DIGITS and (i == 0 or (t[i - 1] not in _ALNUM and t[i - 1] != ".")):
            j = i
            while j < n and (t[j] in _DIGITS or t[j] == "."):
                j += 1
            k = j
            if k < n and t[k] == " ":
                k += 1
            if k < n and t[k] == "%":
                out.append({"code": "numeric_claim", "severity": "medium", "excerpt": t[i:k + 1]})
            elif j < n and t[j] == "x" and (j + 1 == n or t[j + 1] not in _ALNUM):
                out.append({"code": "numeric_claim", "severity": "medium", "excerpt": t[i:j + 1]})
            i = j
        else:
            i += 1


def check_claims(subject: str, body: str, first_touch: bool) -> dict:
    chars = list(_lower(subject + "\n" + body))
    findings: list[dict] = []
    for code, severity, phrases in CLAIM_RULES:
        for phrase in phrases:
            for _ in range(_take_phrase(chars, phrase)):
                findings.append({"code": code, "severity": severity, "excerpt": phrase})
    _numeric_claims("".join(chars), findings)
    subj = _lower(_trim(subject))
    if first_touch and (subj.startswith("re:") or subj.startswith("fwd:") or subj.startswith("fw:")):
        findings.append({"code": "deceptive_subject", "severity": "high",
                         "excerpt": subj[:subj.find(":") + 1]})
    letters = [c for c in subject if c in _ALNUM and c not in _DIGITS]
    upper = sum(1 for c in letters if "A" <= c <= "Z")
    if len(letters) >= 8 and upper * 10 > len(letters) * 6:
        findings.append({"code": "shouting_subject", "severity": "low", "excerpt": subject})
    bangs = (subject + body).count("!")
    if bangs >= 3:
        findings.append({"code": "excessive_exclamation", "severity": "low",
                         "excerpt": f"{bangs} x !"})
    risk = min(100, sum(severity_points(f["severity"]) for f in findings))
    high = any(f["severity"] == "high" for f in findings)
    verdict = "block_recommended" if high else ("review" if risk >= 20 else "pass")
    return {"verdict": verdict, "risk": risk, "findings": findings}
