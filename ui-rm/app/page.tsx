"use client";

// The journey shell, serving whichever journey NEXT_PUBLIC_JOURNEY names. Deliberately THIN:
// it renders a persona switcher and a tabbed set of
// same-origin iframes over the portal BFF, and nothing else. All identity, proxying and config
// live in the BFF; the shell just composes. Its small size IS the point - it is the evidence that
// the agents drop into an existing React app with a couple of hundred lines of host code.

import { useCallback, useEffect, useState } from "react";

import { listJourneys, listPersonas, setPersona, whoami } from "@/lib/api";
import type { JourneyModel, Persona, WhoAmI } from "@/lib/types";

const JOURNEY_KEY = process.env.NEXT_PUBLIC_JOURNEY || "rm";

export default function Page() {
  const [journey, setJourney] = useState<JourneyModel | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [me, setMe] = useState<WhoAmI | null>(null);
  const [activeApp, setActiveApp] = useState<string>("");
  const [reloadKey, setReloadKey] = useState(0);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    (async () => {
      try {
        const [journeys, ps, who] = await Promise.all([listJourneys(), listPersonas(), whoami()]);
        const found = journeys.find((j) => j.key === JOURNEY_KEY) ?? journeys[0] ?? null;
        setJourney(found);
        setActiveApp(found?.apps[0]?.id ?? "");
        setPersonas(ps);
        setMe(who);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, []);

  const onPersona = useCallback(async (id: string) => {
    try {
      setMe(await setPersona(id));
      // The embedded apps read identity from the portal cookie on their next API call, so a fresh
      // iframe load picks up the new persona everywhere at once.
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  if (error) {
    return (
      <main className="shell">
        <p className="error">Portal error: {error}. Is the BFF running on :8110?</p>
      </main>
    );
  }
  if (!journey) {
    return (
      <main className="shell">
        <p className="loading">Loading journey…</p>
      </main>
    );
  }

  const app = journey.apps.find((a) => a.id === activeApp) ?? journey.apps[0];

  return (
    <main className="shell" data-demo={`${JOURNEY_KEY}-journey`}>
      <header className="topbar">
        <div className="brand">
          {/* The mark names the journey being served, because one shell serves them all. */}
          <span className="tier">{JOURNEY_KEY.toUpperCase()}</span>
          <div>
            <h1>{journey.label}</h1>
            <p>{journey.blurb}</p>
          </div>
        </div>
        <div className="identity">
          {personas.length > 0 && me && (
            <label className="persona">
              Demo identity{" "}
              <select
                data-demo="persona-selector"
                value={me.persona}
                onChange={(e) => onPersona(e.target.value)}
              >
                {personas.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} · {p.subject}
                  </option>
                ))}
              </select>
            </label>
          )}
          {me && (
            <span data-demo="verified-identity" className="who" title={me.principals.join(", ")}>
              {me.subject} @ {me.tenant}
            </span>
          )}
        </div>
      </header>

      <nav className="tabs">
        {journey.apps.map((a) => (
          <button
            key={a.id}
            data-demo={`app-tab-${a.id}`}
            className={a.id === app.id ? "tab active" : "tab"}
            onClick={() => setActiveApp(a.id)}
          >
            {a.label}
          </button>
        ))}
      </nav>

      <section className="stage">
        <iframe
          key={`${app.id}-${reloadKey}`}
          data-demo="embedded-app"
          title={app.label}
          src={app.ui_base}
          className="frame"
        />
      </section>
    </main>
  );
}
