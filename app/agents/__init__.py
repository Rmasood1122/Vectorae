"""Vectorae agents: five advisory agents with native cores and a master
supervisor. See docs/features/vectorae-agents.md.

    lead_priority, followup_timing      Rust   (native/rust)
    deliverability_guard, reply_triage,
    claim_check                         C++    (native/cpp)
    supervisor (the master)             Rust   (native/rust/src/supervisor.rs)
"""
