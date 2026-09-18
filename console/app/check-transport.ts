// C1 consumer-transport regression. Not a copied parser: this drives the
// real extension.ts collect() path against console/app/evidence.py and the
// shared Python evidence CLI with a controlled `gh` fixture. It covers a
// healthy read, real prompt/context hooks plus a cited roadmap stand-in,
// cancellation of a hung read without live descendants, and acceptance of
// the producer's byte-bounded partial.
// Run from the repository root:
//   node console/app/check-transport.ts
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

const hooks: Record<string, (...args: unknown[]) => unknown> = {};
const tools: Record<string, (id: string, params: unknown, signal: AbortSignal, update: () => void, ctx: unknown) => Promise<{ content: Array<{ type: string; text: string }> }>> = {};
const piStub = {
  registerTool: (definition: { name: string; execute: (id: string, params: unknown, signal: AbortSignal, update: () => void, ctx: unknown) => Promise<unknown> }) => {
    tools[definition.name] = definition.execute as (typeof tools)["fm_observe"];
  },
  on: (name: string, handler: (...args: unknown[]) => unknown) => { hooks[name] = handler; },
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
const investigate = tools["fm_investigate"];

const ctxStub = {
  signal: new AbortController().signal,
  model: { provider: "fixture", id: "fixture" },
  ui: { setStatus: () => undefined, notify: () => undefined, select: async () => "Browse only — send nothing" },
  isIdle: () => false,
};
const request = { schema_version: 1, repository: REPO };
interface ObservedEvidence {
  ok: boolean;
  observation_id: string;
  observed_at: string;
  attention_count: number | null;
  coverage: { status: string; notices: unknown };
  cases: { number: number }[];
  sources: { id: string; label: string; text: string; url?: string; path?: string }[];
  investigation?: {
    kind: string;
    status?: string;
    reason?: string | null;
    next_offset?: number | null;
    handoff_source_id?: string | null;
    plans?: Array<{
      number: number;
      sections: Record<string, string>;
      blockers: Array<{ number: number; title?: string; state?: string; url?: string }>;
      children?: Array<{ number: number; source?: string; blockers: Array<{ number: number }> }>;
    }>;
    attention?: Array<{ kind: string; question: string; initiative: number }>;
  };
}
// Structural parse of a settled tool result. JSON.parse is the untrusted
// boundary; the cast is its declared contract, and every consumer field is
// checked against the producer envelope afterwards.
function parsedEvidence(value: unknown): ObservedEvidence | undefined {
  if (!value || typeof value !== "object" || !("content" in value)) return undefined;
  const content = value.content;
  if (!Array.isArray(content)) return undefined;
  const texts: string[] = [];
  for (const row of content) {
    if (!row || typeof row !== "object" || !("type" in row) || !("text" in row)) return undefined;
    const item = row;
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

  // An exact-head archive read is local even when no retained result exists.
  const beforeResultCalls = calls().length;
  const absentResult = parsedEvidence(await settledWithin(
    30_000,
    investigate("op-retained-result", { ...request, kind: "result", number: 7, head: "a".repeat(40), offset: 0 },
      new AbortController().signal, () => undefined, ctxStub),
    "retained-result read exceeded 30s",
  ));
  check(absentResult?.ok === false && absentResult.investigation?.kind === "result"
    && absentResult.investigation.status !== "complete" && Boolean(absentResult.investigation.reason)
    && absentResult.investigation.next_offset === null && absentResult.investigation.handoff_source_id === null,
  "missing retained content stays explicit through the actual console tool");
  check(calls().length === beforeResultCalls, "retained-result lookup makes no GitHub calls");

  // Phase A2: provider-free C1 plumbing. Disclosure remains closed, while the
  // real startup/context hooks and fm_investigate -> Python adapter are
  // exercised with cited initiative and dependency evidence.
  const blockedInput = await hooks["input"](
    { text: "What decision blocks initiative 52?", images: [] },
    ctxStub,
  );
  check(Boolean(blockedInput && typeof blockedInput === "object" && "action" in blockedInput
    && blockedInput.action === "handled"),
  "unapproved conversational input remains blocked before any provider request");
  await hooks["before_agent_start"](
    { systemPromptOptions: { skills: [], contextFiles: [] } },
    ctxStub,
  );

  const initiativeBody = [
    "**Status**", "underway",
    "**Outcome**", "A shared API rolls out without skipping the storage prerequisite.",
    "**Owner**", "roadmap-owner",
    "**Areas**", "api, storage",
    "**Boundaries**", "No publication or dispatch from the read-only console.",
    "**Plan**", "#54 prepares the storage schema before #53 consumes it.",
    "**Open decisions**", "Which migration window should owner and storage team use?",
    "**Success evidence**", "Owner-confirmed success evidence is not yet supplied.",
    "**Implementation links**", "#53",
  ].join("\n");
  const issue = (number: number, title: string, body: string, labels: string[] = []) => ({
    number, title, body, state: "open",
    html_url: `https://github.com/${REPO}/issues/${number}`,
    labels: labels.map(name => ({ name })), assignees: [],
    created_at: "2026-01-02T03:04:05Z", updated_at: "2026-01-02T03:04:05Z",
  });
  const initiativeIssue = issue(52, "Shared API roadmap <untrusted>", initiativeBody, ["initiative"]);
  const childIssue = issue(53, "Ship API consumer", "Initiative: #52\nBlocked by: #54", ["ready-for-human"]);
  const blockerIssue = issue(54, "Prepare storage schema", "Required before #53.", ["ready-for-human"]);
  writeResponses({
    [PREFIX + "issues"]: { json: [initiativeIssue] },
    [PREFIX + "pulls"]: { json: [] },
    [PREFIX + "issues/52"]: { json: initiativeIssue },
    [PREFIX + "issues/52/comments"]: { json: [] },
    [PREFIX + "issues/52/timeline"]: { json: [] },
    [PREFIX + "issues/53"]: { json: childIssue },
    [PREFIX + "issues/53/dependencies/blocked_by"]: { json: [] },
    [PREFIX + "issues/53/comments"]: { json: [] },
    [PREFIX + "issues/53/timeline"]: { json: [] },
    [PREFIX + "issues/54"]: { json: blockerIssue },
    [PREFIX + "issues/54/comments"]: { json: [] },
    [PREFIX + "issues/54/timeline"]: { json: [] },
  });
  const investigated = await settledWithin(
    30_000,
    investigate("op-initiative", { ...request, kind: "initiative", number: 52 },
      new AbortController().signal, () => undefined, ctxStub),
    "initiative investigation exceeded 30s",
  ).then(
    (value) => ({ ok: true as const, value, error: undefined }),
    (error: Error) => ({ ok: false as const, value: undefined, error }),
  );
  check(investigated.ok, `real initiative tool reaches the Python roadmap producer${investigated.ok ? "" : ": " + String(investigated.error)}`);
  const initiativeData = investigated.ok ? parsedEvidence(investigated.value) : undefined;
  const roadmap = initiativeData?.investigation;
  const plan = roadmap?.plans?.[0];
  const question = roadmap?.attention?.find(row => row.kind === "open_decisions")?.question;
  const initiativeCitation = initiativeData?.sources.find(row => row.url?.endsWith("/issues/52"));
  const dependent = plan?.children?.find(row => row.number === 53);
  const blocker = dependent?.blockers.find(row => row.number === 54);
  const blockerCitation = initiativeData?.sources.find(row => row.id === dependent?.source);
  check(initiativeData?.ok === true && roadmap?.kind === "initiative" && plan?.number === 52,
    "targeted roadmap reaches the consumer as a complete initiative investigation");
  check(typeof question === "string" && question.includes("migration window"),
    "declared migration-window decision remains available beside binding questions");
  check(Boolean(blocker),
    "known blocker relationship remains attached to the dependent ticket");
  check(Boolean(initiativeCitation && blockerCitation),
    "initiative plan and dependency each retain a supplied source citation");

  let toolContent: unknown = [];
  if (investigated.ok && investigated.value && typeof investigated.value === "object"
      && "content" in investigated.value) {
    toolContent = investigated.value.content;
  }
  const toolMessage = { role: "tool", content: toolContent, timestamp: Date.now() };
  const contextual = await hooks["context"]({ messages: [toolMessage] });
  const messages = contextual && typeof contextual === "object" && "messages" in contextual
    && Array.isArray(contextual.messages) ? contextual.messages : [];
  const lastMessage = messages.at(-1);
  const awarenessContent = lastMessage && typeof lastMessage === "object" && "content" in lastMessage
    && Array.isArray(lastMessage.content) ? lastMessage.content[0] : undefined;
  const awareness = awarenessContent && typeof awarenessContent === "object" && "text" in awarenessContent
    && typeof awarenessContent.text === "string" ? awarenessContent.text : "";
  check(messages[0] === toolMessage,
    "real context hook preserves the source-bearing tool result");
  check(Boolean(initiativeCitation && awareness.includes(initiativeCitation.id)),
    "shared awareness cites source metadata without repeating accepted snapshots");

  const standIn = question && initiativeCitation && blockerCitation
    ? `The blocking decision is: ${question} [${initiativeCitation.id}] Sequence #${blocker?.number} before #${dependent?.number} because the observed dependency blocks that ticket [${blockerCitation.id}]. Delivery and unrecorded overlap remain unknown.`
    : "";
  check(/\[S\d+\].*\[S\d+\]/.test(standIn),
    "deterministic stand-in answers the decision/sequence question with supplied citations");
  const blockedStandIn = await hooks["input"]({ text: standIn, images: [] }, ctxStub);
  check(Boolean(blockedStandIn && typeof blockedStandIn === "object" && "action" in blockedStandIn
    && blockedStandIn.action === "handled"),
  "adapter stand-in is not sent to a provider while disclosure remains unapproved");
  console.log(`adapter stand-in (deterministic plumbing only, not model output): ${standIn}`);

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