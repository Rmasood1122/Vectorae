// Plain-assert tests for the C++ core; run by ctest (no Python involved).
#include <cassert>
#include <cmath>
#include <cstdio>
#include <initializer_list>
#include <string>

#include "vectorae/agents.hpp"
#include "vectorae/text.hpp"

using namespace vectorae;

static bool has(const ClaimReport& r, const std::string& code) {
    for (const auto& f : r.findings) if (f.code == code) return true;
    return false;
}

int main() {
    // text helpers
    std::string t = "not interested, but interested-ish? nonstop";
    assert(take_phrase(t, "not interested") == 1);
    assert(take_phrase(t, "interested") == 1);  // "interested-ish": '-' is a boundary
    assert(take_phrase(t, "stop") == 0);        // inside "nonstop"

    // deliverability
    assert(wilson_lower(0, 100) == 0.0);
    // No successes is exactly 0 for every n and z (GCC 15 left ~3e-19).
    for (long n : {1L, 7L, 100L, 12345L})
        for (double z : {1.0, 1.645, 1.96, 2.576})
            assert(wilson_lower(0, n, z) == 0.0);
    assert(std::fabs(wilson_lower(10, 100) - 0.0552) < 1e-3);
    assert(warmup_cap(5, 100) == 20 && warmup_cap(20, 100) == 40 && warmup_cap(90, 100) == 100);
    assert(warmup_cap(5, 10) == 10);
    auto v = assess_mailbox({10, 0, 0, 0, 90, 100});
    assert(v.risk == "insufficient_data" && v.recommended_cap == 100);
    v = assess_mailbox({200, 2, 0, 6, 90, 100});
    assert(v.risk == "healthy" && v.recommended_cap == 100);
    v = assess_mailbox({200, 8, 0, 0, 90, 100});
    assert(v.risk == "elevated" && v.recommended_cap == 50);
    v = assess_mailbox({200, 30, 0, 0, 90, 100});
    assert(v.risk == "critical" && v.recommended_cap == 0 && v.health == 0.0);
    v = assess_mailbox({200, 0, 0, 0, 10, 100});
    assert(v.recommended_cap == 20);

    // triage
    assert(triage_reply("Re: hi", "Please remove me from your list").label == "opt_out");
    auto tr = triage_reply("Re: hi", "Yes interested, but please unsubscribe my colleague");
    assert(tr.label == "opt_out" && tr.opt_out);
    assert(triage_reply("Automatic reply: out of office", "Back Monday").label == "out_of_office");
    assert(triage_reply("Re: hi", "Not interested, thanks.").label == "negative");
    assert(triage_reply("Re: hi", "Sounds good, when are you free?").label == "interested");
    assert(triage_reply("Re: hi", "Not right now, maybe next quarter").label == "not_now");
    assert(triage_reply("Re: hi", "ok").label == "unclear");
    // quoted text is ignored
    assert(triage_reply("Re: hi", "Tell me more\n> please unsubscribe\nOn Mon, Bob wrote:\nunsubscribe").label
           == "interested");

    // claims
    auto r = check_claims("Quick question", "We help agencies book meetings.", true);
    assert(r.verdict == "pass" && r.findings.empty());
    r = check_claims("RE: your growth", "Guaranteed 3x more leads, 40% cheaper. Act now!!!", true);
    assert(r.verdict == "block_recommended");
    assert(has(r, "unverifiable_guarantee") && has(r, "deceptive_subject") && has(r, "numeric_claim"));
    assert(has(r, "false_urgency") && has(r, "excessive_exclamation"));
    r = check_claims("Re: follow up", "Thanks again", false);
    assert(!has(r, "deceptive_subject"));
    r = check_claims("BIG NEWS FOR YOU", "the #1 tool", false);
    assert(has(r, "shouting_subject") && has(r, "superlative") && r.verdict == "pass" && r.risk == 16);
    r = check_claims("hi", "version 2.5 and abc3x", false);
    assert(!has(r, "numeric_claim"));

    std::puts("vectorae_native_cpp core: all tests passed");
    return 0;
}
