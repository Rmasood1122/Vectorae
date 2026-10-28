import { readEnv, RECENT_INCIDENT_DAYS } from "../lib/config.ts";
import { formatDay } from "../lib/format.ts";
import { activeIncidents, incidentsDir, loadIncidents, recentIncidents } from "../lib/incidents.ts";
import { getSnapshot } from "../lib/snapshot.ts";
import { STATUS_LABELS } from "../lib/status.ts";
import { IncidentCard } from "../components/IncidentCard.tsx";
import { LocalTime } from "../components/LocalTime.tsx";
import { StatusIcon } from "../components/StatusIcon.tsx";
import { UptimeBars } from "../components/UptimeBars.tsx";

// Re-render at most once a minute (Incremental Static Regeneration): the
// UptimeRobot key never reaches the browser and the free plan's 10
// requests/minute are never approached however many people visit.
// Keep equal to REVALIDATE_SECONDS in lib/config.ts.
export const revalidate = 60;

function lastDays(now: Date, count: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < count; i++) out.push(new Date(now.getTime() - i * 86_400_000).toISOString().slice(0, 10));
  return out;
}

export default async function Home() {
  const now = new Date();
  const incidents = loadIncidents(incidentsDir(), now);
  const snap = await getSnapshot(readEnv(), { fetch, now, incidents });
  const active = activeIncidents(incidents);
  const recent = recentIncidents(incidents, now, RECENT_INCIDENT_DAYS);

  return (
    <>
      {snap.dataNotice && (
        <section className="notice" aria-labelledby="notice-title">
          <StatusIcon status="unknown" size={22} />
          <div>
            {/* Not a heading: it sits above the page's h1 (heading order). */}
            <p id="notice-title" className="notice-title">
              <strong>{snap.dataNotice.title}</strong>
            </p>
            <p>{snap.dataNotice.text}</p>
          </div>
        </section>
      )}

      <section className={`banner tone-${snap.overall.tone}`} aria-labelledby="overall">
        <StatusIcon status={snap.overall.tone} size={28} />
        <div>
          <h1 id="overall">{snap.overall.title}</h1>
          <p>
            Updated <LocalTime iso={snap.generatedAt} />. This page refreshes its data every minute; reload to see
            the latest.
          </p>
        </div>
      </section>

      {active.length > 0 && (
        <section aria-labelledby="active-incidents">
          <h2 id="active-incidents">Active incidents</h2>
          {active.map((i) => (
            <IncidentCard key={i.slug} incident={i} />
          ))}
        </section>
      )}

      <section aria-labelledby="components-title">
        <h2 id="components-title">Components</h2>
        <ul className="components">
          {snap.components.map((c) => (
            <li key={c.id} className="component">
              <div className="component-head">
                <div>
                  <h3>{c.name}</h3>
                  <p className="desc">{c.description}</p>
                </div>
                <span className={`status-label s-${c.status}`}>
                  <StatusIcon status={c.status} />
                  {STATUS_LABELS[c.status]}
                </span>
              </div>
              {c.note && <p className="note">{c.note}</p>}
              {c.source === "incident" && <p className="note">Set by an open incident below.</p>}
              {c.checkedAt && (
                <p className="note">
                  Checked <LocalTime iso={c.checkedAt} />
                </p>
              )}
              {c.uptime ? (
                <UptimeBars name={c.name} uptime={c.uptime} />
              ) : c.id === "ai-features" ? (
                <p className="note">No external uptime history: this is checked by Vectorae itself.</p>
              ) : null}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="past-incidents">
        <h2 id="past-incidents">Past {RECENT_INCIDENT_DAYS} days</h2>
        {lastDays(now, RECENT_INCIDENT_DAYS).map((day) => {
          const started = recent.filter((i) => i.startedAt.slice(0, 10) === day);
          return (
            <div key={day} className="day">
              <h3>{formatDay(day)}</h3>
              {started.length === 0 ? (
                <p className="none">No incidents reported.</p>
              ) : (
                started.map((i) => <IncidentCard key={i.slug} incident={i} showAllUpdates={false} />)
              )}
            </div>
          );
        })}
      </section>
    </>
  );
}
