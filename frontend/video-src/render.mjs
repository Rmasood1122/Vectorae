#!/usr/bin/env node
/**
 * Renders video-src/walkthrough.html into the home-page video.
 *
 *   node video-src/render.mjs            (run from frontend/)
 *
 * Output (committed, served from the static export):
 *   public/media/vectorae-walkthrough.mp4     H.264, yuv420p, faststart, no audio
 *   public/media/vectorae-walkthrough.webm    VP9, no audio
 *   public/media/vectorae-walkthrough-poster.jpg
 *
 * The captions file public/media/vectorae-walkthrough.en.vtt is written by
 * hand next to them; src/tests/homeVideo.test.ts checks that its cues match
 * the transcript on the page and that the files exist and stay small.
 *
 * How: the page draws any instant with window.render(t). We step t at FPS,
 * screenshot each frame with Playwright (Chromium), then encode with ffmpeg.
 * Deterministic: no wall-clock animation is involved.
 *
 * Needs: Playwright with a Chromium (CHROMIUM_PATH overrides the binary) and
 * ffmpeg with libx264 + libvpx-vp9 on PATH (FFMPEG overrides). FRAMES_TMP sets
 * where the PNG frames are written (default: the OS temp dir).
 * This directory is NOT part of the export: Next only ships src/ and public/.
 */
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, "../public/media");
const NAME = "vectorae-walkthrough";
const FPS = 30;
const W = 1280;
const H = 720;
const POSTER_T = 25.4; // the filled calendar and the closing line
const FFMPEG = process.env.FFMPEG || "ffmpeg";

function run(cmd, args) {
  const r = spawnSync(cmd, args, { stdio: ["ignore", "inherit", "inherit"] });
  if (r.status !== 0) throw new Error(`${cmd} exited with ${r.status}`);
}

const frames = mkdtempSync(path.join(process.env.FRAMES_TMP || tmpdir(), "lp-frames-"));
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
try {
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(path.join(here, "walkthrough.html")).href);
  await page.evaluate(() => document.fonts.ready);
  const duration = await page.evaluate(() => window.DURATION);
  const total = Math.round(duration * FPS);
  for (let i = 0; i < total; i++) {
    await page.evaluate((t) => window.render(t), i / FPS);
    await page.screenshot({ path: path.join(frames, `f${String(i).padStart(5, "0")}.png`) });
  }
  await page.evaluate((t) => window.render(t), POSTER_T);
  await page.screenshot({ path: path.join(frames, "poster.png") });
  console.log(`rendered ${total} frames (${duration}s at ${FPS} fps)`);
} finally {
  await browser.close();
}

const input = ["-y", "-hide_banner", "-loglevel", "error", "-framerate", String(FPS), "-i", path.join(frames, "f%05d.png")];
run(FFMPEG, [...input, "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-pix_fmt", "yuv420p",
  "-profile:v", "high", "-movflags", "+faststart", path.join(OUT, `${NAME}.mp4`)]);
run(FFMPEG, [...input, "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-b:v", "0", "-crf", "42", "-row-mt", "1",
  "-deadline", "good", "-cpu-used", "2", path.join(OUT, `${NAME}.webm`)]);
run(FFMPEG, ["-y", "-hide_banner", "-loglevel", "error", "-i", path.join(frames, "poster.png"),
  "-q:v", "4", path.join(OUT, `${NAME}-poster.jpg`)]);
rmSync(frames, { recursive: true, force: true });

for (const ext of [".mp4", ".webm", "-poster.jpg"]) {
  const f = path.join(OUT, NAME + ext);
  console.log(`${path.relative(process.cwd(), f)}  ${(statSync(f).size / 1024).toFixed(0)} KB`);
}
