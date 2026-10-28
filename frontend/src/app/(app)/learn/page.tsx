"use client";

/**
 * Learn Vectorae — the tutorial section (Feature 2).
 *
 * One page, three regions: an overall progress header, the badge shelf, and
 * the categorised catalogue with a search box. Selecting a tutorial opens the
 * player inline above its level rather than navigating away, so a user
 * skimming the list never loses their place.
 *
 * Search and level filtering are done by the SERVER (?q= / ?level=). Nine
 * items would filter fine in the browser today, but two implementations of
 * one filter diverge the moment the catalogue grows.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, RotateCcw, Search } from "lucide-react";
import {
  completeTutorial,
  listTutorials,
  resetTutorialProgress,
  updateTutorialProgress,
} from "@/lib/api/tutorials";
import type { Tutorial, TutorialCatalogue, TutorialLevel } from "@/lib/api/types";
import { formatDuration } from "@/lib/tutorial-progress";
import { AsyncState } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { ProgressBar } from "@/components/tutorials/ProgressBar";
import { BadgeShelf } from "@/components/tutorials/BadgeShelf";
import { VideoPlayer } from "@/components/tutorials/VideoPlayer";

const LEVEL_ORDER: TutorialLevel[] = ["beginner", "intermediate", "advanced"];

export default function LearnPage() {
  const queryClient = useQueryClient();
  const [rawSearch, setRawSearch] = useState("");
  const [search, setSearch] = useState("");
  const [level, setLevel] = useState<TutorialLevel | "">("");
  const [openSlug, setOpenSlug] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /**
   * Debounced so typing "analytics" is one request, not nine.
   *
   * rawSearch drives the input (so it stays responsive) and `search` drives
   * the query key. Binding the query key straight to the input would refetch
   * on every keystroke and make the list flicker between result sets.
   */
  const onSearchChange = useCallback((value: string) => {
    setRawSearch(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => setSearch(value), 250);
  }, []);

  const query = useQuery({
    queryKey: ["tutorials", search, level],
    queryFn: () => listTutorials({ q: search, level: level || undefined }),
    // Progress is written by this same page, so a stale window would show a
    // completion the user just earned as still incomplete.
    staleTime: 0,
  });

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["tutorials"] });
  }, [queryClient]);

  const progressMutation = useMutation({
    mutationFn: ({ slug, position, duration }: {
      slug: string; position: number; duration: number | null;
    }) => updateTutorialProgress(slug, position, duration ?? undefined),
    // Only refetch when something user-visible changed. A heartbeat that
    // refetched the whole catalogue every ten seconds would re-render the list
    // under the player continuously.
    onSuccess: (updated) => {
      if (updated.progress.completed) invalidate();
    },
  });

  const completeMutation = useMutation({
    mutationFn: (slug: string) => completeTutorial(slug),
    onSuccess: invalidate,
  });

  const resetMutation = useMutation({
    mutationFn: (slug: string) => resetTutorialProgress(slug),
    onSuccess: invalidate,
  });

  const handleProgress = useCallback(
    (slug: string) => (position: number, duration: number | null) => {
      progressMutation.mutate({ slug, position, duration });
    },
    [progressMutation],
  );

  const data = query.data;
  const grouped = useMemo(() => groupByLevel(data), [data]);

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Learn Vectorae</h1>
        <p className="text-sm text-muted-foreground">
          Short lessons that take you from first login to a running pipeline.
        </p>
      </header>

      <AsyncState isLoading={query.isLoading} error={query.error}>
        {data && (
          <>
            {/* Overall progress — describes the WHOLE course, so it does not
                move while the user types in the search box. */}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Your progress</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-baseline justify-between">
                  <span
                    data-testid="overall-progress-text"
                    className="text-2xl font-semibold"
                  >
                    {data.summary.completed} / {data.summary.total}
                  </span>
                  <span className="text-sm text-muted-foreground">
                    {data.summary.percent}% complete
                  </span>
                </div>
                <ProgressBar
                  percent={data.summary.percent}
                  label="Overall course progress"
                  tone={data.summary.completed === data.summary.total
                    ? "success" : "primary"}
                />
                <ul className="grid grid-cols-3 gap-3 pt-1">
                  {LEVEL_ORDER.map((lv) => {
                    const s = data.summary.by_level[lv];
                    if (!s) return null;
                    return (
                      <li key={lv} data-testid={`level-summary-${lv}`}>
                        <p className="text-xs text-muted-foreground">{s.label}</p>
                        <p className="text-sm font-medium">
                          {s.completed} / {s.total}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              </CardContent>
            </Card>

            <BadgeShelf badges={data.badges} />

            {/* Search + level filter */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="flex-1 space-y-1">
                <Label htmlFor="tutorial-search">Search tutorials</Label>
                <div className="relative">
                  <Search
                    size={16}
                    aria-hidden="true"
                    className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
                  />
                  <Input
                    id="tutorial-search"
                    type="search"
                    className="pl-9"
                    placeholder="Try “apollo”, “ICP”, “analytics”…"
                    value={rawSearch}
                    onChange={(e) => onSearchChange(e.target.value)}
                  />
                </div>
              </div>
              <div
                className="flex flex-wrap gap-2"
                role="group"
                aria-label="Filter by level"
              >
                <Button
                  type="button"
                  size="sm"
                  variant={level === "" ? "default" : "outline"}
                  aria-pressed={level === ""}
                  onClick={() => setLevel("")}
                >
                  All
                </Button>
                {data.levels.map((l) => (
                  <Button
                    key={l.level}
                    type="button"
                    size="sm"
                    variant={level === l.level ? "default" : "outline"}
                    aria-pressed={level === l.level}
                    onClick={() => setLevel(l.level)}
                  >
                    {l.label}
                  </Button>
                ))}
              </div>
            </div>

            {data.tutorials.length === 0 ? (
              <p
                data-testid="no-results"
                className="rounded border border-dashed border-border p-8 text-center text-sm text-muted-foreground"
              >
                No tutorials match “{rawSearch || search}”.
              </p>
            ) : (
              <div className="space-y-8">
                {LEVEL_ORDER.filter((lv) => grouped[lv]?.length).map((lv) => (
                  <section key={lv} aria-labelledby={`level-${lv}`}
                           data-testid={`section-${lv}`}>
                    <div className="mb-3 flex items-center gap-2">
                      <h2 id={`level-${lv}`} className="text-lg font-semibold">
                        {data.summary.by_level[lv]?.label ?? lv}
                      </h2>
                      <Badge tone="default">{grouped[lv].length}</Badge>
                    </div>
                    <ul className="space-y-3">
                      {grouped[lv].map((tutorial) => (
                        <li key={tutorial.slug}>
                          <TutorialRow
                            tutorial={tutorial}
                            open={openSlug === tutorial.slug}
                            onToggle={() =>
                              setOpenSlug((current) =>
                                current === tutorial.slug ? null : tutorial.slug,
                              )
                            }
                            onProgress={handleProgress(tutorial.slug)}
                            onComplete={() => completeMutation.mutate(tutorial.slug)}
                            onReset={() => resetMutation.mutate(tutorial.slug)}
                            busy={
                              completeMutation.isPending || resetMutation.isPending
                            }
                          />
                        </li>
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </AsyncState>
    </div>
  );
}

// --------------------------------------------------------------------------

function groupByLevel(
  data: TutorialCatalogue | undefined,
): Record<TutorialLevel, Tutorial[]> {
  const out = { beginner: [], intermediate: [], advanced: [] } as Record<
    TutorialLevel,
    Tutorial[]
  >;
  for (const tutorial of data?.tutorials ?? []) {
    (out[tutorial.level] ??= []).push(tutorial);
  }
  return out;
}

function TutorialRow({
  tutorial, open, onToggle, onProgress, onComplete, onReset, busy,
}: {
  tutorial: Tutorial;
  open: boolean;
  onToggle: () => void;
  onProgress: (position: number, duration: number | null) => void;
  onComplete: () => void;
  onReset: () => void;
  busy: boolean;
}) {
  const { progress } = tutorial;
  return (
    <Card data-testid={`tutorial-${tutorial.slug}`}>
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-base">
              {progress.completed && (
                <CheckCircle2
                  size={16}
                  className="shrink-0 text-success"
                  aria-label="Completed"
                />
              )}
              <span className="truncate">{tutorial.title}</span>
            </CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              {tutorial.description}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            {tutorial.is_placeholder && (
              <Badge tone="warning">Coming soon</Badge>
            )}
            <span className="text-xs text-muted-foreground">
              {formatDuration(tutorial.duration_seconds)}
            </span>
            <Button
              type="button"
              size="sm"
              variant={open ? "outline" : "default"}
              aria-expanded={open}
              onClick={onToggle}
            >
              {open ? "Close" : progress.started && !progress.completed
                ? "Resume" : "Watch"}
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="flex items-center gap-3">
          <ProgressBar
            percent={progress.percent}
            label={`${tutorial.title} progress`}
            tone={progress.completed ? "success" : "primary"}
            className="flex-1"
          />
          <span
            data-testid={`percent-${tutorial.slug}`}
            className="w-12 shrink-0 text-right text-xs text-muted-foreground"
          >
            {Math.round(progress.percent)}%
          </span>
        </div>

        {open && (
          <div className="space-y-3 pt-1">
            <VideoPlayer tutorial={tutorial} onProgress={onProgress} />
            <div className="flex flex-wrap gap-2">
              {!progress.completed && (
                <Button type="button" size="sm" onClick={onComplete} disabled={busy}>
                  <CheckCircle2 size={16} className="mr-1" aria-hidden="true" />
                  Mark complete
                </Button>
              )}
              {progress.started && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={onReset}
                  disabled={busy}
                >
                  <RotateCcw size={16} className="mr-1" aria-hidden="true" />
                  Reset progress
                </Button>
              )}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
