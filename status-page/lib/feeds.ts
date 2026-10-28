/** /history.rss (RSS 2.0) and /history.atom (Atom 1.0) from the incident files. */

import { IMPACT_LABELS, UPDATE_STATUS_LABELS, type Incident } from "./incidents.ts";
import { formatUtc } from "./format.ts";

const FEED_LIMIT = 50;
const TITLE = "Vectorae status — incident history";

export function escapeXml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function summary(incident: Incident): string {
  const lines = incident.updates.map(
    (u) => `${UPDATE_STATUS_LABELS[u.status]} (${formatUtc(u.at)}): ${u.text}`,
  );
  return [`Impact: ${IMPACT_LABELS[incident.impact]}.`, ...lines].join("\n");
}

function newest(incidents: Incident[]): Incident[] {
  return [...incidents]
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
    .slice(0, FEED_LIMIT);
}

export function buildRss(incidents: Incident[], siteUrl: string, now: Date): string {
  const items = newest(incidents)
    .map((i) => {
      const link = `${siteUrl}/incidents/${i.slug}`;
      return [
        "    <item>",
        `      <title>${escapeXml(i.title)}</title>`,
        `      <link>${escapeXml(link)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(link)}</guid>`,
        `      <pubDate>${new Date(i.updatedAt).toUTCString()}</pubDate>`,
        `      <description>${escapeXml(summary(i))}</description>`,
        "    </item>",
      ].join("\n");
    })
    .join("\n");
  const updated = incidents.length ? newest(incidents)[0]!.updatedAt : now.toISOString();
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    "  <channel>",
    `    <title>${escapeXml(TITLE)}</title>`,
    `    <link>${escapeXml(siteUrl)}/history</link>`,
    `    <atom:link href="${escapeXml(siteUrl)}/history.rss" rel="self" type="application/rss+xml"/>`,
    "    <description>Incidents and maintenance affecting Vectorae.</description>",
    "    <language>en</language>",
    `    <lastBuildDate>${new Date(updated).toUTCString()}</lastBuildDate>`,
    items,
    "  </channel>",
    "</rss>",
    "",
  ]
    .filter((line) => line !== "")
    .join("\n") + "\n";
}

export function buildAtom(incidents: Incident[], siteUrl: string, now: Date): string {
  const list = newest(incidents);
  const updated = list.length ? list[0]!.updatedAt : now.toISOString().replace(/\.\d{3}Z$/, "Z");
  const entries = list
    .map((i) => {
      const link = `${siteUrl}/incidents/${i.slug}`;
      return [
        "  <entry>",
        `    <id>${escapeXml(link)}</id>`,
        `    <title>${escapeXml(i.title)}</title>`,
        `    <link href="${escapeXml(link)}"/>`,
        `    <published>${i.startedAt}</published>`,
        `    <updated>${i.updatedAt}</updated>`,
        `    <content type="text">${escapeXml(summary(i))}</content>`,
        "  </entry>",
      ].join("\n");
    })
    .join("\n");
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<feed xmlns="http://www.w3.org/2005/Atom">',
    `  <id>${escapeXml(siteUrl)}/history.atom</id>`,
    `  <title>${escapeXml(TITLE)}</title>`,
    `  <link href="${escapeXml(siteUrl)}/history.atom" rel="self"/>`,
    `  <link href="${escapeXml(siteUrl)}/history"/>`,
    `  <updated>${updated}</updated>`,
    "  <author><name>Vectorae</name></author>",
    entries,
    "</feed>",
  ]
    .filter((line) => line !== "")
    .join("\n") + "\n";
}
