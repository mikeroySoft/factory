// C0 consumer-transport regression (Astra findings). Not a copied parser:
// this drives the real extension.ts collect() path (fm_observe tool
// execute) against the real Python evidence CLI with a controlled `gh`
// fixture. A healthy read must be accepted, a hung read cancelled through
// a real AbortSignal must reject without leaving live descendants, and the
// consumer boundary must accept the fixed producer's byte-bounded partial —
// the extension hard-codes its evidence.py entry and response cap, so no
// fake stream can substitute.
// Run from the repository root: node console/c0-prototype/check-transport.ts
import { execSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const REPO = "example/evidence";
const PREFIX = `repos/${REPO}/`;
const MARKER = "c1-transport-check-descendant-" + process.pid;
let failed = false;

function check(ok: unknown, message: string): void {
  if (ok) console.log(`ok - ${message}`);
  else { console.error(`FAIL: ${message}`); failed = true; }
}
function delay(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}
function settledWithin(ms: number, promise: Promise<unknown>, label: string): Promise<unknown> {
  const { promise: result, resolve, reject } = Promise.withResolvers<unknown>();
  const timer = setTimeout(() => reject(new Error(label)), ms);
  promise.then((value) => { clearTimeout(timer); resolve(value); }, (error) => { clearTimeout(timer); reject(error); });
  return result;
}
const ROOT = mkdtempSync(join(tmpdir(), "c1-transport-check-"));
const REPO_DIR = join(ROOT, "repo");
const TOOLS = join(ROOT, "bin");
const RESPONSES = join(ROOT, "responses.json");
const CALLS = join(ROOT, "calls.jsonl");

// Controlled `gh` stand-in (Node-written: avoids inline-quoting hazards).
// `fork` spawns a marked descendant in the gh child's own session (the
// producer isolates each gh call in a new session; a plain `sleep` cannot
// take a marker argument, so the descendant is `python3 -c` sleeping with
// the marker as an inert argv element). Surviving-marker checks prove the
// cancellation reached the gh descent of the Python bridge.
const gh = `#!/usr/bin/env python3
import json, os, sys, time
from urllib.parse import parse_qsl, urlencode, urlsplit
args = sys.argv[1:]
endpoint = next((a for a in args if a.startswith("repos/")), "")
parts = urlsplit(endpoint)
query = dict(parse_qsl(parts.query))
with open(os.environ["EVIDENCE_CALLS"], "a") as stream:
    stream.write(json.dumps({"path": parts.path}) + "\\n")
with open(os.environ["EVIDENCE_RESPONSES"]) as stream:
    responses = json.load(stream)
key = parts.path + ("?" + urlencode(sorted(query.items())) if query else "")
response = responses.get(key, responses.get(parts.path))
if response is None:
    raise SystemExit(96)
if response.get("fork"):
    if os.fork() == 0:
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 0)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.execvpe("python3", ["python3", "-c", "import time; time.sleep(120)", os.environ["EVIDENCE_FORK_MARKER"]], os.environ)
if response.get("sleep"):
    time.sleep(response["sleep"])
if "raw" in response:
    sys.stdout.write(response["raw"])
else:
    json.dump(response.get("json"), sys.stdout)
raise SystemExit(response.get("exit", 0))
`;
function finish(): never {
  for (const pid of markerPids()) {
    for (const sig of ["SIGTERM", "SIGKILL"]) {
      try { process.kill(pid, sig); } catch { /* gone */ }
    }
  }
  try { rmSync(ROOT, { recursive: true, force: true }); } catch { /* best-effort */ }
  process.exit(failed ? 1 : 0);
}
function commandPath(name: string): string {
  const target = execSync(`command -v ${name}`, { encoding: "utf8" }).trim();
  if (target.length === 0 || !existsSync(target)) finish();
  return target;
}
const PYTHON = commandPath("python3");

mkdirSync(REPO_DIR, { recursive: true });
mkdirSync(TOOLS, { recursive: true });
for (const name of ["git", "python3"]) {
  execSync(`ln -sf ${JSON.stringify(commandPath(name))} ${JSON.stringify(join(TOOLS, name))}`);
}
writeFileSync(join(TOOLS, "gh"), gh);
execSync(`chmod 755 ${JSON.stringify(join(TOOLS, "gh"))}`);
// The Python bridge probes the host timer via systemctl; stub it fixture-only.
writeFileSync(join(TOOLS, "systemctl"), `#!/usr/bin/env python3\nprint("ActiveState=inactive\\nNextElapseUSecRealtime=0")\n`);
execSync(`chmod 755 ${JSON.stringify(join(TOOLS, "systemctl"))}`);
mkdirSync(join(REPO_DIR, ".factory", "locks"), { recursive: true });
execSync(`git init -q -b main ${JSON.stringify(REPO_DIR)}`);
writeFileSync(join(REPO_DIR, ".factory.toml"), `[repo]\nslug = "${REPO}"\n[gate]\nlock = "${join(REPO_DIR, "gpu.lock")}"\n`);
writeFileSync(join(REPO_DIR, ".factory", "events.jsonl"), "");
writeFileSync(join(REPO_DIR, ".factory", "locks", "merge.lock"), "");
writeFileSync(join(REPO_DIR, "gpu.lock"), "");

process.env.FM_C0_ROOT = REPO_DIR;
process.env.FM_C0_REPOSITORY = REPO;
process.env.FM_C0_PROVIDER = "fixture";
process.env.FM_C0_MODEL = "fixture";
process.env.FM_C0_ENDPOINT = "fixture://local";
process.env.FM_C0_PYTHON = PYTHON;
process.env.PYTHONPATH = join(HERE, "..", "..");
process.env.PYTHONPYCACHEPREFIX = join(ROOT, "pycache");
process.env.GH_TOKEN = "";
process.env.GITHUB_TOKEN = "";
process.env.HOME = join(ROOT, "home");
process.env.XDG_CONFIG_HOME = join(ROOT, "host");
process.env.GH_CONFIG_DIR = join(ROOT, "gh");
process.env.EVIDENCE_RESPONSES = RESPONSES;
process.env.EVIDENCE_CALLS = CALLS;
process.env.EVIDENCE_FORK_MARKER = MARKER;
// Fixture-only PATH: the checker must never resolve the host `gh` binary.
process.env.PATH = TOOLS;

const tools: Record<string, (id: string, params: unknown, signal: AbortSignal, update: () => void, ctx: unknown) => Promise<{ content: Array<{ type: string; text: string }> }>> = {};
const piStub = {
  registerTool: (definition: { name: string; execute: (id: string, params: unknown, signal: AbortSignal, update: () => void, ctx: unknown) => Promise<unknown> }) => {
    tools[definition.name] = definition.execute as (typeof tools)["fm_observe"];
  },
  on: () => undefined,
  registerCommand: () => undefined,
  sendMessage: () => undefined,
  setActiveTools: () => undefined,
  getActiveTools: () => [],
} as unknown as ExtensionAPI;
// Dynamic import is deliberate: extension.ts reads FM_C0_* at module top,
// so a static top-of-file import would capture empty values.
const { default: register } = await import("./extension.ts");
register(piStub);
const observe = tools["fm_observe"];

const ctxStub = {
  ui: { setStatus: () => undefined, notify: () => undefined, select: async () => "Browse only — send nothing" },
  isIdle: () => false,
};
const request = { schema_version: 1, repository: REPO };
interface ObservedEvidence {
  ok: boolean;
  attention_count: number | null;
  coverage: { status: string; notices: unknown };
  cases: { number: number }[];
  sources: unknown[];
}
// Structural parse of a settled tool result. JSON.parse is the untrusted
// boundary; the cast is its declared contract, and every consumer field is
// checked against the producer envelope afterwards.
function parsedEvidence(value: unknown): ObservedEvidence | undefined {
  if (!value || typeof value !== "object" || !("content" in value)) return undefined;
  const content = (value as { content?: unknown }).content;
  if (!Array.isArray(content)) return undefined;
  const texts: string[] = [];
  for (const row of content) {
    if (!row || typeof row !== "object" || !("type" in row) || !("text" in row)) return undefined;
    const item = row as { type: unknown; text: unknown };
    if (item.type !== "text" || typeof item.text !== "string") return undefined;
    texts.push(item.text);
  }
  try {
    return JSON.parse(texts.join("")) as ObservedEvidence;
  } catch {
    return undefined;
  }
}
function writeResponses(values: Record<string, unknown>): void {
  writeFileSync(CALLS, "");
  writeFileSync(RESPONSES, JSON.stringify(values));
}
function calls(): string[] {
  if (!existsSync(CALLS)) return [];
  return readFileSync(CALLS, "utf8").split("\n").filter(Boolean).map((line) => (JSON.parse(line) as { path: string }).path);
}
function markerPids(): number[] {
  const found: number[] = [];
  for (const entry of readdirSync("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    let cmdline = "";
    try { cmdline = readFileSync(`/proc/${entry}/cmdline`, "utf8"); } catch { continue; /* exited */ }
    if (cmdline.includes(MARKER)) found.push(Number(entry));
  }
  return found;
}
async function waitUntil(predicate: () => boolean, timeoutMs: number, label: string): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await delay(50);
  }
  check(predicate(), label);
  return predicate();
}
// All phases run under one finally: any throw, any phase, kills marker
// descendants and removes the temp tree before exit.
try {
  // Phase A: a healthy read flows unmodified through the real collect path.
  writeResponses({
    [PREFIX + "issues"]: { json: [8, 7, 6, 5, 4, 3, 2, 1].map((number) => ({
      number, title: `Case ${String(number).padStart(3, "0")} transport probe`, state: "open",
      html_url: `https://github.com/${REPO}/issues/${number}`,
      labels: [{ name: "ready-for-human" }, { name: `multilingual-εä中한${number}` }],
      created_at: "2026-01-02T03:04:05Z", updated_at: "2026-01-02T03:04:05Z",
    })) },
    [PREFIX + "pulls"]: { json: [] },
  });
  const healthy = await settledWithin(30_000, observe("op-healthy", request, new AbortController().signal, () => undefined, ctxStub), "healthy read exceeded 30s").then(
    (value) => ({ ok: true as const, value, error: undefined }),
    (error: Error) => ({ ok: false as const, value: undefined, error }),
  );
  check(healthy.ok, `healthy read succeeds through the real collect path${healthy.ok ? "" : ": " + String(healthy.error)}`);
  const healthyData = healthy.ok ? parsedEvidence(healthy.value) : undefined;
  check(healthy.ok && healthyData !== undefined, "healthy read reached the consumer as a parseable evidence envelope");
  if (healthyData) {
    check(healthyData.ok === true, "healthy read accepted as ok:true through the real collect path");
    check(healthyData.coverage.status === "bounded", "clean state reports bounded coverage");
    check(Array.isArray(healthyData.cases) && healthyData.cases.length === 8, "all eight fixture cases reach the consumer");
    check(healthyData.attention_count === 8, "grounded attention count reaches the consumer");
  }
  check(calls().filter((path) => path === PREFIX + "issues").length === 1, "exactly one issues GET served the healthy read");

  // Phase B: hung read + real AbortSignal cancel (the /fm cancel protocol).
  // The marker descendant stays in the gh session; the consumer must cancel
  // the bridge such that the producer reaps the whole gh descent.
  writeResponses({
    [PREFIX + "issues"]: { json: [], sleep: 60, fork: true },
    [PREFIX + "pulls"]: { json: [] },
  });
  const controller = new AbortController();
  const hungPromise = observe("op-hung", request, controller.signal, () => undefined, ctxStub);
  const served = await waitUntil(() => calls().includes(PREFIX + "issues"), 60_000, "hung fixture was actually reached (no issues GET in call log)");
  if (!served) finish();
  // The abort must land while the gh descent is live; otherwise this phase
  // would pass without ever proving cancel reaches the forked descendant.
  const descended = await waitUntil(() => markerPids().length > 0, 60_000, "marker descendant never became visible before cancel");
  if (!descended) finish();
  controller.abort();
  const hung = await settledWithin(60_000, hungPromise, "cancel did not settle the collect Promise within 60s").then(
    (value) => ({ ok: true as const, value, error: undefined }),
    (error: Error) => ({ ok: false as const, value: undefined, error }),
  );
  check(!hung.ok || hung.value === undefined, "cancelled read must never be accepted with evidence");
  check(!hung.ok && hung.error instanceof Error && /cancel/i.test(hung.error.message), `cancel surfaced as a rejection, got: ${String(hung.error)}`);
  // Guard against the still-pending original promise keeping the event loop
  // alive after the wrapper settled; the finally's process.exit covers it.
  hungPromise.catch(() => {});
  await waitUntil(() => markerPids().length === 0, 15_000, `live descendants survived cancel: ${JSON.stringify(markerPids())}`);

  // Phase C: consumer boundary on the real payload. Each issue carries
  // ready-for-human plus 19 unique labels of 48 CJK ideographs plus a
  // two-digit index — 50 chars each, the API cap, BMP only. The wire
  // response is within the 1 MiB per-read bound, but the ASCII-escaped page
  // alone exceeds the 500000-byte envelope. The baseline extension stopped
  // at >500000 bytes and rejected; the fixed producer must instead emit a
  // bounded partial the consumer accepts.
  const boundaryIssues = Array.from({ length: 100 }, (_, index) => {
    const number = index + 1;
    const labels = [{ name: "ready-for-human" }];
    for (let position = 1; position < 20; position += 1) {
      labels.push({ name: "中".repeat(48) + String(position).padStart(2, "0") });
    }
    return {
      number, title: `Case ${String(number).padStart(3, "0")} transport-boundary probe`, state: "open",
      html_url: `https://github.com/${REPO}/issues/${number}`,
      labels, created_at: "2026-01-02T03:04:05Z", updated_at: "2026-01-02T03:04:05Z",
    };
  });
  const boundaryValues: Record<string, unknown> = {
    [PREFIX + "issues"]: { json: boundaryIssues },
    [PREFIX + "pulls"]: { json: [] },
  };
  const wireBytes = Buffer.byteLength(JSON.stringify(boundaryValues));
  // All label characters are BMP: every non-ASCII character escapes to six
  // ASCII bytes (\uXXXX), so this emulation is exact for the producer side.
  const escapedBytes = Buffer.byteLength(JSON.stringify(boundaryValues).replace(/[^ -~]/g, "zzzzzz"));
  check(wireBytes < 1_048_576, `wire input within per-read bound (${wireBytes} < 1048576)`);
  check(escapedBytes > 500_000, `ASCII-escaped page alone exceeds the response cap (${escapedBytes} > 500000)`);
  writeResponses(boundaryValues);
  const boundary = await settledWithin(40_000, observe("op-boundary", request, new AbortController().signal, () => undefined, ctxStub), "boundary read did not settle within 40s").then(
    (value) => ({ ok: true as const, value, error: undefined }),
    (error: Error) => ({ ok: false as const, value: undefined, error }),
  );
  check(boundary.ok,
    `consumer must accept the fixed producer's bounded partial, got rejection: ${String(boundary.error)}`);
  const boundaryData = boundary.ok ? parsedEvidence(boundary.value) : undefined;
  check(boundary.ok && boundaryData !== undefined, "boundary read reached the consumer as a parseable evidence envelope");
  if (boundaryData) {
    check(boundaryData.ok === false, "fixed producer emits an honest partial (ok:false) that the consumer accepts");
    check(boundaryData.attention_count === null, "reduced case coverage keeps attention_count null at the consumer");
    check(Array.isArray(boundaryData.cases) && boundaryData.cases.length < 100, "consumer received an honest partial: clipped case list (possibly empty when no case fits the byte budget)");
    check(Array.isArray(boundaryData.sources) && boundaryData.sources.length > 0, "usable partial sources survived the boundary");
    check(Array.isArray(boundaryData.coverage.notices) && (boundaryData.coverage.notices as unknown[]).length > 0, "omission is honestly marked in coverage notices");
  }
  check(markerPids().length === 0, "boundary check leaves no live descendants");
  check(!failed, "consumer transport contract holds");
} catch (error) {
  console.error(error);
  failed = true;
} finally {
  finish();
}