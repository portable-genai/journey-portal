import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RM Journey",
  description: "Relationship-manager cockpit (Hrz9 journey portal, React shell).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
