"use client";

/**
 * The home page product video (public/media/vectorae-walkthrough.*, made by
 * frontend/video-src/render.mjs). Muted, looping, inline, poster first,
 * preload="metadata".
 *
 * It starts by itself only when the visitor has not asked for reduced motion
 * (src/lib/homeMotion.ts shouldPlay), which is why play() is called from
 * script instead of putting `autoplay` in the HTML: the attribute would start
 * the video before React could check the preference. Without JavaScript the
 * browser's own controls are shown instead of ours.
 *
 * Accessibility: a visible Pause/Play button and a Captions button (the
 * WebVTT track describes each scene; there is no sound), a text description
 * tied to the video with aria-describedby, and the full transcript below it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Captions, Pause, Play } from "lucide-react";

import { type PlayChoice, prefersReducedMotion, shouldPlay } from "@/lib/homeMotion";
import { VIDEO } from "./content";

export function HomeVideo() {
  const ref = useRef<HTMLVideoElement>(null);
  const [enhanced, setEnhanced] = useState(false); // our controls replace the native ones
  const [playing, setPlaying] = useState(false);
  const [captionsOn, setCaptionsOn] = useState(true);
  const choice = useRef<PlayChoice>(null);
  const inView = useRef(true);
  const reduced = useRef(false);

  const apply = useCallback(() => {
    const v = ref.current;
    if (!v) return;
    const want = shouldPlay({ reducedMotion: reduced.current, choice: choice.current, inView: inView.current });
    if (want && v.paused) {
      const p = v.play();
      // A browser may still refuse (data saver, low power): stay on the poster.
      if (p && typeof p.catch === "function") p.catch(() => setPlaying(false));
    } else if (!want && !v.paused) {
      v.pause();
    }
  }, []);

  useEffect(() => {
    setEnhanced(true);
    reduced.current = prefersReducedMotion();
    const v = ref.current;
    let io: IntersectionObserver | null = null;
    if (v && typeof IntersectionObserver !== "undefined") {
      io = new IntersectionObserver(([entry]) => {
        inView.current = entry.isIntersecting;
        apply();
      }, { threshold: 0.25 });
      io.observe(v);
    }
    apply();
    return () => io?.disconnect();
  }, [apply]);

  useEffect(() => {
    const track = ref.current?.textTracks?.[0];
    if (track) track.mode = captionsOn ? "showing" : "hidden";
  }, [captionsOn, enhanced]);

  function toggle() {
    choice.current = playing ? "pause" : "play";
    apply();
  }

  return (
    <figure className="m-0">
      <div className="lp-video-frame relative overflow-hidden rounded-2xl bg-[#0b1324] ring-1 ring-white/15">
        <video
          ref={ref}
          className="block aspect-video w-full"
          muted
          loop
          playsInline
          preload="metadata"
          poster={VIDEO.poster}
          controls={!enhanced}
          aria-label={VIDEO.label}
          aria-describedby="home-video-desc"
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          data-testid="home-video"
        >
          <source src={VIDEO.mp4} type="video/mp4" />
          <source src={VIDEO.webm} type="video/webm" />
          <track kind="captions" src={VIDEO.captions} srcLang="en" label="English" default />
        </video>
      </div>
      <figcaption className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-[#cbd5e1]">
        {enhanced && (
          <span className="flex gap-2">
            <button
              type="button"
              onClick={toggle}
              className="lp-chip"
              aria-label={playing ? "Pause video" : "Play video"}
              data-testid="home-video-toggle"
            >
              {playing ? <Pause aria-hidden className="h-4 w-4" /> : <Play aria-hidden className="h-4 w-4" />}
              {playing ? "Pause" : "Play"}
            </button>
            <button
              type="button"
              onClick={() => setCaptionsOn((c) => !c)}
              className="lp-chip"
              aria-pressed={captionsOn}
              data-testid="home-video-captions"
            >
              <Captions aria-hidden className="h-4 w-4" />
              Captions
            </button>
          </span>
        )}
        <span id="home-video-desc">{VIDEO.description}</span>
      </figcaption>
      <details className="lp-transcript mt-2 text-sm text-[#cbd5e1]">
        <summary className="cursor-pointer select-none rounded py-1 text-[#e2e8f0] underline decoration-white/30 underline-offset-4 hover:decoration-white">
          Read what the video shows
        </summary>
        <ol className="mt-3 list-decimal space-y-1.5 pl-5" data-testid="home-video-transcript">
          {VIDEO.transcript.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ol>
      </details>
    </figure>
  );
}
