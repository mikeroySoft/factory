"""Per-repository configuration: `.factory.toml` at the target repo root.

Everything the factory scripts used to hardcode for one repo lives here:
the GitHub slug, the optional upstream remote, worker/reviewer commands,
and the gate's check list. Labels and branch naming (`agent/<n>`) are
conventions, not configuration.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = ".factory.toml"
LESSONS_NAME = ".factory-lessons.md"  # committed; `factory learn` writes, every worker prompt reads

# Triage roles -> label strings. Fixed by convention; `factory init` creates them.
LABEL_TRIAGE = "needs-triage"
LABEL_INFO = "needs-info"
LABEL_AGENT = "ready-for-agent"
LABEL_HUMAN = "ready-for-human"
LABEL_APPROVED = "factory-approved"
LABEL_CHORE = "chore"
LABELS = {
    LABEL_TRIAGE: ("FBCA04", "Maintainer needs to evaluate this issue"),
    LABEL_INFO: ("D4C5F9", "Waiting on reporter for more information"),
    LABEL_AGENT: ("0E8A16", "Fully specified and ready for an AFK agent"),
    LABEL_HUMAN: ("B60205", "Requires human implementation"),
    LABEL_APPROVED: ("0E8A16", "Reviewer APPROVE recorded by the factory; merge-stage precondition"),
    LABEL_CHORE: ("C2E0C6", "Mechanical task; routed to the chore worker"),
}

DEFAULT_LEAK_PATTERN = r"internal|confidential|proprietary|private|jira|confluence|\.corp|\.internal"
DEFAULT_WORKER = ["omp", "-p", "--cwd", "{cwd}", "@{prompt}"]
DEFAULT_CHORE_WORKER = ["droid", "exec", "-f", "{prompt}", "--auto", "medium", "--cwd", "{cwd}"]
DEFAULT_REVIEWER = ["omp", "-p", "--no-session", "--model", "anthropic/claude-fable-5-1", "{prompt}"]
DEFAULT_LLM_URL = "http://127.0.0.1:11434/v1/chat/completions"
DEFAULT_LLM_MODEL = "qwen3:30b"
DEFAULT_INSTALL = {"every": "10min", "dashboard": False, "host": "127.0.0.1", "env": {}}

# Host-side layer: `$XDG_CONFIG_HOME/factory/config.toml`, same table shapes
# as `.factory.toml`. `[defaults.*]` < `[repo."owner/name".*]` < the repo file.
# Only these tables/keys are taken from the host: a clone on another machine
# must run the same gate, so gate checks, leak scan and upstream never come
# from here. Everything else in the host file is left for other tools (District).
HOST_TABLES = frozenset({"triage", "workers", "review", "manager", "install"})
HOST_KEYS = {"dashboard": ("port",), "gate": ("lock",)}

# Every key the loader reads, by table; `factory doctor` reports anything else.
# `workers` is label-keyed, `gate.check` is a list of {name, run, exclusive}.
KNOWN_KEYS = {
    "repo": ("slug", "upstream", "main"),
    "dispatch": ("max_active", "max_attempts", "budget_min", "review_rounds", "cost_pattern", "signoff"),
    "workers": None,
    "review": ("command",),
    "manager": ("model", "command"),
    "gate": ("timeout", "lock", "check"),
    "leak_scan": ("pattern", "exclude"),
    "triage": ("url", "model"),
    "dashboard": ("port", "theme"),
    "install": ("every", "dashboard", "host", "env"),
}
CHECK_KEYS = ("name", "run", "exclusive")


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
    review_rounds: int = 1  # REVISE -> worker -> re-review cycles before escalating
    signoff: bool = True  # `git commit -s`; Signed-off-by trailer on merges
    cost_pattern: str | None = None  # regex with one capture: dollars in the worker log
    workers: dict[str, list[str]] = field(
        default_factory=lambda: {"default": DEFAULT_WORKER, LABEL_CHORE: DEFAULT_CHORE_WORKER}
    )
    reviewer: list[str] = field(default_factory=lambda: list(DEFAULT_REVIEWER))
    checks: list[Check] = field(default_factory=list)
    check_timeout: int = 1200
    lock: Path = Path("/tmp/factory.lock")  # host-wide: one GPU, many repos
    leak_pattern: str | None = DEFAULT_LEAK_PATTERN
    leak_exclude: list[str] = field(default_factory=list)
    llm_url: str = DEFAULT_LLM_URL
    llm_model: str = DEFAULT_LLM_MODEL
    manager_model: str | None = None  # dashboard's no-tools OMP briefing; never a command
    dashboard_port: int = 8765
    dashboard_theme: Path | None = None  # CSS file served after the built-in stylesheet
    install: dict = field(default_factory=lambda: dict(DEFAULT_INSTALL))  # `factory install` defaults
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

    def worker(self, labels: set[str], prompt: Path, cwd: Path) -> list[str]:
        """argv for the worker that owns these ticket labels (first match wins)."""
        argv = next((self.workers[k] for k in self.workers if k in labels), self.workers["default"])
        return expand(argv, prompt=str(prompt), cwd=str(cwd))

    def review_cmd(self, prompt: str) -> list[str]:
        return expand(self.reviewer, prompt=prompt)


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


def manager_model(table: dict) -> str | None:
    """Read only a model selector from legacy manager argv; never execute it."""
    if not isinstance(table, dict):
        raise ConfigError("[manager] must be a table")
    model = table.get("model")
    if model is None and "command" in table:
        command = table["command"]
        if isinstance(command, str):
            try:
                command = shlex.split(command)
            except ValueError as exc:
                raise ConfigError(f"manager.command: {exc}") from exc
        if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
            raise ConfigError("manager.command must be an argv array or command string")
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
    return model


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
    layered = merge(host_filter(host.get("defaults", {})), host_filter(host.get("repo", {}).get(slug, {})))
    raw, raw_repo = merge(layered, raw), raw
    repo_t, dispatch, workers = raw.get("repo", {}), raw.get("dispatch", {}), raw.get("workers", {})
    gate, leak, triage, dash = raw.get("gate", {}), raw.get("leak_scan", {}), raw.get("triage", {}), raw.get("dashboard", {})
    cfg = Config(root=root, repo=slug, raw_repo=raw_repo)
    cfg.upstream = repo_t.get("upstream") or None
    cfg.main = repo_t.get("main", cfg.main)
    cfg.max_active = int(dispatch.get("max_active", cfg.max_active))
    cfg.max_attempts = int(dispatch.get("max_attempts", cfg.max_attempts))
    cfg.budget_min = int(dispatch.get("budget_min", cfg.budget_min))
    cfg.review_rounds = int(dispatch.get("review_rounds", cfg.review_rounds))
    cfg.cost_pattern = dispatch.get("cost_pattern") or None
    cfg.signoff = bool(dispatch.get("signoff", cfg.signoff))
    if workers:
        if "default" not in workers:
            raise ConfigError(f"{path}: [workers] needs a `default` command")
        cfg.workers = {k: list(v) for k, v in workers.items()}
    if "command" in raw.get("review", {}):
        cfg.reviewer = list(raw["review"]["command"])
    cfg.check_timeout = int(gate.get("timeout", cfg.check_timeout))
    cfg.lock = Path(gate.get("lock", cfg.lock))
    cfg.checks = [
        Check(c["name"], list(c["run"]), bool(c.get("exclusive", False))) for c in gate.get("check", [])
    ]
    names = [c.name for c in cfg.checks]
    if len(set(names)) != len(names) or {"conflict-markers", "leak-scan"} & set(names):
        raise ConfigError(f"{path}: gate check names must be unique and not conflict-markers/leak-scan")
    if "pattern" in leak:
        cfg.leak_pattern = leak["pattern"] or None
    cfg.leak_exclude = list(leak.get("exclude", []))
    cfg.llm_url = triage.get("url", cfg.llm_url)
    cfg.llm_model = triage.get("model", cfg.llm_model)
    cfg.manager_model = manager_model(raw.get("manager", {}))
    cfg.dashboard_port = int(dash.get("port", cfg.dashboard_port))
    cfg.dashboard_theme = root / dash["theme"] if dash.get("theme") else None
    cfg.install = merge(DEFAULT_INSTALL, raw.get("install", {}))
    cfg.install["dashboard"] = bool(cfg.install["dashboard"])
    cfg.install["env"] = {k: str(v) for k, v in cfg.install["env"].items()}
    return cfg
