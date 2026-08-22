import { CommonModule } from "@angular/common";
import { ChangeDetectorRef, Component, OnInit, inject } from "@angular/core";
import { DomSanitizer, SafeResourceUrl } from "@angular/platform-browser";

import { AppModel, JourneyModel, Persona, PortalService, WhoAmI } from "./portal.service";

// The Ops Journey shell. Deliberately THIN, exactly like the React RM shell: a persona switcher and
// a tabbed set of same-origin iframes over the portal BFF. Same BFF, different host framework - the
// whole point of the two-shell demo. Identity, proxying and journey config live in the BFF.
const JOURNEY_KEY = "ops";

@Component({
  selector: "app-root",
  standalone: true,
  imports: [CommonModule],
  templateUrl: "./app.component.html",
  styleUrls: ["./app.component.css"],
})
export class AppComponent implements OnInit {
  private portal = inject(PortalService);
  private sanitizer = inject(DomSanitizer);
  private changeDetector = inject(ChangeDetectorRef);

  journey: JourneyModel | null = null;
  personas: Persona[] = [];
  me: WhoAmI | null = null;
  active: AppModel | null = null;
  frameUrl: SafeResourceUrl | null = null;
  error = "";

  ngOnInit(): void {
    this.portal.journeys().subscribe({
      next: (journeys) => {
        this.journey = journeys.find((j) => j.key === JOURNEY_KEY) ?? journeys[0] ?? null;
        this.select(this.journey?.apps[0] ?? null);
        this.changeDetector.detectChanges();
      },
      error: (e: unknown) => (this.error = this.message(e)),
    });
    this.portal.personas().subscribe({
      next: (personas) => {
        this.personas = personas;
        this.changeDetector.detectChanges();
      },
      error: () => undefined,
    });
    this.portal.whoami().subscribe({
      next: (who) => {
        this.me = who;
        this.changeDetector.detectChanges();
      },
      error: () => undefined,
    });
  }

  select(app: AppModel | null): void {
    this.active = app;
    // The app UI is same-origin (proxied), so a relative URL is safe; Angular still requires it be
    // marked trusted to bind to an iframe src.
    this.frameUrl = app ? this.sanitizer.bypassSecurityTrustResourceUrl(app.ui_base) : null;
  }

  onPersona(id: string): void {
    this.portal.setPersona(id).subscribe({
      next: (who) => {
        this.me = who;
        // Force the active iframe to reload so the embedded apps pick up the new identity (their
        // next API call carries the updated portal cookie).
        const current = this.active;
        this.select(null);
        this.changeDetector.detectChanges();
        setTimeout(() => {
          this.select(current);
          this.changeDetector.detectChanges();
        }, 0);
      },
      error: (e: unknown) => (this.error = this.message(e)),
    });
  }

  private message(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }
}
