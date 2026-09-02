"""Per-repository configuration: `.factory.toml` at the target repo root.

Everything the factory scripts used to hardcode for one repo lives here:
the GitHub slug, the optional upstream remote, worker/reviewer commands,
and the gate's check list. Labels and branch naming (`agent/<n>`) are
conventions, not configuration.
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = ".factory.toml"

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
DEFAULT_REVIEWER = ["codex", "exec", "{prompt}"]
DEFAULT_LLM_URL = "http://127.0.0.1:11434/v1/chat/completions"
DEFAULT_LLM_MODEL = "qwen3:30b"


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
    signoff: bool = True  # `git commit -s`; Signed-off-by trailer on merges
    workers: dict[str, list[str]] = field(
        default_factory=lambda: {"default": DEFAULT_WORKER, LABEL_CHORE: DEFAULT_CHORE_WORKER}
    )
    reviewer: list[str] = field(default_factory=lambda: list(DEFAULT_REVIEWER))
    checks: list[Check] = field(default_factory=list)
    check_timeout: int = 1200
    lock: Path = Path("/tmp/agent-factory.lock")  # host-wide: one GPU, many repos
    leak_pattern: str | None = DEFAULT_LEAK_PATTERN
    leak_exclude: list[str] = field(default_factory=list)
    llm_url: str = DEFAULT_LLM_URL
    llm_model: str = DEFAULT_LLM_MODEL
    dashboard_port: int = 8765

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


def load(start: Path | None = None) -> Config:
    """Load `<root>/.factory.toml`; every key optional except a resolvable repo slug."""
    root = repo_root(start)
    path = root / CONFIG_NAME
    raw: dict = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    repo_t, dispatch, workers = raw.get("repo", {}), raw.get("dispatch", {}), raw.get("workers", {})
    gate, leak, triage, dash = raw.get("gate", {}), raw.get("leak_scan", {}), raw.get("triage", {}), raw.get("dashboard", {})
    slug = repo_t.get("slug") or remote_slug(root, "origin")
    cfg = Config(root=root, repo=slug)
    cfg.upstream = repo_t.get("upstream") or None
    cfg.main = repo_t.get("main", cfg.main)
    cfg.max_active = int(dispatch.get("max_active", cfg.max_active))
    cfg.max_attempts = int(dispatch.get("max_attempts", cfg.max_attempts))
    cfg.budget_min = int(dispatch.get("budget_min", cfg.budget_min))
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
    cfg.dashboard_port = int(dash.get("port", cfg.dashboard_port))
    return cfg
