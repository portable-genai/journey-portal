/** @type {import('next').NextConfig} */
// The RM shell is the same-origin front for the portal in dev: it serves its own chrome at "/" and
// reverse-proxies the portal BFF for everything under /apps/* (embedded app UIs + their APIs) and
// /v1/* (the portal's journey/persona endpoints). Because the browser only ever sees THIS origin,
// every embedded iframe (src="/apps/<id>/") is first-party: true mode-1 same-origin embedding, no
// CORS, no third-party cookies. In production a reverse proxy / CDN plays the same role.
const bff = process.env.PORTAL_BFF_ORIGIN || "http://127.0.0.1:8110";

// Three states, never two. `||` treated "never configured" and "deliberately set to nothing" as
// the same answer and silently handed both the shipped default, which hides an operator error:
// a config template that renders this blank looks like it took effect. Unset keeps 'self'; a
// value naming no origin refuses at config load, which is boot for Next.js; anything else is
// normalised and used. A total lockdown stays expressible as 'none'. The production shells apply
// the same rule in static_server.py, and the BFF applies it from the reviewed tenant registry.
// Values that must never be accepted as a framing ancestor. Four spellings, not one: `'*'` is
// the quoted form CSP also honours, `*.*` is the subdomain wildcard, and `null` is the origin a
// SANDBOXED iframe presents, so allowing it hands the frame to any page that can sandbox one.
const FRAME_ANCESTOR_WILDCARDS = new Set(["*", "'*'", "null", "*.*"]);

function resolveFrameAncestors(raw) {
  if (raw === undefined || raw === null) return "'self'";
  const parts = String(raw).trim().split(/\s+/).filter(Boolean);
  const value = parts.join(" ");
  if (!value) {
    throw new Error(
      "NEXT_PUBLIC_FRAME_ANCESTORS is set to an empty value: it names no parent origin, and an " +
        "empty CSP frame-ancestors directive is a parse error that browsers discard, taking the " +
        "clickjacking restriction with it. Leave it unset to keep the 'self' default, or set it " +
        "to 'none' to refuse all framing.",
    );
  }
  for (const part of parts) {
    if (FRAME_ANCESTOR_WILDCARDS.has(part)) {
      throw new Error(
        `NEXT_PUBLIC_FRAME_ANCESTORS contains ${JSON.stringify(part)}: the origin policy must ` +
          "never contain a wildcard. That is the clickjacking control switched off, since any " +
          "site could then frame this shell. Name the exact parent origins instead, leave it " +
          "unset to keep the 'self' default, or set it to 'none' to refuse all framing.",
      );
    }
  }
  return value;
}

const frameAncestors = resolveFrameAncestors(process.env.NEXT_PUBLIC_FRAME_ANCESTORS);
// The shell's rewrite proxy is a hop in front of the BFF, so it needs the longest
// timeout in the chain, not the shortest. Under the live profiles a real CDD dossier
// runs for minutes (documents read locally, grounded research, several model calls);
// with Next's default the shell dropped that request mid-build ("socket hang up") and
// the browser showed nothing while the backend went on to answer 200. Default 15 min,
// comfortably above the BFF's own PORTAL_UPSTREAM_TIMEOUT (600s under --live).
const proxyTimeout = Number(process.env.PORTAL_SHELL_PROXY_TIMEOUT_MS || 900_000);
const staticExport = process.env.PORTAL_STATIC_EXPORT === "1";

const nextConfig = {
  reactStrictMode: true,
  output: staticExport ? "export" : undefined,
  experimental: { proxyTimeout },
  ...(staticExport
    ? {}
    : {
        async rewrites() {
          return [
            { source: "/apps/:path*", destination: `${bff}/apps/:path*` },
            { source: "/agent/:path*", destination: `${bff}/agent/:path*` },
            { source: "/v1/:path*", destination: `${bff}/v1/:path*` },
          ];
        },
        async headers() {
          return [
            {
              source: "/:path*",
              headers: [
                { key: "Content-Security-Policy", value: `frame-ancestors ${frameAncestors}` },
                ...(frameAncestors === "'self'"
                  ? [{ key: "X-Frame-Options", value: "SAMEORIGIN" }]
                  : []),
                { key: "X-Content-Type-Options", value: "nosniff" },
                { key: "Referrer-Policy", value: "no-referrer" },
              ],
            },
          ];
        },
      }),
};

export default nextConfig;
