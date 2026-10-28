// Vectorae agents, C++ core: Deliverability Guard, Reply Triage and Claim &
// Compliance Check. Pure functions over plain values; bindings.cpp exposes
// them to Python as the module `vectorae_native_cpp`.
#pragma once

#include <string>
#include <utility>
#include <vector>

namespace vectorae {

// ---- Deliverability Guard --------------------------------------------------
struct MailboxStats {
    long sent = 0;
    long bounced = 0;
    long unsubscribed = 0;
    long replies = 0;
    double age_days = 0.0;
    long current_cap = 100;
};

struct MailboxVerdict {
    std::string risk;  // insufficient_data | healthy | elevated | critical
    long recommended_cap = 0;
    double health = 0.0;
    double bounce_rate = 0.0;
    double unsub_rate = 0.0;
    double reply_rate = 0.0;
    double bounce_lower = 0.0;
    double unsub_lower = 0.0;
    std::vector<std::string> reasons;
};

double wilson_lower(long k, long n, double z = 1.96);
long warmup_cap(double age_days, long current_cap);
MailboxVerdict assess_mailbox(const MailboxStats& s);

// ---- Reply Triage ----------------------------------------------------------
struct PhraseGroup {
    std::string category;
    double weight;
    std::vector<std::string> phrases;
};

struct Triage {
    std::string label;
    double confidence = 0.0;
    bool opt_out = false;
    std::vector<std::pair<std::string, double>> scores;    // category order
    std::vector<std::pair<std::string, std::string>> matched;  // (category, phrase)
};

const std::vector<PhraseGroup>& triage_groups();
std::string strip_quoted(const std::string& text);
Triage triage_reply(const std::string& subject, const std::string& body);

// ---- Claim & Compliance Check ----------------------------------------------
struct ClaimRule {
    std::string code;
    std::string severity;  // high | medium | low
    std::vector<std::string> phrases;
};

struct Finding {
    std::string code;
    std::string severity;
    std::string excerpt;
};

struct ClaimReport {
    std::string verdict;  // pass | review | block_recommended
    int risk = 0;
    std::vector<Finding> findings;
};

const std::vector<ClaimRule>& claim_rules();
int severity_points(const std::string& severity);
ClaimReport check_claims(const std::string& subject, const std::string& body, bool first_touch);

}  // namespace vectorae
