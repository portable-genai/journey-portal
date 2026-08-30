// Mirrors the portal BFF's /v1 response models (journey_portal.api.schemas).

export interface AppModel {
  id: string;
  label: string;
  mount_path: string;
  ui_base: string;
  api_base: string;
}

export interface JourneyModel {
  key: string;
  label: string;
  blurb: string;
  apps: AppModel[];
}

export interface Persona {
  id: string;
  subject: string;
  tenant: string;
  principals: string;
}

export interface WhoAmI {
  subject: string;
  tenant: string;
  principals: string[];
  source: string;
  persona: string;
}

/** The portal BFF's health, for the provenance banner. */
export type Health = {
  status: string;
  profile: string;
  // Where the PORTAL runs and which model answers it. The portal generates nothing, so
  // its model is "no-model"; each embedded app states its own inside its own frame, and
  // the two can legitimately differ.
  runtime: string;
  generator_model: string;
  region: string;
};
