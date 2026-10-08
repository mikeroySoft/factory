"""Per-repository configuration: `.factory.toml` at the target repo root.

Everything the factory scripts used to hardcode for one repo lives here:
the GitHub slug, the optional upstream remote, worker/reviewer commands,
and the gate's check list. Labels and branch naming (`agent/<n>`) are
conventions, not configuration.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

from factory import lifecycle

CONFIG_NAME = ".factory.toml"
LESSONS_NAME = ".factory-lessons.md"  # committed; `factory learn` writes, every worker prompt reads

# Triage roles -> label strings. Fixed by convention; `factory init` creates them.
LABEL_VIABILITY = "needs-viability"
LABEL_REVIEW = "needs-review"
LABEL_TRIAGE = "needs-triage"
LABEL_INFO = "needs-info"
LABEL_AGENT = "ready-for-agent"
LABEL_HUMAN = "ready-for-human"
LABEL_APPROVED = "factory-approved"
LABEL_PROTECTED_OVERRIDE = "factory-protected-override"
LABEL_CHORE = "chore"
LABEL_INITIATIVE = "initiative"
LABEL_WONTFIX = "wontfix-proposal"
LABELS = {
    LABEL_VIABILITY: ("D4C5F9", "Opt in to a manager build/defer recommendation before triage"),
    LABEL_REVIEW: ("D4C5F9", "Opt in to a manager PR direction recommendation before review"),
    LABEL_TRIAGE: ("FBCA04", "Maintainer needs to evaluate this issue"),
    LABEL_INFO: ("D4C5F9", "Waiting on reporter for more information"),
    LABEL_AGENT: ("0E8A16", "Fully specified and ready for an AFK agent"),
    LABEL_HUMAN: ("B60205", "Requires human implementation"),
    LABEL_APPROVED: ("0E8A16", "Reviewer APPROVE recorded by the factory; merge-stage precondition"),
    LABEL_CHORE: ("C2E0C6", "Mechanical maintenance work routed to the chore worker"),
    LABEL_PROTECTED_OVERRIDE: ("B60205", "Human authorization for worker edits to protected paths"),
    LABEL_WONTFIX: ("EDEDED", "Triage or manager proposes not to action this; a human decides"),
    LABEL_INITIATIVE: ("1D76DB", "Shared initiative plan read by `factory plan`; never triaged, dispatched, managed or merged"),
}

# Word-bounded strong signals only: bare words like "internal"/"private" are ordinary English.
DEFAULT_LEAK_PATTERN = (r"\.(?:corp|internal|intranet|lan)\b|\bconfidential\b|\bproprietary\b"
                        r"|\bjira\b|\bconfluence\b|\.atlassian\.net\b")
DEFAULT_WORKER = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
DEFAULT_CHORE_WORKER = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd}"]
DEFAULT_REVIEWER = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]
DEFAULT_LLM_URL = "http://127.0.0.1:11434/v1/chat/completions"
DEFAULT_LLM_MODEL = "qwen3:30b"
DEFAULT_INSTALL = {"every": "10min", "dashboard": False, "host": "127.0.0.1", "python": None, "env": {}}

# Host-side layer: `$XDG_CONFIG_HOME/factory/config.toml`, same table shapes
# as `.factory.toml`. `[defaults.*]` < `[repo."owner/name".*]` < the repo file.
# Only these tables/keys are taken from the host: a clone on another machine
# must run the same gate, so gate checks, leak scan and upstream never come
# from here. Everything else in the host file is left for other tools (District).
# `worker_wrap` is host-only: a committed `[worker_wrap]` is refused, never merged.
# `leak_scan.extra` is read from both and concatenated: host terms only add to the scan.
HOST_TABLES = frozenset({"triage", "workers", "worker_wrap", "review", "manager", "install", "prices"})
HOST_KEYS = {"dashboard": ("port",), "gate": ("lock",)}

# Every key the loader reads, by table; `factory doctor` reports anything else.
# `workers` is label-keyed, `gate.check` is a list of {name, run, exclusive}.
KNOWN_KEYS = {
    "repo": ("slug", "upstream", "main"),
    "dispatch": ("max_active", "max_attempts", "budget_min", "idle_timeout", "exit_grace",
                 "review_rounds", "cost_pattern", "signoff"),
    "workers": None,
    "worker_wrap": ("command",),
    "review": ("command",),
    "manager": ("model", "command", "rounds", "review", "stale_days", "max_active_cap", "budget_min_cap"),
    "gate": ("timeout", "lock", "check", "protected_paths"),
    "leak_scan": ("pattern", "exclude", "extra"),
    "triage": ("url", "model", "timeout"),
    "journal": ("max_mb", "retention"),
    "dashboard": ("port", "theme"),
    "install": ("every", "dashboard", "host", "python", "env", "dispatch_env"),
    "collaboration": ("fallback", "reasons", "components"),
    "prices": None,
}
# Normalized numeric fields of an `llm-usage` event; a worker profile maps each to JSON paths.
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens", "cost")
CHECK_KEYS = ("name", "run", "exclusive")
ROUTE_REASONS = ("requirements", "implementation", "ci", "unknown")
# GitHub login, or `@org/team`. Syntax only: never proof of membership or authorization.
OWNER = re.compile(r"@?(?P<login>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)|(?P<team>@[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100})")


class ConfigError(SystemExit):
    def __init__(self, msg: str) -> None:
        super().__init__(f"factory: {msg}")


@dataclass
class Check:
    """One gate check: argv run inside the worktree; nonzero exit = FAIL.

    `exclusive` checks hold the host lock (shared GPU, licence server, ...)
    so two worktrees never run them at once.
    """

    name: str
    run: list[str]
    exclusive: bool = False


@dataclass
class Config:
    root: Path  # main checkout of the target repository (never a worktree)
    repo: str  # GitHub "owner/name"
    upstream: str | None = None  # remote whose main syncs into ours; None disables
    main: str = "main"
    max_active: int = 2
    max_attempts: int = 3
    budget_min: int = 90
    idle_timeout: float = 30  # minutes without worker log output or worktree change = stuck; 0 off
    exit_grace: float = 300  # seconds a done (handoff/commit) worker may stay quiet before termination; 0 off
    review_rounds: int = 1  # REVISE -> worker -> re-review cycles before escalating
    signoff: bool = True  # `git commit -s`; Signed-off-by trailer on merges
    cost_pattern: str | None = None  # regex with one capture: dollars in the worker log
    workers: dict[str, list[str]] = field(
        default_factory=lambda: {"default": DEFAULT_WORKER, LABEL_CHORE: DEFAULT_CHORE_WORKER}
    )
    worker_when: dict[str, str] = field(default_factory=dict)
    # Per worker label: JSON paths read from its log (see `usage_spec`); absent = cost_pattern only.
    worker_usage: dict[str, dict] = field(default_factory=dict)
    # Host `[prices."<model>"]`: USD per million prompt/completion/cached tokens; empty = no priced dollars.
    prices: dict[str, dict] = field(default_factory=dict)
    # Host-only argv prefix for every worker launch: a trusted operator executable, not a sandbox.
    worker_wrap: list[str] = field(default_factory=list)
    reviewer: list[str] = field(default_factory=lambda: list(DEFAULT_REVIEWER))
    manager: list[str] | None = None
    manager_rounds: int = 1
    manager_review: str = "escalated"
    manager_stale_days: int = 7
    # Ceilings for the fleet manager (`district manage`) raising `max_active`/`budget_min`; None = no cap.
    manager_max_active_cap: int | None = None
    manager_budget_min_cap: int | None = None
    checks: list[Check] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)
    check_timeout: int = 1200
    lock: Path = Path("/tmp/factory.lock")  # host-wide: one GPU, many repos
    leak_pattern: str | None = DEFAULT_LEAK_PATTERN
    leak_exclude: list[str] = field(default_factory=list)
    llm_url: str = DEFAULT_LLM_URL
    llm_model: str = DEFAULT_LLM_MODEL
    triage_timeout: int = 60  # seconds per triage model request
    manager_model: str | None = None  # dashboard's no-tools OMP briefing; never a command
    dashboard_port: int = 8765
    dashboard_theme: Path | None = None  # CSS file served after the built-in stylesheet
    journal_max_mb: int = 64  # events.jsonl rotates into events.jsonl.N.gz past this size
    journal_retention: int = 8  # gzip segments kept; older closed-execution lifecycle rows are dropped
    install: dict = field(default_factory=lambda: dict(DEFAULT_INSTALL))  # `factory install` defaults
    # `[collaboration]`: human decision owners for `factory plan route`; None = section absent (legacy behaviour).
    collaboration: dict | None = None
    raw_repo: dict = field(default_factory=dict)  # the committed file alone, before host layering

    @property
    def name(self) -> str:
        return self.repo.rsplit("/", 1)[-1]

    @property
    def factory(self) -> Path:
        """On-disk state: worktrees, locks, logs, prompts. Gitignored."""
        return self.root / ".factory"

    @property
    def unit(self) -> str:
        """systemd user-unit stem: `<unit>.timer`, `<unit>.service`, `<unit>-dashboard.service`."""
        return f"factory-{self.name}"

    def worker_key(self, labels: set[str]) -> str:
        """The `[workers]` label that owns these ticket labels (first match wins)."""
        return next((k for k in self.workers if k in labels), "default")

    def worker(self, labels: set[str], prompt: Path, cwd: Path) -> list[str]:
        """argv for the worker that owns these ticket labels (first match wins)."""
        argv = self.workers[self.worker_key(labels)]
        return expand([*self.worker_wrap, *argv], prompt=str(prompt), cwd=str(cwd),
                      root=str(self.root), repo=self.repo, home=str(Path.home()))

    def review_cmd(self, prompt: str) -> list[str]:
        return expand(self.reviewer, prompt=prompt)

    def manager_cmd(self, prompt_path: Path, cwd: Path) -> list[str]:
        """Expand the manager's prompt file, matching the worker transport."""
        argv = self.manager or []
        if argv and Path(argv[0]).name == "omp" and "{prompt}" in argv:
            raise ConfigError('manager.command: use "@{prompt}" instead of bare "{prompt}" for omp')
        return expand(argv, prompt=str(prompt_path), cwd=str(cwd))


def expand(argv: list[str], **values: str) -> list[str]:
    """Substitute `{name}` placeholders; literal braces elsewhere are left alone."""
    out = []
    for arg in argv:
        for key, val in values.items():
            arg = arg.replace("{" + key + "}", val)
        out.append(arg)
    return out


def git(root: Path | None, *args: str) -> str:
    cmd = ["git", *(("-C", str(root)) if root else ()), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise ConfigError(f"`{' '.join(cmd)}` failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def repo_root(start: Path | None = None) -> Path:
    """Main checkout root, even when called from inside one of its worktrees."""
    common = git(start, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent


def remote_slug(root: Path, remote: str) -> str:
    url = git(root, "remote", "get-url", remote)
    if "github.com" not in url:
        raise ConfigError(f"remote `{remote}` ({url}) is not on github.com; set [repo].slug")
    return url.rsplit("github.com", 1)[-1].strip(":/").removesuffix(".git")


def host_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "factory" / "config.toml"


def host_config() -> dict:
    path = host_config_path()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def engine_commit() -> str | None:
    """Source commit of the installed engine: District's `[defaults.engine].sha`,
    else the uv install's source directory, which District names by commit."""
    engine = host_config().get("defaults", {}).get("engine", {})
    if isinstance(engine, dict) and isinstance(engine.get("sha"), str) and engine["sha"]:
        return engine["sha"]
    try:
        url = json.loads(metadata.distribution("factory").read_text("direct_url.json") or "{}").get("url", "")
    except (metadata.PackageNotFoundError, ValueError):
        return None
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return name if re.fullmatch(r"[0-9a-f]{40}", name) else None


def engine_drift(cfg: Config) -> str | None:
    """Warning when `cfg.root` is factory's own source and origin/<main> is not the installed engine."""
    try:
        name = tomllib.loads((cfg.root / "pyproject.toml").read_text()).get("project", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    commit = engine_commit() if name == "factory" else None
    if commit is None:
        return None
    main = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "--verify", "--quiet", f"origin/{cfg.main}"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    if not main or main.startswith(commit):
        return None
    return (f"installed {commit[:12]} differs from origin/{cfg.main} {main[:12]}; "
            "merged engine changes are not installed")


def host_filter(table: dict) -> dict:
    """Keep only the host-owned tables/keys of one host section."""
    out = {k: table[k] for k in HOST_TABLES if isinstance(table.get(k), dict)}
    for name, keys in HOST_KEYS.items():
        sub = table.get(name)
        if isinstance(sub, dict) and (kept := {k: sub[k] for k in keys if k in sub}):
            out[name] = kept
    return out


def merge(base: dict, over: dict) -> dict:
    """Recursive on dicts; scalars and lists in `over` replace."""
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def unknown_keys(raw: dict) -> list[str]:
    """Dotted paths in a `.factory.toml`-shaped dict the loader does not read."""
    out = []
    for table, val in raw.items():
        if table not in KNOWN_KEYS:
            out.append(table)
            continue
        known = KNOWN_KEYS[table]
        if known is None or not isinstance(val, dict):
            continue
        out += [f"{table}.{k}" for k in val if k not in known]
        if table == "gate":
            for i, c in enumerate(val.get("check", [])):
                out += [f"gate.check[{i}].{k}" for k in c if k not in CHECK_KEYS]
    return out


def owner(value: object, where: str) -> str:
    """Normalize one configured owner: `login` or `@org/team`; syntactic invalidity is a ConfigError."""
    match = OWNER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ConfigError(f"{where}: expected a GitHub login or @org/team, got {value!r}")
    return match["login"] or match["team"]


def collaboration_settings(table: object) -> dict:
    """Validate `[collaboration]`: fallback owner, per-reason owners, exact path-prefix component owners."""
    if not isinstance(table, dict):
        raise ConfigError("[collaboration] must be a table")
    out = {"fallback": None, "reasons": {}, "components": {}}
    if "fallback" in table:
        out["fallback"] = owner(table["fallback"], "collaboration.fallback")
    reasons, components = table.get("reasons", {}), table.get("components", {})
    if not isinstance(reasons, dict) or not isinstance(components, dict):
        raise ConfigError("collaboration.reasons and collaboration.components must be tables")
    for reason, value in reasons.items():
        if reason not in ROUTE_REASONS[:-1]:
            raise ConfigError(f"collaboration.reasons.{reason}: expected one of {', '.join(ROUTE_REASONS[:-1])}")
        out["reasons"][reason] = owner(value, f"collaboration.reasons.{reason}")
    for prefix, value in components.items():
        parts = prefix.strip("/").split("/")
        if not prefix or prefix.startswith("/") or "\\" in prefix or any(p in ("", ".", "..") for p in parts):
            raise ConfigError(f"collaboration.components: {prefix!r} is not a repo-relative path prefix")
        normalized = "/".join(parts)
        if normalized in out["components"]:
            raise ConfigError(f"collaboration.components: duplicate normalized prefix {normalized!r}")
        out["components"][normalized] = owner(value, f"collaboration.components.{prefix!r}")
    return out


def usage_spec(label: str, spec: object) -> dict:
    """Validate `workers.<label>.usage`: dotted JSON paths summed per field, a `model` path, a `match` filter."""
    where = f"workers.{label}.usage"
    if not isinstance(spec, dict) or set(spec) - {*USAGE_FIELDS, "model", "match"}:
        raise ConfigError(f"{where} must be a table of {', '.join(USAGE_FIELDS)}, model, match")
    out: dict = {}
    for key in USAGE_FIELDS:
        paths = spec.get(key, [])
        out[key] = [paths] if isinstance(paths, str) else paths
        if not isinstance(out[key], list) or not all(isinstance(p, str) and p for p in out[key]):
            raise ConfigError(f"{where}.{key} must be a JSON path or an array of paths")
    out["model"], out["match"] = spec.get("model"), spec.get("match", {})
    if out["model"] is not None and not (isinstance(out["model"], str) and out["model"]):
        raise ConfigError(f"{where}.model must be a JSON path")
    if not isinstance(out["match"], dict):
        raise ConfigError(f"{where}.match must be a table of JSON path = value")
    return out


def price_table(table: object) -> dict:
    """Validate `[prices."<model>"]`: nonnegative USD per million `prompt`, `completion`, optional `cached` tokens."""
    if not isinstance(table, dict):
        raise ConfigError("[prices] must be a table")
    for model, price in table.items():
        if (
            not isinstance(price, dict) or not {"prompt", "completion"} <= set(price) <= {"prompt", "completion", "cached"}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in price.values())
        ):
            raise ConfigError(f"prices.{model!r} needs nonnegative prompt and completion (optional cached) USD per million tokens")
    return table


def manager_settings(table: dict) -> tuple[list[str] | None, str | None]:
    """Normalize manager argv and select the read-only briefing model; never execute."""
    if not isinstance(table, dict):
        raise ConfigError("[manager] must be a table")
    model = table.get("model")
    command = None
    if "command" in table:
        command = table["command"]
        if isinstance(command, str):
            try:
                command = shlex.split(command)
            except ValueError as exc:
                raise ConfigError(f"manager.command: {exc}") from exc
        if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
            raise ConfigError("manager.command must be an argv array or command string")
    if model is None and command:
        for i, arg in enumerate(command):
            if arg == "--model":
                if i + 1 == len(command):
                    raise ConfigError("manager.command: --model needs a value")
                model = command[i + 1]
            elif arg.startswith("--model="):
                model = arg.split("=", 1)[1]
    if model is not None and (
        not isinstance(model, str) or not model or len(model) > 200
        or model.startswith("-") or any(c.isspace() or ord(c) < 32 for c in model)
    ):
        raise ConfigError("manager.model must be a nonempty model selector (≤ 200 chars, no whitespace)")
    return command, model


def load(start: Path | None = None) -> Config:
    """Load `<root>/.factory.toml` over the host layer; every key optional except a resolvable repo slug."""
    root = repo_root(start)
    path = root / CONFIG_NAME
    raw: dict = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    slug = raw.get("repo", {}).get("slug") or remote_slug(root, "origin")
    host = host_config()
    host_sections = (host.get("defaults", {}), host.get("repo", {}).get(slug, {}))
    for section in host_sections:
        if "worker_wrap" in section and not isinstance(section["worker_wrap"], dict):
            raise ConfigError(f"{host_config_path()}: worker_wrap must be a table")
    layered = merge(host_filter(host_sections[0]), host_filter(host_sections[1]))
    raw, raw_repo = merge(layered, raw), raw
    repo_t, dispatch, workers = raw.get("repo", {}), raw.get("dispatch", {}), raw.get("workers", {})
    gate, leak, triage, dash = raw.get("gate", {}), raw.get("leak_scan", {}), raw.get("triage", {}), raw.get("dashboard", {})
    manager = raw.get("manager", {})
    cfg = Config(root=root, repo=slug, raw_repo=raw_repo)
    cfg.upstream = repo_t.get("upstream") or None
    cfg.main = repo_t.get("main", cfg.main)
    cfg.max_active = int(dispatch.get("max_active", cfg.max_active))
    cfg.max_attempts = int(dispatch.get("max_attempts", cfg.max_attempts))
    cfg.budget_min = int(dispatch.get("budget_min", cfg.budget_min))
    cfg.idle_timeout = float(dispatch.get("idle_timeout", cfg.idle_timeout))
    cfg.exit_grace = float(dispatch.get("exit_grace", cfg.exit_grace))
    if cfg.idle_timeout < 0 or cfg.exit_grace < 0:
        raise ConfigError("dispatch.idle_timeout and dispatch.exit_grace must be >= 0 (0 disables)")
    cfg.review_rounds = int(dispatch.get("review_rounds", cfg.review_rounds))
    cfg.cost_pattern = dispatch.get("cost_pattern") or None
    cfg.signoff = bool(dispatch.get("signoff", cfg.signoff))
    if workers:
        if "default" not in workers:
            raise ConfigError(f"{path}: [workers] needs a `default` command")
        cfg.workers = {}
        for label, entry in workers.items():
            command = entry.get("command") if isinstance(entry, dict) else entry
            when = entry.get("when", "") if isinstance(entry, dict) else ""
            if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command) or not command[0]:
                raise ConfigError(f"{path}: workers.{label} needs a non-empty command argv")
            if not isinstance(when, str):
                raise ConfigError(f"{path}: workers.{label}.when must be text")
            cfg.workers[label] = command
            if isinstance(entry, dict) and "usage" in entry:
                cfg.worker_usage[label] = usage_spec(label, entry["usage"])
            if when:
                cfg.worker_when[label] = when
    if "worker_wrap" in raw_repo:
        raise ConfigError(f"{path}: [worker_wrap] is host-only; set it in {host_config_path()} "
                          "[defaults.worker_wrap] or [repo.\"<slug>\".worker_wrap]")
    wrap = raw.get("worker_wrap", {})
    if "command" in wrap:
        command = wrap["command"]
        if not isinstance(command, list) or not command or any(not isinstance(a, str) for a in command) or not command[0]:
            raise ConfigError("worker_wrap.command needs a non-empty argv array of strings")
        cfg.worker_wrap = command
    if "command" in raw.get("review", {}):
        cfg.reviewer = list(raw["review"]["command"])
    cfg.manager, cfg.manager_model = manager_settings(manager)
    cfg.manager_rounds = int(manager.get("rounds", cfg.manager_rounds))
    cfg.manager_review = manager.get("review", cfg.manager_review)
    if cfg.manager_review not in ("escalated", "all"):
        raise ConfigError("manager.review must be escalated or all")
    cfg.manager_stale_days = manager.get("stale_days", cfg.manager_stale_days)
    if type(cfg.manager_stale_days) is not int or cfg.manager_stale_days <= 0:
        raise ConfigError("manager.stale_days must be a positive integer")
    for key in ("max_active_cap", "budget_min_cap"):
        cap = manager.get(key)
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, int) or cap < 1):
            raise ConfigError(f"manager.{key} must be a positive integer")
        setattr(cfg, f"manager_{key}", cap)
    journal = raw.get("journal", {})
    for key in ("max_mb", "retention"):
        value = journal.get(key, getattr(cfg, f"journal_{key}"))
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"journal.{key} must be a positive integer")
        setattr(cfg, f"journal_{key}", value)
    if "collaboration" in raw:
        cfg.collaboration = collaboration_settings(raw["collaboration"])
    cfg.prices = price_table(raw.get("prices", {}))
    cfg.check_timeout = int(gate.get("timeout", cfg.check_timeout))
    paths = gate.get("protected_paths", [])
    if not isinstance(paths, list) or any(not isinstance(p, str) or not p for p in paths):
        raise ConfigError("gate.protected_paths must be an array of nonempty globs")
    cfg.protected_paths = paths
    cfg.lock = Path(gate.get("lock", cfg.lock))
    cfg.checks = [
        Check(c["name"], list(c["run"]), bool(c.get("exclusive", False))) for c in gate.get("check", [])
    ]
    names = [c.name for c in cfg.checks]
    if len(set(names)) != len(names) or {"conflict-markers", "leak-scan", "protected-paths"} & set(names):
        raise ConfigError(f"{path}: gate check names must be unique and not conflict-markers/leak-scan/protected-paths")
    if "pattern" in leak:
        cfg.leak_pattern = leak["pattern"] or None
    cfg.leak_exclude = list(leak.get("exclude", []))
    extra = [section.get("leak_scan", {}).get("extra", []) for section in (*host_sections, raw_repo)]
    if any(not isinstance(e, list) or any(not isinstance(t, str) or not t for t in e) for e in extra):
        raise ConfigError("leak_scan.extra needs an array of non-empty regex strings")
    if terms := [t for e in extra for t in e]:
        cfg.leak_pattern = "|".join(p for p in (cfg.leak_pattern, *terms) if p)
    cfg.llm_url = triage.get("url", cfg.llm_url)
    cfg.llm_model = triage.get("model", cfg.llm_model)
    cfg.triage_timeout = int(triage.get("timeout", cfg.triage_timeout))
    cfg.dashboard_port = int(dash.get("port", cfg.dashboard_port))
    cfg.dashboard_theme = root / dash["theme"] if dash.get("theme") else None
    cfg.install = merge(DEFAULT_INSTALL, raw.get("install", {}))
    python = cfg.install["python"]
    if python is not None and (not isinstance(python, str) or not python):
        raise ConfigError("[install].python must be a non-empty path")
    cfg.install["dashboard"] = bool(cfg.install["dashboard"])
    cfg.install["env"] = {k: str(v) for k, v in cfg.install["env"].items()}
    if "dispatch_env" in cfg.install:  # optional; absent keeps cfg.install as it was
        cfg.install["dispatch_env"] = {k: str(v) for k, v in cfg.install["dispatch_env"].items()}
    lifecycle.MAX_BYTES = cfg.journal_max_mb * 1024 * 1024
    lifecycle.RETENTION = cfg.journal_retention
    return cfg


def dashboard_env(cfg: Config) -> dict[str, str]:
    """This process's environment as the dashboard unit sees it: `[install].dispatch_env`
    keys dropped, `[install].env` applied. Its `gh` is the human identity."""
    env = {k: v for k, v in os.environ.items() if k not in cfg.install.get("dispatch_env", {})}
    return {**env, **cfg.install["env"]}
