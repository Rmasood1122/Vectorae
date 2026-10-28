"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getLawfulBasis } from "@/lib/api/autopilot";
import { intakeCanLaunch, optInBody } from "@/lib/autopilot";
import { Checkbox } from "@/components/ui/checkbox";
import {
  canAdvance,
  INITIAL_INTAKE,
  nextStep,
  prevStep,
  type IntakeState,
} from "@/components/intake/wizard";
import {
  addPastClients,
  createProduct,
  createStrategy,
} from "@/lib/api/strategies";
import { ApiError } from "@/lib/api/client";
import {
  BUSINESS_TYPES,
  INITIAL_LOCAL,
  RADIUS_NOTE,
  businessTypeBody,
  businessTypeLabel,
  describeArea,
  localErrors,
  strategyLocalBody,
  type BusinessType,
  type DistanceUnit,
  type LocalDraft,
} from "@/lib/localBusiness";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";

export default function IntakeWizard() {
  const router = useRouter();
  const toast = useToast();
  const [state, setState] = useState<IntakeState>(INITIAL_INTAKE);
  const [busy, setBusy] = useState(false);
  // Autopilot launch: both boxes start UNTICKED (never pre-ticked).
  const [autopilot, setAutopilot] = useState(false);
  const [lawful, setLawful] = useState(false);
  const basis = useQuery({ queryKey: ["autopilot-lawful-basis"], queryFn: getLawfulBasis });

  async function launch() {
    setBusy(true);
    try {
      const businessType = businessTypeBody(state.local);
      const product = await createProduct(
        businessType ? { ...state.product, business_type: businessType } : state.product,
      );
      if (state.hasPastClients && state.clients.length) {
        await addPastClients(product.id, state.clients);
      }
      const strategy = await createStrategy(
        product.id, !!state.hasPastClients, optInBody(autopilot, lawful, basis.data),
        strategyLocalBody(state.local));
      toast("Strategy created — research pipeline started", "success");
      router.push(`/strategies/detail?id=${strategy.id}`);
    } catch (err) {
      toast(err instanceof ApiError ? err.detail : "Could not start", "error");
      setBusy(false);
    }
  }

  const local = state.local ?? INITIAL_LOCAL;
  const setLocal = (patch: Partial<LocalDraft>) =>
    setState((s) => ({ ...s, local: { ...(s.local ?? INITIAL_LOCAL), ...patch } }));
  const localProblems = localErrors(state.local);

  const setClient = (i: number, field: "details" | "acquisition_story", v: string) =>
    setState((s) => ({
      ...s,
      clients: s.clients.map((c, j) => (j === i ? { ...c, [field]: v } : c)),
    }));

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">New strategy</h1>

      {state.step === "product" && (
        <Card>
          <CardHeader><CardTitle>What do you want clients for?</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="name">Name</Label>
              <Input id="name" value={state.product.name}
                     onChange={(e) => setState((s) => ({ ...s, product: { ...s.product, name: e.target.value } }))} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="desc">Describe the product or skill</Label>
              <Textarea id="desc" value={state.product.description}
                        placeholder="What it does, who it's for, what it costs…"
                        onChange={(e) => setState((s) => ({ ...s, product: { ...s.product, description: e.target.value } }))} />
            </div>
            <fieldset className="flex gap-4" aria-label="Type">
              {(["product", "skill"] as const).map((t) => (
                <label key={t} className="flex items-center gap-2 text-sm">
                  <input type="radio" name="type" checked={state.product.type === t}
                         onChange={() => setState((s) => ({ ...s, product: { ...s.product, type: t } }))} />
                  {t === "product" ? "A product" : "A skill / service"}
                </label>
              ))}
            </fieldset>
            <div className="space-y-1">
              <Label htmlFor="business-type">Is this a local business?</Label>
              <select id="business-type" value={local.businessType}
                      className="flex h-10 w-full rounded border border-border bg-card px-3 py-2 text-sm text-card-foreground"
                      onChange={(e) => setLocal({ businessType: e.target.value as BusinessType | "" })}>
                <option value="">No</option>
                {BUSINESS_TYPES.map((t) => (
                  <option key={t.key} value={t.key}>{t.label}</option>
                ))}
              </select>
              {local.businessType && (
                <p className="text-xs text-muted-foreground">
                  {BUSINESS_TYPES.find((t) => t.key === local.businessType)?.hint}{" "}
                  Vectorae finds business buyers near you; it never contacts consumers.
                </p>
              )}
            </div>
            {local.businessType && (
              <div className="space-y-3 rounded border border-border p-3">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label htmlFor="city">City or neighbourhood</Label>
                    <Input id="city" value={local.city} placeholder="Astoria, NY"
                           onChange={(e) => setLocal({ city: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="postal">Postcode (optional)</Label>
                    <Input id="postal" value={local.postalCode}
                           onChange={(e) => setLocal({ postalCode: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="country">Country (2 letters)</Label>
                    <Input id="country" value={local.country} maxLength={2}
                           onChange={(e) => setLocal({ country: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor="radius">Radius</Label>
                    <div className="flex gap-2">
                      <Input id="radius" type="number" min={0.5} step={0.5} value={local.radius}
                             onChange={(e) => setLocal({ radius: e.target.value })} />
                      <select aria-label="Unit" value={local.unit}
                              className="h-10 rounded border border-border bg-card px-2 text-sm"
                              onChange={(e) => setLocal({ unit: e.target.value as DistanceUnit })}>
                        <option value="mi">mi</option>
                        <option value="km">km</option>
                      </select>
                    </div>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">{RADIUS_NOTE}</p>
                <div className="space-y-1">
                  <Label htmlFor="offer">What you offer business buyers (optional)</Label>
                  <Textarea id="offer" value={local.offer} maxLength={500}
                            placeholder="Catering packages for team lunches, 10-200 people"
                            onChange={(e) => setLocal({ offer: e.target.value })} />
                </div>
                {localProblems.length > 0 && (
                  <ul className="list-disc pl-5 text-xs text-destructive">
                    {localProblems.map((p) => <li key={p}>{p}</li>)}
                  </ul>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {state.step === "past-clients-question" && (
        <Card>
          <CardHeader>
            <CardTitle>Do you have any past clients for this?</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 sm:flex-row">
            <Button
              variant={state.hasPastClients === true ? "default" : "outline"}
              className="flex-1"
              onClick={() => setState((s) => ({ ...s, hasPastClients: true }))}
            >
              Yes — build on proven patterns
            </Button>
            <Button
              variant={state.hasPastClients === false ? "default" : "outline"}
              className="flex-1"
              onClick={() => setState((s) => ({ ...s, hasPastClients: false }))}
            >
              No — research the market from scratch
            </Button>
          </CardContent>
        </Card>
      )}

      {state.step === "past-clients" && (
        <div className="space-y-4">
          {state.clients.map((c, i) => (
            <Card key={i}>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Past client #{i + 1}</CardTitle>
                {state.clients.length > 1 && (
                  <Button variant="ghost" size="sm"
                          aria-label={`Remove client ${i + 1}`}
                          onClick={() => setState((s) => ({ ...s, clients: s.clients.filter((_, j) => j !== i) }))}>
                    Remove
                  </Button>
                )}
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor={`details-${i}`}>Client details</Label>
                  <Textarea id={`details-${i}`} value={c.details}
                            placeholder="Industry, size, who the buyer was, deal size…"
                            onChange={(e) => setClient(i, "details", e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`story-${i}`}>How were they acquired?</Label>
                  <Textarea id={`story-${i}`} value={c.acquisition_story}
                            placeholder="Referral? Cold email? What triggered the deal?"
                            onChange={(e) => setClient(i, "acquisition_story", e.target.value)} />
                </div>
              </CardContent>
            </Card>
          ))}
          <Button variant="outline"
                  onClick={() => setState((s) => ({ ...s, clients: [...s.clients, { details: "", acquisition_story: "" }] }))}>
            + Add another client
          </Button>
        </div>
      )}

      {state.step === "review" && (
        <Card>
          <CardHeader><CardTitle>Ready to launch</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p><span className="text-muted-foreground">Product:</span> {state.product.name}</p>
            {state.local?.businessType && (
              <p>
                <span className="text-muted-foreground">Local business:</span>{" "}
                {businessTypeLabel(state.local.businessType)}, {describeArea(state.local)}
              </p>
            )}
            <p>
              <span className="text-muted-foreground">Flow:</span>{" "}
              {state.hasPastClients
                ? `72-step research pipeline anchored on ${state.clients.length} past client(s)`
                : "144-step pipeline: 72 strategy + 72 GTM research"}
            </p>
            <p className="text-muted-foreground">
              The pipeline runs in the cloud — you can close this tab; progress
              streams to the strategy page.
            </p>
            {basis.data?.available && (
              <div className="space-y-2 rounded border border-border p-3">
                <label className="flex items-start gap-2">
                  <Checkbox checked={autopilot}
                            onCheckedChange={(on) => { setAutopilot(on); if (!on) setLawful(false); }}
                            aria-label="Turn on Autopilot" />
                  <span>
                    <span className="font-medium">Autopilot:</span> {basis.data.label}.
                  </span>
                </label>
                {autopilot && (
                  <label className="flex items-start gap-2">
                    <Checkbox checked={lawful} onCheckedChange={setLawful}
                              aria-label="Confirm I have a lawful basis to contact the people Autopilot finds" />
                    <span>{basis.data.text}</span>
                  </label>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      <div className="flex justify-between">
        <Button variant="outline" onClick={() => setState(prevStep)}
                disabled={state.step === "product" || busy}>
          Back
        </Button>
        {state.step === "review" ? (
          <Button onClick={launch} disabled={busy || !intakeCanLaunch(autopilot, lawful)}>
            {busy ? "Launching…" : "Launch pipeline"}
          </Button>
        ) : (
          <Button onClick={() => setState(nextStep)} disabled={!canAdvance(state)}>
            Continue
          </Button>
        )}
      </div>
    </div>
  );
}
