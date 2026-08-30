import type { Metadata } from "next";

import { ProvenanceBanner } from "../components/ProvenanceBanner";
import "./globals.css";

// This shell serves whichever journey NEXT_PUBLIC_JOURNEY names, so the browser tab has to
// follow it. A fixed title labelled every persona workbench as the relationship manager's,
// which is wrong on screen and wrong in a screenshot taken for evidence.
const journey = process.env.NEXT_PUBLIC_JOURNEY || "rm";

export const metadata: Metadata = {
  title: `${journey.toUpperCase()} Journey`,
  description: "Persona journey cockpit (Hrz9 journey portal, React shell).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  // Mounted in the LAYOUT, not in a page: "at the top of every page" is a property of the
  // shell, and a page that forgot it would be the one page a screenshot came from.
  return (
    <html lang="en">
      <body>
        <ProvenanceBanner />
        {children}
      </body>
    </html>
  );
}
