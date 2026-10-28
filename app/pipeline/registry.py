"""The Step Registry — Vectorae's core IP, defined as DATA not code.

Both pipelines (STRATEGY: project knowledge section C phases 1-8, and
GTM: the Flow-2 second 72) are declared below as plain phase/step
structures. Editing a step's name, adding guidance, or re-wording the
prompt template requires touching only this file — the engine
(app/pipeline/engine.py) never changes.

Invariants enforced by tests (tests/test_registry.py):
- exactly 8 phases x 9 steps = 72 steps per pipeline
- globally unique step ids ("s3.21", "g8.72", ...)
- step_no runs 1..72 in order within each pipeline
"""

from dataclasses import dataclass

from app.db.models import PipelineKind

# --------------------------------------------------------------------------
# Prompt templates shared by all steps (edit freely -- they are data).
# The engine fills them; a step's prompt is PREFIX_TEMPLATE then STEP_TEMPLATE.
#
# Phase 3 item 3: the prefix holds what every step of a phase shares (the
# product, past-client patterns, earlier phases' syntheses and the phase's
# live inputs) and is sent as the gateway's cache_prefix, so steps 2-9 of a
# phase read it from the cache. The step template holds what changes: this
# phase's outputs so far, then the step and its instructions -- the question
# after the long material it is about.
# --------------------------------------------------------------------------

PREFIX_TEMPLATE = """You are an elite B2B go-to-market strategist working inside
Vectorae, an automated client-acquisition system. You are executing ONE
research step of a larger pipeline. Be concrete, decision-ready and
specific to THIS product — never generic filler.

PRODUCT / SKILL UNDER RESEARCH:
{product_block}

PROVEN PAST-CLIENT PATTERNS (anchor on these when present):
{patterns_block}

RELEVANT PRIOR RESEARCH (earlier phases' syntheses and live inputs):
{stable_context}

"""

STEP_TEMPLATE = """RELEVANT PRIOR RESEARCH (this phase so far):
{phase_context}

CURRENT PHASE: {phase_title}
CURRENT STEP: {step_name}
STEP GUIDANCE: {guidance}

Produce the output for the CURRENT STEP only, in clear markdown,
300-600 words, ending with a 2-3 bullet "Key takeaways" list."""

SYSTEM_PROMPT = (
    "You are Vectorae's research engine: a senior GTM strategist. "
    "You produce rigorous, specific, actionable research outputs. "
    "You never invent statistics; when a figure is an estimate you label "
    "it as an assumption."
)

# --------------------------------------------------------------------------
# STRATEGY pipeline — section C, phases 1-8 (9 steps each)
# --------------------------------------------------------------------------

STRATEGY_PHASES: list[tuple[str, list[str]]] = [
    ("Product/skill decomposition, value prop, differentiators", [
        "Core offering definition",
        "Problem statement & cost of inaction",
        "Value proposition draft",
        "Key differentiators",
        "Feature-to-benefit mapping",
        "Pricing logic & packaging hypotheses",
        "Delivery model & fulfillment constraints",
        "Proof-of-capability inventory",
        "Phase synthesis: product thesis",
    ]),
    ("ICP: firmographics, personas, pain points, buying triggers", [
        "Firmographics (industry, size, geography, revenue)",
        "Buyer personas",
        "Decision-maker & influencer roles",
        "Pain points ranked by urgency",
        "Buying triggers & timing signals",
        "Disqualifiers & anti-ICP",
        "Watering holes (where the ICP congregates)",
        "Past-client pattern alignment check",
        "Phase synthesis: ICP definition",
    ]),
    ("Market sizing, segmentation, prioritized segments", [
        "TAM estimate with stated assumptions",
        "SAM estimate",
        "SOM 12-month target",
        "Segmentation criteria",
        "Segment attractiveness scoring",
        "Priority segment #1 deep profile",
        "Priority segment #2 deep profile",
        "Segment entry sequencing",
        "Phase synthesis: market map",
    ]),
    ("Competitors, alternatives, positioning gaps", [
        "Direct competitor inventory",
        "Indirect alternatives & DIY options",
        "Status-quo / do-nothing analysis",
        "Competitor messaging audit",
        "Pricing landscape comparison",
        "Strengths/weaknesses grid",
        "Positioning gaps & white space",
        "Win/loss hypotheses",
        "Phase synthesis: positioning statement",
    ]),
    ("Channel analysis: email, WhatsApp, LinkedIn, referral, inbound", [
        "Cold email viability & constraints",
        "WhatsApp viability (opt-in & template rules)",
        "LinkedIn viability",
        "Referral engine design potential",
        "Inbound/content channel assessment",
        "Channel-to-ICP fit scoring",
        "Per-channel compliance constraints (by target country)",
        "Channel mix & budget split recommendation",
        "Phase synthesis: channel plan",
    ]),
    ("Messaging, offer design, hooks per persona", [
        "Core narrative & story arc",
        "Hooks per persona",
        "Offer design (irresistible front-end offer)",
        "Subject lines & openers bank",
        "Call-to-action design",
        "Personalization variables & data needs",
        "Message sequence arcs per channel",
        "A/B variant plan",
        "Phase synthesis: messaging playbook",
    ]),
    ("Objection mapping, proof assets, case-study angles", [
        "Objection inventory ranked by frequency",
        "Rebuttal scripts",
        "Proof asset inventory & gaps",
        "Case-study angles",
        "ROI / business-case math",
        "Risk-reversal & guarantee options",
        "Social-proof acquisition plan",
        "Objection-handling FAQ",
        "Phase synthesis: objection playbook",
    ]),
    ("Execution plan: sequences, cadence, KPIs, budget", [
        "Sequence blueprints per channel",
        "Cadence & send-time plan",
        "Volume ramp & warm-up plan",
        "KPI targets (reply, meeting, win rates)",
        "Budget allocation",
        "Tooling & data requirements",
        "Team & process design",
        "Risk register & contingency plan",
        "Phase synthesis: 90-day execution plan",
    ]),
]

# --------------------------------------------------------------------------
# GTM pipeline — Flow 2's second 72 (project knowledge section C, GTM map)
# --------------------------------------------------------------------------

GTM_PHASES: list[tuple[str, list[str]]] = [
    ("Positioning", [
        "Market category definition",
        "Positioning statement",
        "Messaging hierarchy",
        "Competitive-alternative framing",
        "Unique attributes",
        "Value themes per audience",
        "Proof points per claim",
        "Naming & tagline options",
        "Phase synthesis: positioning document",
    ]),
    ("Pricing", [
        "Cost basis & margin floor",
        "Value metric selection",
        "Pricing model options",
        "Tier & package design",
        "Anchoring & decoy strategy",
        "Discount & negotiation policy",
        "Competitor price benchmarking",
        "Willingness-to-pay test plan",
        "Phase synthesis: pricing plan",
    ]),
    ("Launch sequencing", [
        "Launch goals & definition of success",
        "Audience waves & sequencing",
        "Pre-launch asset checklist",
        "Launch-week day-by-day plan",
        "Post-launch follow-up plan",
        "Beta / pilot program design",
        "Announcement channel plan",
        "Launch risk register",
        "Phase synthesis: launch timeline",
    ]),
    ("Channel budget", [
        "Channel inventory & candidates",
        "CAC assumptions per channel",
        "Budget scenarios (lean/base/aggressive)",
        "Allocation by segment",
        "Payback-period modeling",
        "Experiment budget reserve",
        "Scaling triggers per channel",
        "Kill criteria per channel",
        "Phase synthesis: channel budget plan",
    ]),
    ("Content plan", [
        "Content pillars",
        "Funnel-stage content mapping",
        "Editorial calendar (first 90 days)",
        "Lead magnet designs",
        "SEO & keyword targets",
        "Distribution & amplification plan",
        "Repurposing system",
        "Content KPIs",
        "Phase synthesis: content plan",
    ]),
    ("Partnerships", [
        "Partner categories",
        "Ideal partner profile",
        "Value-exchange design",
        "Target-partner list criteria",
        "Partner outreach approach",
        "Co-marketing plays",
        "Referral terms & incentives",
        "Partner success metrics",
        "Phase synthesis: partnership map",
    ]),
    ("Sales motion", [
        "Sales motion selection (self-serve/inside/field)",
        "Sales stage definitions",
        "Qualification framework",
        "Demo / first-meeting design",
        "Proposal & close process",
        "Handoff & onboarding design",
        "CRM & process tooling",
        "Sales enablement assets",
        "Phase synthesis: sales motion document",
    ]),
    ("Funnel metrics & dashboards", [
        "Funnel stage definitions",
        "Stage conversion benchmarks",
        "North-star metric selection",
        "Dashboard specification",
        "Attribution approach",
        "Reporting cadence & owners",
        "Alert thresholds",
        "Review rituals",
        "Phase synthesis: metrics & dashboard plan",
    ]),
]


# --------------------------------------------------------------------------
# Registry construction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StepSpec:
    id: str                 # "s1.01" .. "s8.72" / "g1.01" .. "g8.72"
    pipeline: PipelineKind
    phase: int              # 1..8
    phase_title: str
    step_no: int            # 1..72 within the pipeline
    name: str
    guidance: str

    def build_prompt(self, product_block: str, patterns_block: str, stable_context: str,
                     phase_context: str) -> tuple[str, str]:
        """(cache prefix, step prompt). The prefix is identical for every step
        of a phase; the model reads it first, then the step prompt."""
        prefix = PREFIX_TEMPLATE.format(product_block=product_block,
                                        patterns_block=patterns_block,
                                        stable_context=stable_context)
        prompt = STEP_TEMPLATE.format(step_name=self.name, phase_title=self.phase_title,
                                      guidance=self.guidance, phase_context=phase_context)
        return prefix, prompt


def _build(pipeline: PipelineKind, prefix: str, phases: list[tuple[str, list[str]]]) -> list[StepSpec]:
    specs: list[StepSpec] = []
    for phase_idx, (title, steps) in enumerate(phases, start=1):
        for i, name in enumerate(steps, start=1):
            step_no = (phase_idx - 1) * 9 + i
            specs.append(
                StepSpec(
                    id=f"{prefix}{phase_idx}.{step_no:02d}",
                    pipeline=pipeline,
                    phase=phase_idx,
                    phase_title=title,
                    step_no=step_no,
                    name=name,
                    guidance=f"Focus exclusively on: {name}. Build on prior research; do not repeat it.",
                )
            )
    return specs


STRATEGY_STEPS: list[StepSpec] = _build(PipelineKind.STRATEGY, "s", STRATEGY_PHASES)
GTM_STEPS: list[StepSpec] = _build(PipelineKind.GTM, "g", GTM_PHASES)

REGISTRY: dict[PipelineKind, list[StepSpec]] = {
    PipelineKind.STRATEGY: STRATEGY_STEPS,
    PipelineKind.GTM: GTM_STEPS,
}


def get_steps(pipeline: PipelineKind) -> list[StepSpec]:
    return REGISTRY[pipeline]


def phase_title(pipeline: PipelineKind, phase: int) -> str:
    phases = STRATEGY_PHASES if pipeline is PipelineKind.STRATEGY else GTM_PHASES
    return phases[phase - 1][0]
