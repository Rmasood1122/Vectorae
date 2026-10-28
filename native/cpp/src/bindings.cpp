// pybind11 module `vectorae_native_cpp`: plain dicts/lists in and out, so the
// Python side (app/agents/native.py) can swap in the reference implementation
// without changing a single caller.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "vectorae/agents.hpp"

namespace py = pybind11;
using namespace vectorae;

PYBIND11_MODULE(vectorae_native_cpp, m) {
    m.doc() = "Vectorae agents, C++ core: deliverability guard, reply triage, claim check";
    m.attr("__version__") = "0.1.0";

    m.def("assess_mailbox", [](long sent, long bounced, long unsubscribed, long replies,
                               double age_days, long current_cap) {
        const auto v = assess_mailbox({sent, bounced, unsubscribed, replies, age_days, current_cap});
        py::dict d;
        d["risk"] = v.risk;
        d["recommended_cap"] = v.recommended_cap;
        d["health"] = v.health;
        d["bounce_rate"] = v.bounce_rate;
        d["unsub_rate"] = v.unsub_rate;
        d["reply_rate"] = v.reply_rate;
        d["bounce_lower"] = v.bounce_lower;
        d["unsub_lower"] = v.unsub_lower;
        d["reasons"] = v.reasons;
        return d;
    }, py::arg("sent"), py::arg("bounced"), py::arg("unsubscribed"), py::arg("replies"),
       py::arg("age_days"), py::arg("current_cap"));

    m.def("wilson_lower", &wilson_lower, py::arg("k"), py::arg("n"), py::arg("z") = 1.96);

    m.def("triage_reply", [](const std::string& subject, const std::string& body) {
        const auto t = triage_reply(subject, body);
        py::dict d;
        d["label"] = t.label;
        d["confidence"] = t.confidence;
        d["opt_out"] = t.opt_out;
        py::dict scores;
        for (const auto& s : t.scores) scores[py::str(s.first)] = s.second;
        d["scores"] = scores;
        d["matched"] = t.matched;
        return d;
    }, py::arg("subject"), py::arg("body"));

    m.def("check_claims", [](const std::string& subject, const std::string& body, bool first_touch) {
        const auto r = check_claims(subject, body, first_touch);
        py::dict d;
        d["verdict"] = r.verdict;
        d["risk"] = r.risk;
        py::list findings;
        for (const auto& f : r.findings) {
            py::dict fd;
            fd["code"] = f.code;
            fd["severity"] = f.severity;
            fd["excerpt"] = f.excerpt;
            findings.append(fd);
        }
        d["findings"] = findings;
        return d;
    }, py::arg("subject"), py::arg("body"), py::arg("first_touch"));

    // The phrase tables, so the parity test can prove the Python reference
    // implementation carries exactly the same lists.
    m.def("triage_groups", [] {
        py::list out;
        for (const auto& g : triage_groups()) out.append(py::make_tuple(g.category, g.weight, g.phrases));
        return out;
    });
    m.def("claim_rules", [] {
        py::list out;
        for (const auto& r : claim_rules()) out.append(py::make_tuple(r.code, r.severity, r.phrases));
        return out;
    });
}
