"use client";

/**
 * "How it works": the six stages as a keyboard-accessible tab list
 * (WAI-ARIA tabs: arrow keys move, Home/End jump, Tab moves into the panel).
 * Each panel pairs the plain description with a small illustration drawn in
 * HTML. The illustrations use obviously sample names and are hidden from
 * screen readers; the text says everything they show.
 *
 * All six panels are in the exported HTML (inactive ones `hidden`), so the
 * text is there for search engines and assistive tech. The panel swap
 * animates only in response to a choice, and not at all under
 * prefers-reduced-motion (globals.css .lp-panel-in).
 */

import { useRef, useState } from "react";
import { Check } from "lucide-react";

import { nextTabIndex } from "@/lib/homeMotion";
import { STAGES, type Stage } from "./content";

export function StageStepper() {
  const [active, setActive] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);

  function select(i: number, focus = false) {
    setActive(i);
    if (focus) tabs.current[i]?.focus();
  }

  return (
    <div className="grid gap-8 lg:grid-cols-[17rem_1fr] lg:gap-12">
      <div
        role="tablist"
        aria-label="Stages of a Vectorae campaign"
        aria-orientation="vertical"
        className="lp-stage-tabs -mx-4 flex gap-2 overflow-x-auto px-4 pb-2 lg:mx-0 lg:flex-col lg:gap-0 lg:overflow-visible lg:px-0 lg:pb-0"
        onKeyDown={(e) => {
          const next = nextTabIndex(e.key, active, STAGES.length);
          if (next === null) return;
          e.preventDefault();
          select(next, true);
        }}
      >
        {STAGES.map((s, i) => {
          const selected = i === active;
          const done = i < active;
          return (
            <button
              key={s.id}
              ref={(el) => {
                tabs.current[i] = el;
              }}
              role="tab"
              id={`stage-tab-${s.id}`}
              aria-selected={selected}
              aria-controls={`stage-panel-${s.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => select(i)}
              data-state={selected ? "active" : done ? "done" : "todo"}
              className="lp-stage-tab group relative flex shrink-0 items-center gap-3 rounded-xl px-4 py-3 text-left lg:rounded-none lg:px-0 lg:py-4"
            >
              <span aria-hidden className="lp-stage-dot">
                {done ? <Check className="h-3 w-3" strokeWidth={3.5} /> : null}
              </span>
              <span className="flex items-baseline gap-2">
                <span className="text-sm tabular-nums text-[#64748b]">{i + 1}</span>
                <span className="text-base font-semibold tracking-tight lg:text-lg">{s.name}</span>
              </span>
            </button>
          );
        })}
      </div>

      {STAGES.map((stage, i) => (
      <div
        key={stage.id}
        hidden={i !== active}
        role="tabpanel"
        id={`stage-panel-${stage.id}`}
        aria-labelledby={`stage-tab-${stage.id}`}
        tabIndex={0}
        className="lp-panel-in grid gap-8 rounded-2xl bg-white p-6 ring-1 ring-[#e2e8f0] sm:p-8 xl:grid-cols-[1fr_22rem]"
        data-testid={`stage-panel-${stage.id}`}
      >
        <div>
          <h3 className="text-2xl font-bold tracking-tight text-[#0f172a] sm:text-[1.75rem] sm:leading-tight">
            {stage.title}
          </h3>
          <p className="mt-4 max-w-[62ch] text-base leading-relaxed text-[#334155]">{stage.body}</p>
          <ul className="mt-5 space-y-2">
            {stage.facts.map((f) => (
              <li key={f} className="flex gap-2.5 text-[0.95rem] text-[#0f172a]">
                <Check aria-hidden className="mt-1 h-4 w-4 shrink-0 text-[#0e7490]" strokeWidth={3} />
                {f}
              </li>
            ))}
          </ul>
          <p className="mt-6 border-l-4 border-[#f2b544] pl-4 text-[0.95rem] text-[#334155]">
            <span className="font-semibold text-[#0f172a]">Your part: </span>
            {stage.you}
          </p>
          {i < STAGES.length - 1 && (
            <button
              type="button"
              onClick={() => select(i + 1, true)}
              className="mt-6 text-[0.95rem] font-semibold text-[#1d4ed8] underline decoration-[#1d4ed8]/30 underline-offset-4 hover:decoration-[#1d4ed8]"
            >
              Next: {STAGES[i + 1].name}
            </button>
          )}
        </div>
        <StageVisual stage={stage} />
      </div>
      ))}
    </div>
  );
}

/** Decorative: everything here is also said in the panel text. */
function StageVisual({ stage }: { stage: Stage }) {
  return (
    <div aria-hidden className="lp-visual self-start rounded-xl bg-[#f1f5f9] p-4 text-[13px] text-[#0f172a]">
      {VISUALS[stage.id]}
      <p className="mt-3 text-[11px] text-[#64748b]">Illustration with sample data</p>
    </div>
  );
}

const pill = "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold";

const VISUALS: Record<string, React.ReactNode> = {
  research: (
    <div className="space-y-2">
      {["Ideal client profile", "Market and demand", "Competitors", "Channels", "Messaging"].map((x) => (
        <div key={x} className="flex items-center gap-2">
          <span className="grid h-4 w-4 place-items-center rounded-full bg-[#0e7490] text-white">
            <Check className="h-2.5 w-2.5" strokeWidth={4} />
          </span>
          {x}
        </div>
      ))}
      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#e2e8f0]">
        <div className="h-full w-full bg-[#1d4ed8]" />
      </div>
      <p className={`${pill} mt-2 bg-[#dcfce7] text-[#166534]`}>Verified: 10 of 10 checks</p>
    </div>
  ),
  leads: (
    <div className="divide-y divide-[#e2e8f0] rounded-lg bg-white px-3">
      {[
        ["Acme Fire Protection", "Verified", true],
        ["Northwind Sprinkler Co.", "Verified", true],
        ["Example Alarm Services", "Removed", false],
        ["Summit Safety Systems", "Verified", true],
      ].map(([name, label, ok]) => (
        <div key={name as string} className="flex items-center justify-between gap-2 py-2">
          <span className={ok ? "font-medium" : "text-[#94a3b8] line-through"}>{name}</span>
          <span className={`${pill} ${ok ? "bg-[#dcfce7] text-[#166534]" : "bg-[#fee2e2] text-[#991b1b]"}`}>{label}</span>
        </div>
      ))}
    </div>
  ),
  sequences: (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {["Day 0", "Day 4", "Day 9"].map((d, i) => (
          <span key={d} className="rounded-md bg-white px-2 py-1 font-semibold ring-1 ring-[#cbd5e1]">
            Step {i + 1}, {d.toLowerCase()}
          </span>
        ))}
      </div>
      <div className="mt-3 rounded-lg bg-white p-3 leading-relaxed">
        <p className="text-[11px] text-[#64748b]">To: Dana Reyes, Acme Fire Protection</p>
        <p className="mt-1 font-semibold">Inspection season at Acme</p>
        <p className="mt-1 text-[#334155]">Hi Dana, spring inspections usually fill your team&apos;s calendar…</p>
        <p className="mt-2 border-t border-dashed border-[#cbd5e1] pt-2 text-[11px] text-[#64748b]">
          Don&apos;t want these emails? <span className="text-[#1d4ed8] underline">Unsubscribe instantly</span>
        </p>
      </div>
    </div>
  ),
  sending: (
    <div className="space-y-2">
      <div className="rounded-lg bg-white p-3">
        <p className="font-semibold">Automatic sending today</p>
        <p className={`${pill} mt-2 bg-[#dcfce7] text-[#166534]`}>Approved for today</p>
      </div>
      {[
        ["9:40 AM", "Dana, Eastern time"],
        ["10:15 AM", "Sam, Central time"],
        ["11:05 AM", "Jo, Pacific time"],
      ].map(([t, who]) => (
        <div key={t} className="flex items-center justify-between rounded-lg bg-[#eff6ff] px-3 py-2 ring-1 ring-[#bfdbfe]">
          <span className="font-semibold tabular-nums text-[#1d4ed8]">{t}</span>
          <span className="text-[#334155]">{who}</span>
        </div>
      ))}
    </div>
  ),
  replies: (
    <div className="divide-y divide-[#e2e8f0] rounded-lg bg-white px-3">
      {[
        ["Dana Reyes", "Can we talk Thursday?", "Interested", "bg-[#dcfce7] text-[#166534]"],
        ["Sam Patel", "Try us after the new year.", "Not now", "bg-[#fef3c7] text-[#92400e]"],
        ["Jo Kim", "Please take me off your list.", "Unsubscribed", "bg-[#e2e8f0] text-[#334155]"],
      ].map(([who, said, label, tone]) => (
        <div key={who} className="flex items-center justify-between gap-2 py-2">
          <span>
            <span className="block font-semibold">{who}</span>
            <span className="text-[#475569]">&ldquo;{said}&rdquo;</span>
          </span>
          <span className={`${pill} shrink-0 ${tone}`}>{label}</span>
        </div>
      ))}
    </div>
  ),
  meetings: (
    <div className="grid grid-cols-5 gap-1 rounded-lg bg-white p-2">
      {["Mon", "Tue", "Wed", "Thu", "Fri"].map((d) => (
        <span key={d} className="pb-1 text-center text-[11px] font-semibold text-[#475569]">
          {d}
        </span>
      ))}
      {Array.from({ length: 20 }, (_, i) => {
        const booked = i === 8 || i === 11 || i === 19;
        return (
          <span
            key={i}
            className={`h-7 rounded ${booked ? "bg-[#f2b544]" : "bg-[#f1f5f9]"}`}
          />
        );
      })}
    </div>
  ),
};
