import { HttpClient } from "@angular/common/http";
import { Injectable, inject } from "@angular/core";
import { Observable } from "rxjs";
import { map } from "rxjs/operators";

// Mirrors the portal BFF's /v1 response models (journey_portal.api.schemas). All calls are
// same-origin: the Angular dev-server proxy (proxy.conf.json) forwards /v1 and /apps to the BFF,
// so there is no base URL and no CORS.

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

export interface Health {
  status: string;
  profile: string;
  // Where the PORTAL runs and which model answers it. The portal generates nothing, so
  // its model is "no-model"; each embedded app states its own inside its own frame, and
  // the two can legitimately differ.
  runtime: string;
  generator_model: string;
  region: string;
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

@Injectable({ providedIn: "root" })
export class PortalService {
  private http = inject(HttpClient);

  journeys(): Observable<JourneyModel[]> {
    return this.http
      .get<{ journeys: JourneyModel[] }>("/v1/journeys")
      .pipe(map((r) => r.journeys));
  }

  personas(): Observable<Persona[]> {
    return this.http.get<Persona[]>("/v1/personas");
  }

  whoami(): Observable<WhoAmI> {
    return this.http.get<WhoAmI>("/v1/whoami");
  }

  // /v1/healthz, not /healthz: the serverless frontend answers the latter without ever
  // reaching the container, so a shell reading it would be told the portal is healthy and
  // running locally whether or not the application is up. See the BFF's own comment.
  health(): Observable<Health> {
    return this.http.get<Health>("/v1/healthz");
  }

  setPersona(id: string): Observable<WhoAmI> {
    return this.http.post<WhoAmI>("/v1/session/persona", { id });
  }
}
