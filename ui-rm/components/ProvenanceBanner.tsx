"use client";

import { useEffect, useState } from "react";

import { health } from "../lib/api";

/**
 * The provenance this shell states at the top of every page: WHERE the portal is running
 * and WHICH model answers it (org decision, 2026-08-30).
 *
 * The portal is a launcher and a BFF: it generates nothing, so its model reads `no-model`.
 * That does NOT make the banner redundant here, it makes it the more useful of the two a
 * viewer sees. Every page that shows generated content is an embedded app, and each of
 * those renders its own banner from its own healthz, inside its own frame. The two answers
 * can legitimately differ -- a portal on GCP mounting an app running on a laptop, or the
 * reverse -- and a reviewer reading a dossier in a frame needs both facts, not whichever
 * one happened to be on screen.
 *
 * Nothing here is inferred in the browser. A shell that read its runtime from
 * `window.location` would be right until the deployment served through a proxy and wrong
 * silently after that.
 */

/** The wording, spelled once. The canonical copy lives in `hex-service-template`. */
export function provenance(runtime: string, model: string): string {
  const where = runtime === "gcp" ? "running on GCP" : "running locally";
  return `${where} · model ${model}`;
}

export function ProvenanceBanner() {
  const [origin, setOrigin] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    health()
      .then((status) => {
        if (!live || !status?.runtime) return;
        setOrigin(provenance(status.runtime, status.generator_model));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  // Null until the BFF has answered, and null again if it never does. Defaulting to
  // "running locally" during the fetch would state a falsehood on every deployment page
  // load, and a shell that guessed would be asserting provenance it does not have.
  if (!origin) return null;
  return <p className="provenance-banner">{origin}</p>;
}
