// Deliverability Guard: is this mailbox safe to keep sending from, and how much?
//
// Point rates say "elevated" (throttle); a Wilson LOWER bound above the hard
// line says we are confident it is bad, so "critical" (recommend a pause).
// Using the lower bound for the drastic call keeps a mailbox with 1 bounce in
// 25 sends from being paused on noise.
#include <algorithm>
#include <cmath>

#include "vectorae/agents.hpp"

namespace vectorae {

namespace {
constexpr long kMinSample = 20;
constexpr double kBounceElevated = 0.02, kBounceCritical = 0.05;
constexpr double kUnsubElevated = 0.005, kUnsubCritical = 0.01;

double rate(long k, long n) { return n > 0 ? static_cast<double>(k) / static_cast<double>(n) : 0.0; }
double round1(double x) { return std::floor(x * 10.0 + 0.5) / 10.0; }
}  // namespace

double wilson_lower(long k, long n, double z) {
    // With no successes the Wilson lower bound is exactly 0. Computed through
    // the formula it is centre - margin of two equal terms, which a compiler
    // that fuses multiply-adds (GCC 15 on the CI runner) leaves at ~3e-19.
    if (n <= 0 || k <= 0) return 0.0;
    const double nn = static_cast<double>(n);
    const double p = static_cast<double>(k) / nn;
    const double z2 = z * z;
    const double centre = p + z2 / (2.0 * nn);
    const double margin = z * std::sqrt(p * (1.0 - p) / nn + z2 / (4.0 * nn * nn));
    return std::max(0.0, (centre - margin) / (1.0 + z2 / nn));
}

long warmup_cap(double age_days, long current_cap) {
    long warm = current_cap;
    if (age_days < 14.0) warm = 20;
    else if (age_days < 30.0) warm = 40;
    else if (age_days < 60.0) warm = 70;
    return std::min(current_cap, warm);
}

MailboxVerdict assess_mailbox(const MailboxStats& s) {
    MailboxVerdict v;
    v.bounce_rate = rate(s.bounced, s.sent);
    v.unsub_rate = rate(s.unsubscribed, s.sent);
    v.reply_rate = rate(s.replies, s.sent);
    v.bounce_lower = wilson_lower(s.bounced, s.sent);
    v.unsub_lower = wilson_lower(s.unsubscribed, s.sent);
    const long cap = warmup_cap(s.age_days, s.current_cap);
    if (cap < s.current_cap) v.reasons.push_back("mailbox is still warming up");

    if (s.sent < kMinSample) {
        if (s.bounced >= 3 || s.unsubscribed >= 2) {
            v.risk = "elevated";
            v.reasons.push_back("several bounces or opt-outs in a small sample");
        } else {
            v.risk = "insufficient_data";
        }
    } else if (v.bounce_lower > kBounceCritical || v.unsub_lower > kUnsubCritical) {
        v.risk = "critical";
        if (v.bounce_lower > kBounceCritical) v.reasons.push_back("bounce rate is confidently above 5%");
        if (v.unsub_lower > kUnsubCritical) v.reasons.push_back("opt-out rate is confidently above 1%");
    } else if (v.bounce_rate > kBounceElevated || v.unsub_rate > kUnsubElevated) {
        v.risk = "elevated";
        if (v.bounce_rate > kBounceElevated) v.reasons.push_back("bounce rate above 2%");
        if (v.unsub_rate > kUnsubElevated) v.reasons.push_back("opt-out rate above 0.5%");
    } else {
        v.risk = "healthy";
    }

    if (v.risk == "critical") v.recommended_cap = 0;
    else if (v.risk == "elevated") v.recommended_cap = cap / 2;
    else v.recommended_cap = cap;

    const double health = 100.0 - 1000.0 * v.bounce_rate - 3000.0 * v.unsub_rate
                          + std::min(10.0, 100.0 * v.reply_rate);
    v.health = round1(std::clamp(health, 0.0, 100.0));
    return v;
}

}  // namespace vectorae
