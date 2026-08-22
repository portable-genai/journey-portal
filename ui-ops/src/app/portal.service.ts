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

  setPersona(id: string): Observable<WhoAmI> {
    return this.http.post<WhoAmI>("/v1/session/persona", { id });
  }
}
