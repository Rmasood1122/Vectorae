"use client";

/** Settings > Voice > "Your voice on calls" (docs/features/voice-twin.md).
 *
 *  Written consent first, then recordings (uploaded or recorded here), then
 *  ElevenLabs' own voice verification, training and "ready". The provider's
 *  status is shown as it is. Every call opens by saying it is your AI
 *  assistant using your voice and that the call is recorded -- the page says
 *  so, and the server enforces it whatever this page sends. */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getVoiceClone,
  getVoiceTwinSettings,
  getVoiceVerification,
  previewVoiceClone,
  refreshVoiceClone,
  resumeAiCalls,
  revokeVoiceClone,
  saveVoiceTwinSettings,
  sendVoiceVerification,
  setUseMyVoice,
  signVoiceConsent,
  submitVoiceSamples,
  uploadVoiceSample,
} from "@/lib/api/voiceTwin";
import {
  REVOKE_CONFIRM,
  canRecord,
  cloneStep,
  consentError,
  consentTextFor,
  extensionFor,
  pickRecorderType,
  sampleError,
  settingsBody,
  statusLabel,
  type VerificationChallenge,
  type VoiceTwinState,
} from "@/lib/voiceTwin";
import { InboundCalls } from "@/components/calls/CallHistory";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, Label, Textarea } from "@/components/ui/input";
import { AsyncState } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

const KEY = ["voice-clone"];

function useRecorder() {
  const [recording, setRecording] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const supported = typeof window !== "undefined" && canRecord(window as never);

  // L7: leaving the page mid-recording must release the microphone.
  useEffect(() => () => {
    const r = recorder.current;
    if (r) {
      try { if (r.state !== "inactive") r.stop(); } catch { /* already stopped */ }
      r.stream.getTracks().forEach((t) => t.stop());
    }
  }, []);

  async function start() {
    const type = pickRecorderType((t) => MediaRecorder.isTypeSupported(t));
    if (!type) throw new Error("This browser cannot record audio here — upload a file instead.");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    chunks.current = [];
    const r = new MediaRecorder(stream, { mimeType: type });
    r.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data); };
    recorder.current = r;
    r.start();
    setRecording(true);
  }

  function stop(): Promise<Blob> {
    return new Promise((resolve) => {
      const r = recorder.current;
      if (!r) return resolve(new Blob());
      r.onstop = () => {
        r.stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        resolve(new Blob(chunks.current, { type: r.mimeType }));
      };
      r.stop();
    });
  }

  return { supported, recording, start, stop };
}

export function VoiceTwinCard() {
  const toast = useToast();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: KEY, queryFn: getVoiceClone });
  const onState = (data: VoiceTwinState) => qc.setQueryData(KEY, data);
  const fail = (e: unknown) => toast((e as Error).message, "error");

  return (
    <AsyncState isLoading={query.isLoading} error={query.error}>
      {query.data && (
        <div className="space-y-4">
          <CloneCard state={query.data} onState={onState} fail={fail} />
          <AccountCard fail={fail} />
        </div>
      )}
    </AsyncState>
  );
}

function CloneCard({ state, onState, fail }: {
  state: VoiceTwinState;
  onState: (s: VoiceTwinState) => void;
  fail: (e: unknown) => void;
}) {
  const toast = useToast();
  const step = cloneStep(state);
  const clone = state.clone;
  const [signature, setSignature] = useState("");
  const [ownVoice, setOwnVoice] = useState(false);
  const [challenge, setChallenge] = useState<VerificationChallenge | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const rec = useRecorder();

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  const consent = useMutation({
    mutationFn: () => signVoiceConsent(signature, ownVoice, state.consent.version,
                                       state.consent.template),
    onSuccess: (s) => { onState(s); toast("Consent signed", "success"); },
    onError: fail,
  });
  const upload = useMutation({
    mutationFn: async (files: { blob: Blob; name: string }[]) => {
      let last: VoiceTwinState = state;
      let used = { count: clone?.sample_count ?? 0, bytes: clone?.sample_bytes ?? 0 };
      for (const f of files) {
        const problem = sampleError({ size: f.blob.size, type: f.blob.type }, state.limits, used);
        if (problem) throw new Error(`${f.name}: ${problem}`);
        last = await uploadVoiceSample(f.blob, f.name);
        used = { count: last.clone?.sample_count ?? 0, bytes: last.clone?.sample_bytes ?? 0 };
      }
      return last;
    },
    onSuccess: (s) => { onState(s); toast("Recording sent to ElevenLabs — not kept by Vectorae", "success"); },
    onError: fail,
  });
  const simple = (fn: () => Promise<VoiceTwinState>, ok?: string) => ({
    mutationFn: fn,
    onSuccess: (s: VoiceTwinState) => { onState(s); if (ok) toast(ok, "success"); },
    onError: fail,
  });
  const submit = useMutation(simple(submitVoiceSamples));
  const refresh = useMutation(simple(refreshVoiceClone));
  const verify = useMutation(simple(async () => {
    const blob = await rec.stop();
    return sendVoiceVerification(blob);
  }, "Verification sent"));
  const use = useMutation({
    mutationFn: (enabled: boolean) => setUseMyVoice(enabled),
    onSuccess: onState,
    onError: fail,
  });
  const revoke = useMutation(simple(revokeVoiceClone, "Voice clone revoked"));
  const loadChallenge = useMutation({ mutationFn: getVoiceVerification, onSuccess: setChallenge, onError: fail });
  const preview = useMutation({
    mutationFn: previewVoiceClone,
    onSuccess: (blob) => setPreviewUrl(URL.createObjectURL(blob)),
    onError: fail,
  });

  const signError = consentError(signature, ownVoice);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Your voice on calls</CardTitle>
        <p className="text-sm text-muted-foreground">
          Let your AI assistant speak in your own voice on AI calls. It sounds natural — short
          turns, it lets people interrupt — but it never pretends to be you or a human. Every call
          opens with: <q className="italic">{state.opening_line}</q>
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={clone?.status === "ready" ? "success" : clone?.status === "failed" ? "destructive" : "default"}>
            {statusLabel(clone?.status)}
          </Badge>
          {clone && clone.sample_count > 0 && (
            <span className="text-muted-foreground">{clone.sample_count} recording(s) uploaded</span>
          )}
        </div>
        {clone?.error && <p role="alert" className="text-destructive">{clone.error}</p>}

        {step === "disabled" && (
          <p className="text-muted-foreground">
            Voice cloning is not enabled on this Vectorae. An administrator turns it on (it needs
            AI calling on too).
          </p>
        )}

        {step === "consent" && (
          <div className="space-y-3">
            <p className="font-medium">Your written consent (version {state.consent.version})</p>
            <p className="whitespace-pre-line rounded border border-border bg-muted/40 p-3">
              {consentTextFor(state.consent.template, signature)}
            </p>
            <label className="flex items-center gap-2">
              <Checkbox checked={ownVoice} onCheckedChange={setOwnVoice} />
              This is my own voice
            </label>
            <div className="space-y-1">
              <Label htmlFor="vt-signature">Type your full name to sign</Label>
              <Input id="vt-signature" value={signature} maxLength={200}
                     onChange={(e) => setSignature(e.target.value)} />
            </div>
            <Button size="sm" disabled={!!signError || consent.isPending} onClick={() => consent.mutate()}>
              {consent.isPending ? "Signing…" : "Sign consent"}
            </Button>
          </div>
        )}

        {step === "upload" && (
          <div className="space-y-3">
            <p>
              Add recordings of you speaking: clear audio, one speaker, no music. ElevenLabs
              recommends at least 30 minutes in total for a professional clone. Files go straight to
              ElevenLabs; Vectorae does not keep them.
            </p>
            <Input type="file" accept={state.limits.types.join(",")} multiple aria-label="Upload recordings"
                   disabled={upload.isPending}
                   onChange={(e) => {
                     const files = Array.from(e.target.files ?? []).map((f) => ({ blob: f as Blob, name: f.name }));
                     if (files.length) upload.mutate(files);
                     e.target.value = "";
                   }} />
            {rec.supported ? (
              <Button size="sm" variant="ghost" disabled={upload.isPending}
                      onClick={async () => {
                        try {
                          if (!rec.recording) return await rec.start();
                          const blob = await rec.stop();
                          upload.mutate([{ blob, name: `recording.${extensionFor(blob.type)}` }]);
                        } catch (e) { fail(e); }
                      }}>
                {rec.recording ? "Stop and upload" : "Record in the browser"}
              </Button>
            ) : (
              <p className="text-muted-foreground">Recording here is not supported in this browser — upload files instead.</p>
            )}
            <Button size="sm" disabled={!clone?.sample_count || submit.isPending} onClick={() => submit.mutate()}>
              I&apos;m done — verify my voice
            </Button>
          </div>
        )}

        {step === "verify" && (
          <div className="space-y-3">
            <p>
              ElevenLabs checks that the voice is yours: you read a short text aloud yourself.
            </p>
            <Button size="sm" variant="ghost" onClick={() => loadChallenge.mutate()}>Show the text to read</Button>
            {challenge?.kind === "image" && challenge.image && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={challenge.image} alt="Text to read aloud" className="max-w-full rounded border border-border" />
            )}
            {challenge?.kind === "text" && <p className="rounded border border-border p-3 font-medium">{challenge.text}</p>}
            {challenge && challenge.kind !== "unavailable" && rec.supported && (
              <Button size="sm" onClick={async () => {
                try { if (!rec.recording) await rec.start(); else verify.mutate(); } catch (e) { fail(e); }
              }}>
                {rec.recording ? "Stop and send" : "Record my reading"}
              </Button>
            )}
            {(challenge?.kind === "unavailable" || (challenge && !rec.supported)) && (
              <p className="text-muted-foreground">{challenge.fallback}</p>
            )}
            {!challenge && <p className="text-muted-foreground">{state.verify_on_elevenlabs}</p>}
            <Button size="sm" variant="ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
              Check status
            </Button>
          </div>
        )}

        {step === "training" && (
          <Button size="sm" variant="ghost" disabled={refresh.isPending} onClick={() => refresh.mutate()}>
            Check status
          </Button>
        )}

        {step === "ready" && (
          <div className="space-y-3">
            <Button size="sm" variant="ghost" disabled={preview.isPending} onClick={() => preview.mutate()}>
              Hear your AI assistant&apos;s opening line
            </Button>
            {previewUrl && <audio controls src={previewUrl} className="w-full" />}
            <label className="flex items-center gap-2">
              <Checkbox checked={state.settings.use_clone_for_calls} disabled={use.isPending || !state.feature_enabled}
                        onCheckedChange={(v) => use.mutate(v)} />
              Use my voice for AI calls
            </label>
          </div>
        )}

        {step === "revoking" && (
          <p className="text-muted-foreground">
            Your voice is no longer used. Vectorae keeps retrying the delete at ElevenLabs until it succeeds.
          </p>
        )}

        {clone && clone.status !== "revoked" && clone.status !== "revoke_pending" && (
          <Button size="sm" variant="ghost" className="text-destructive" disabled={revoke.isPending}
                  onClick={() => { if (window.confirm(REVOKE_CONFIRM)) revoke.mutate(); }}>
            Revoke consent and delete my voice
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function AccountCard({ fail }: { fail: (e: unknown) => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const query = useQuery({ queryKey: ["voice-twin-settings"], queryFn: getVoiceTwinSettings });
  const [form, setForm] = useState({ answer_inbound: false, transfer_number: "", assistant_notes: "" });

  useEffect(() => {
    if (query.data) {
      setForm({
        answer_inbound: query.data.answer_inbound,
        transfer_number: query.data.transfer_number ?? "",
        assistant_notes: query.data.assistant_notes ?? "",
      });
    }
  }, [query.data]);

  const save = useMutation({
    mutationFn: () => saveVoiceTwinSettings(settingsBody(form, query.data!)),
    onSuccess: (data) => { qc.setQueryData(["voice-twin-settings"], data); toast("Saved", "success"); },
    onError: fail,
  });
  const resume = useMutation({
    mutationFn: resumeAiCalls,
    onSuccess: (data) => { qc.setQueryData(["voice-twin-settings"], data); toast("AI calls resumed", "success"); },
    onError: fail,
  });
  const data = query.data;
  if (!data) return null;
  const changed = Object.keys(settingsBody(form, data)).length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">AI answering and take-over</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {data.paused && (
          <div role="alert" className="space-y-2 rounded border border-destructive/50 p-3">
            <p className="font-medium text-destructive">AI calls are paused.</p>
            <p>{data.paused_reason}</p>
            <Button size="sm" disabled={resume.isPending} onClick={() => resume.mutate()}>
              I read the transcript — resume AI calls
            </Button>
          </div>
        )}
        <label className="flex items-center gap-2">
          <Checkbox checked={form.answer_inbound} disabled={!data.feature_enabled}
                    onCheckedChange={(v) => setForm((f) => ({ ...f, answer_inbound: v }))} />
          Answer my calls with my AI assistant
        </label>
        <p className="text-muted-foreground">
          Only calls to your own configured AI-calling number. The assistant says it is an AI, can
          book a meeting, take a message, or put the caller through to you.
          {!data.own_number_configured && " Your account has no number of its own yet — ask your Vectorae administrator to assign one."}
        </p>
        <div className="space-y-1">
          <Label htmlFor="vt-transfer">Your phone number (for take-over and transfers)</Label>
          <Input id="vt-transfer" inputMode="tel" placeholder="+1 555 010 2000" value={form.transfer_number}
                 disabled={data.can_edit_transfer_number === false}
                 onChange={(e) => setForm((f) => ({ ...f, transfer_number: e.target.value }))} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="vt-notes">Notes for your assistant (hours, what you do)</Label>
          <Textarea id="vt-notes" rows={3} maxLength={2000} value={form.assistant_notes}
                    onChange={(e) => setForm((f) => ({ ...f, assistant_notes: e.target.value }))} />
          <p className="text-xs text-muted-foreground">
            Instructions to hide that it is an AI are refused.
          </p>
        </div>
        <Button size="sm" disabled={!changed || save.isPending} onClick={() => save.mutate()}>Save</Button>
        <p className="pt-2 font-medium">Calls your assistant answered</p>
        <InboundCalls />
      </CardContent>
    </Card>
  );
}
