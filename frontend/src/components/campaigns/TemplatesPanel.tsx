"use client";

/**
 * WhatsApp template manager (Campaigns → Templates).
 *
 * Templates belong to the whole install (one Meta WhatsApp Business Account),
 * so only an admin creates, edits, generates, submits and syncs them
 * (F-SEC-05; the API enforces it). Everyone else sees the approved templates,
 * which is all the API returns to them.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTemplates } from "@/lib/api/hooks";
import { generateTemplates, submitTemplate, syncTemplate } from "@/lib/api/campaigns";
import type { TemplateOut } from "@/lib/api/types";
import { AsyncState } from "@/components/ui/skeleton";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Modal } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { TemplateEditor } from "@/components/campaigns/TemplateEditor";

export function TemplatesPanel({ strategyId, canManage }: {
  strategyId: string;
  canManage: boolean;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const { data, isLoading, error } = useTemplates();
  const templates = data?.templates ?? [];
  const [editing, setEditing] = useState<TemplateOut | null | "new">(null);
  const [generating, setGenerating] = useState(false);
  const [aiDrafts, setAiDrafts] = useState<TemplateOut[]>([]);

  async function generate() {
    setGenerating(true);
    try {
      const r = await generateTemplates(strategyId);
      setAiDrafts(r.templates);
      toast(`${r.templates.length} draft(s) generated — pick one to use`, "success");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setGenerating(false);
    }
  }

  async function submit(id: string) {
    try {
      await submitTemplate(id);
      qc.invalidateQueries({ queryKey: ["wa-templates"] });
      toast("Submitted to Meta. Approval takes minutes to days.", "info");
    } catch (err) {
      toast((err as Error).message, "error");
    }
  }

  async function sync(id: string) {
    try {
      const t = await syncTemplate(id);
      qc.invalidateQueries({ queryKey: ["wa-templates"] });
      toast(`Status: ${t.status}`, "info");
    } catch (err) {
      toast((err as Error).message, "error");
    }
  }

  return (
    <div className="space-y-4">
      {canManage ? (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setEditing("new")}>+ New draft</Button>
          <Button size="sm" variant="outline" onClick={generate} disabled={generating}>
            {generating ? "Generating…" : "Generate with AI"}
          </Button>
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          WhatsApp templates are shared by everyone on this install, so an admin
          creates and submits them. Approved templates are listed here.
        </p>
      )}

      {aiDrafts.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">AI-generated drafts — pick one to use</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            {aiDrafts.map((t, i) => (
              <details key={i} className="rounded border border-border p-2 text-sm">
                <summary className="cursor-pointer font-medium">{t.name}</summary>
                <pre className="mt-2 whitespace-pre-wrap text-xs">{t.body}</pre>
                <Button size="sm" className="mt-2"
                        onClick={() => { setEditing(t); setAiDrafts([]); }}>
                  Edit & save this draft
                </Button>
              </details>
            ))}
          </CardContent>
        </Card>
      )}

      <AsyncState isLoading={isLoading} error={error}
                  empty={!templates.length} emptyLabel={canManage ? "No templates yet." : "No approved templates yet."}>
        <div className="space-y-2">
          {templates.map(t => (
            <Card key={t.id}>
              <CardContent className="flex flex-wrap items-center justify-between gap-2 p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-sm">{t.name}</span>
                    <Badge tone={statusTone(t.status)}>{t.status}</Badge>
                    {t.version && t.version > 1 && (
                      <span className="text-xs text-muted-foreground">v{t.version}</span>
                    )}
                  </div>
                  {t.rejection_reason && (
                    <p className="mt-1 text-xs text-destructive">
                      Rejected: {t.rejection_reason}
                    </p>
                  )}
                </div>
                {canManage && <div className="flex gap-1">
                  {(t.status === "draft" || t.status === "rejected") && (
                    <>
                      <Button size="sm" variant="outline"
                              onClick={() => setEditing(t)}>Edit</Button>
                      <Button size="sm"
                              onClick={() => submit(t.id)}>Submit</Button>
                    </>
                  )}
                  {t.status === "submitted" && (
                    <Button size="sm" variant="outline"
                            onClick={() => sync(t.id)}>Sync status</Button>
                  )}
                </div>}
              </CardContent>
            </Card>
          ))}
        </div>
      </AsyncState>

      <Modal open={!!editing} onClose={() => setEditing(null)}
             title={editing === "new" ? "New template" : "Edit draft"}>
        {editing && (
          <TemplateEditor
            initial={editing === "new" ? null : editing}
            onSave={() => {
              qc.invalidateQueries({ queryKey: ["wa-templates"] });
              setEditing(null);
            }}
            onClose={() => setEditing(null)}
          />
        )}
      </Modal>
    </div>
  );
}
