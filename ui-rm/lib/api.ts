// Same-origin calls to the portal BFF. In dev these resolve through the Next.js rewrites in
// next.config.mjs (/v1/* -> BFF); in production the reverse proxy in front of the shell does the
// same. No base URL and no CORS: the shell and the BFF are one origin to the browser.

import type { Health, JourneyModel, Persona, WhoAmI } from "./types";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function listJourneys(): Promise<JourneyModel[]> {
  const res = await fetch("/v1/journeys");
  return (await asJson<{ journeys: JourneyModel[] }>(res)).journeys;
}

export async function listPersonas(): Promise<Persona[]> {
  return asJson<Persona[]>(await fetch("/v1/personas"));
}

export async function whoami(): Promise<WhoAmI> {
  return asJson<WhoAmI>(await fetch("/v1/whoami"));
}

export async function setPersona(id: string): Promise<WhoAmI> {
  const res = await fetch("/v1/session/persona", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  });
  return asJson<WhoAmI>(res);
}

export async function health(): Promise<Health> {
  return asJson<Health>(await fetch("/v1/healthz"));
}
