#!/usr/bin/env node
// Fail closed on every high or critical dependency finding.
import { spawnSync } from "node:child_process";

const result = spawnSync("npm", ["audit", "--json"], { encoding: "utf8" });
if (result.error || result.signal || result.status === null) {
  console.error("npm audit did not execute successfully:", result.error?.message || result.signal);
  process.exit(1);
}
if (![0, 1].includes(result.status)) {
  console.error(`npm audit exited unexpectedly with status ${result.status}`);
  process.exit(1);
}
let report;
try {
  report = JSON.parse(result.stdout);
} catch {
  console.error("npm audit returned malformed JSON");
  process.exit(1);
}
if (
  !report ||
  typeof report !== "object" ||
  Array.isArray(report) ||
  !report.vulnerabilities ||
  typeof report.vulnerabilities !== "object" ||
  Array.isArray(report.vulnerabilities) ||
  !report.metadata ||
  typeof report.metadata !== "object" ||
  !report.metadata.vulnerabilities ||
  typeof report.metadata.vulnerabilities !== "object"
) {
  console.error("npm audit returned an incomplete schema");
  process.exit(1);
}
if (report.error) {
  console.error(report.error.summary || "npm audit failed");
  process.exit(1);
}
const severities = ["info", "low", "moderate", "high", "critical"];
const metadataCounts = report.metadata.vulnerabilities;
if (
  ![...severities, "total"].every(
    (severity) =>
      Number.isInteger(metadataCounts[severity]) && metadataCounts[severity] >= 0,
  ) ||
  metadataCounts.total !==
    severities.reduce((total, severity) => total + metadataCounts[severity], 0)
) {
  console.error("npm audit returned invalid vulnerability counts");
  process.exit(1);
}
const entryCounts = Object.fromEntries(severities.map((severity) => [severity, 0]));
const blocked = [];
for (const [name, finding] of Object.entries(report.vulnerabilities)) {
  if (
    !finding ||
    typeof finding !== "object" ||
    !severities.includes(finding.severity)
  ) {
    console.error(`npm audit returned an invalid finding for ${name}`);
    process.exit(1);
  }
  entryCounts[finding.severity] += 1;
  if (["high", "critical"].includes(finding.severity)) {
    blocked.push(name);
  }
}
if (
  metadataCounts.total !== Object.keys(report.vulnerabilities).length ||
  severities.some((severity) => metadataCounts[severity] !== entryCounts[severity])
) {
  console.error("npm audit vulnerability entries disagree with metadata counts");
  process.exit(1);
}
if (metadataCounts.high > 0 || metadataCounts.critical > 0 || blocked.length) {
  console.error(
    "High/critical npm findings:",
    blocked.join(", ") || "reported in metadata",
  );
  process.exit(1);
}
console.log("PASS npm audit: no high or critical findings");
