//! Vectorae native agents, Rust core. Pure Rust in `scoring`, `timing` and
//! `supervisor`; the `python` feature adds the PyO3 module `vectorae_native_rs`.

pub mod scoring;
pub mod supervisor;
pub mod timing;

#[cfg(feature = "python")]
mod py {
    use pyo3::prelude::*;
    use pyo3::types::PyDict;

    use crate::{scoring, supervisor, timing};

    /// (fit, opens, clicks, replied, bounced, unsubscribed, touches,
    ///  days_since_last_touch, days_since_last_engagement) per lead ->
    /// [(priority, action, engagement, fatigue)].
    #[pyfunction]
    #[allow(clippy::type_complexity)]
    fn prioritize_leads(leads: Vec<(f64, u32, u32, bool, bool, bool, u32, f64, f64)>)
                        -> Vec<(f64, &'static str, f64, f64)> {
        leads.into_iter().map(|l| {
            let p = scoring::prioritize(&scoring::LeadSignals {
                fit: l.0, opens: l.1, clicks: l.2, replied: l.3, bounced: l.4,
                unsubscribed: l.5, touches: l.6, days_since_last_touch: l.7,
                days_since_last_engagement: l.8 });
            (p.priority, p.action, p.engagement, p.fatigue)
        }).collect()
    }

    /// -> (hours_ahead, weekday, hour, weight, confidence) or None.
    #[pyfunction]
    fn next_followup_slot(replies: Vec<(u32, u32)>, now_weekday: u32, now_hour: u32,
                          min_gap_hours: u32, business_only: bool)
                          -> Option<(u32, u32, u32, f64, f64)> {
        timing::next_slot(&replies, now_weekday, now_hour, min_gap_hours, business_only)
            .map(|s| (s.hours_ahead, s.weekday, s.hour, s.weight, s.confidence))
    }

    #[pyfunction]
    fn top_reply_slots(replies: Vec<(u32, u32)>, k: usize) -> Vec<(u32, u32, f64)> {
        timing::top_slots(&replies, k)
    }

    /// runs: [(ok, latency_ms, age_s)] newest first -> dict.
    #[pyfunction]
    #[pyo3(signature = (runs, failure_threshold=3, cooldown_s=300.0, latency_budget_ms=5000.0,
                        ewma_alpha=0.3, degraded_error_rate=0.3))]
    fn evaluate_agent<'py>(py: Python<'py>, runs: Vec<(bool, f64, f64)>, failure_threshold: u32,
                           cooldown_s: f64, latency_budget_ms: f64, ewma_alpha: f64,
                           degraded_error_rate: f64) -> PyResult<Bound<'py, PyDict>> {
        let runs: Vec<supervisor::Run> = runs.into_iter()
            .map(|(ok, latency_ms, age_s)| supervisor::Run { ok, latency_ms, age_s }).collect();
        let policy = supervisor::Policy { failure_threshold, cooldown_s, latency_budget_ms,
                                          ewma_alpha, degraded_error_rate };
        let h = supervisor::evaluate(&runs, &policy);
        let d = PyDict::new(py);
        d.set_item("circuit", h.circuit)?;
        d.set_item("status", h.status)?;
        d.set_item("consecutive_failures", h.consecutive_failures)?;
        d.set_item("error_rate", h.error_rate)?;
        d.set_item("p95_latency_ms", h.p95_latency_ms)?;
        d.set_item("runs", h.runs)?;
        d.set_item("may_run", h.may_run)?;
        d.set_item("reason", h.reason)?;
        Ok(d)
    }

    #[pymodule]
    fn vectorae_native_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        m.add_function(wrap_pyfunction!(prioritize_leads, m)?)?;
        m.add_function(wrap_pyfunction!(next_followup_slot, m)?)?;
        m.add_function(wrap_pyfunction!(top_reply_slots, m)?)?;
        m.add_function(wrap_pyfunction!(evaluate_agent, m)?)?;
        Ok(())
    }
}
