"""`factory init`, `factory doctor`, `factory install`: onboarding one repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from factory import __version__, config
from factory.config import CONFIG_NAME, LABELS, ConfigError

TEMPLATES = Path(__file__).with_name("templates")
GITIGNORE_LINES = ("/.factory/", ".factory-prompt.md")
ISSUE_TEMPLATE = Path(".github/ISSUE_TEMPLATE/agent_task.md")
WORKFLOWS = Path(".github/workflows")
CI_WORKFLOW = WORKFLOWS / "ci.yml"


def workflows(root: Path) -> list[Path]:
    return sorted(p for p in (root / WORKFLOWS).glob("*.y*ml") if p.suffix in (".yml", ".yaml"))


def sh(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)


def ensure_line(path: Path, line: str) -> bool:
    text = path.read_text() if path.exists() else ""
    if line in text.splitlines():
        return False
    sep = "" if not text or text.endswith("\n") else "\n"
    path.write_text(f"{text}{sep}{line}\n")
    return True


# ---------------------------------------------------------------- init


def ensure_labels(slug: str) -> list[str]:
    """Create or update the six factory labels; returns one line per failure."""
    failed = []
    for name, (color, desc) in LABELS.items():
        proc = sh(["gh", "label", "create", name, "--repo", slug, "--color", color, "--description", desc, "--force"])
        if proc.returncode != 0:
            failed.append(f"{name}: {proc.stderr.strip()}")
    return failed


def init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory init",
        description="Prepare this repository: .factory.toml, .gitignore, issue template, labels.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-labels", action="store_true", help="skip creating GitHub labels")
    group.add_argument("--labels-only", action="store_true", help="only ensure the labels; touch no files")
    args = parser.parse_args(argv)

    if args.labels_only:
        slug = config.load().repo
        failed = ensure_labels(slug)
        print(f"factory init: {'label creation FAILED on' if failed else f'ensured {len(LABELS)} labels on'} {slug}")
        for line in failed:
            print(f"  - {line}")
        return 1 if failed else 0

    root = config.repo_root()
    slug = config.remote_slug(root, "origin")
    done: list[str] = []

    cfg_path = root / CONFIG_NAME
    if cfg_path.exists():
        done.append(f"kept existing {CONFIG_NAME}")
    else:
        shutil.copyfile(TEMPLATES / "factory.toml", cfg_path)
        done.append(f"wrote {CONFIG_NAME}")

    for line in GITIGNORE_LINES:
        if ensure_line(root / ".gitignore", line):
            done.append(f"added `{line}` to .gitignore")

    tmpl = root / ISSUE_TEMPLATE
    if tmpl.exists():
        done.append(f"kept existing {ISSUE_TEMPLATE}")
    else:
        tmpl.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATES / "agent_task.md", tmpl)
        done.append(f"wrote {ISSUE_TEMPLATE}")

    # The merge stage refuses a PR with no passing GitHub check; give every repo one.
    if workflows(root):
        done.append(f"kept existing {WORKFLOWS}/*.yml")
    else:
        (root / WORKFLOWS).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(TEMPLATES / "ci.yml", root / CI_WORKFLOW)
        done.append(f"wrote {CI_WORKFLOW}")

    if not args.no_labels:
        failed = ensure_labels(slug)
        if failed:
            done.append("label creation FAILED:\n    " + "\n    ".join(failed))
        else:
            done.append(f"ensured {len(LABELS)} labels on {slug}")

    print(f"factory init: {slug} at {root}")
    for item in done:
        print(f"  - {item}")
    print(
        "\nnext:\n"
        f"  1. edit {CONFIG_NAME}: put your real test/lint commands in [[gate.check]],\n"
        f"     and the same commands in {CI_WORKFLOW} (the merge stage needs a passing check)\n"
        f"  2. git add {CONFIG_NAME} .gitignore {ISSUE_TEMPLATE} {WORKFLOWS} && git commit\n"
        "  3. factory doctor\n"
        "  4. factory install --dashboard   # systemd user timer, every 10 min"
    )
    return 0


# ---------------------------------------------------------------- doctor


def committed_host_keys(raw: dict) -> list[str]:
    """Host-owned tables/keys present in a repo file: the District adopt signal."""
    out = [t for t in sorted(config.HOST_TABLES) if raw.get(t)]
    out += [f"{t}.{k}" for t, keys in config.HOST_KEYS.items() for k in keys if k in raw.get(t, {})]
    return out


def unset_repo_keys(raw: dict) -> list[str]:
    """Loader-known, repo-owned keys the file leaves at their defaults."""
    out = []
    for table, keys in config.KNOWN_KEYS.items():
        if table in config.HOST_TABLES or keys is None:
            continue
        skip = {"slug", "check", *config.HOST_KEYS.get(table, ())}
        out += [f"{table}.{k}" for k in keys if k not in skip and k not in raw.get(table, {})]
    return out


def foreign_host_keys(section: dict) -> list[str]:
    """Tables/keys in one host section the loader ignores (repo-owned or unknown)."""
    out = [k for k, v in section.items() if isinstance(v, dict) and k not in config.HOST_TABLES and k not in config.HOST_KEYS]
    out += [f"{t}.{k}" for t, keys in config.HOST_KEYS.items() for k in section.get(t, {}) if k not in keys]
    return out + config.unknown_keys(config.host_filter(section))


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory doctor", description="Check tools, auth, remotes, config drift, and the triage model."
    )
    parser.add_argument("--json", action="store_true", help="emit {ok, version, repo, root, rows} instead of text")
    args = parser.parse_args(argv)
    cfg = config.load()
    rows: list[dict] = []

    def report(status: bool | None, label: str, detail: str = "", *, info: bool = False) -> None:
        tag = "INFO" if info else "PASS" if status else ("WARN" if status is None else "FAIL")
        rows.append({"status": tag, "label": label, "detail": detail})

    present = (cfg.root / CONFIG_NAME).exists()
    report(present, f"{CONFIG_NAME} present", "" if present else "run `factory init`")
    tracked = sh(["git", "ls-files", "--error-unmatch", CONFIG_NAME], cwd=cfg.root).returncode == 0
    report(True if tracked else None, f"{CONFIG_NAME} committed", "" if tracked else "commit it so clones see it")

    unknown = config.unknown_keys(cfg.raw_repo)
    report(None if unknown else True, f"{CONFIG_NAME} keys", f"unknown (ignored): {', '.join(unknown)}" if unknown else "all known")
    hosted = committed_host_keys(cfg.raw_repo)
    report(None if hosted else True, "host settings committed", f"move to {config.host_config_path()}: {', '.join(hosted)}" if hosted else "none")
    unset = unset_repo_keys(cfg.raw_repo)
    if unset:
        report(None, "defaults in effect", ", ".join(unset), info=True)

    shipped, ours = sha256(TEMPLATES / "agent_task.md"), sha256(cfg.root / ISSUE_TEMPLATE)
    report(
        True if ours == shipped else None, f"{ISSUE_TEMPLATE}",
        "matches shipped template" if ours == shipped else ("missing (factory init)" if ours is None else "differs from shipped template"),
    )
    host = config.host_config()
    if host:
        sections = [("defaults", host.get("defaults", {}))] + [(f'repo."{s}"', t) for s, t in host.get("repo", {}).items()]
        foreign = [f"{name}.{k}" for name, sec in sections if isinstance(sec, dict) for k in foreign_host_keys(sec)]
        report(None if foreign else True, "host config", f"ignored (not host-owned): {', '.join(foreign)}" if foreign else str(config.host_config_path()))

    for tool in ("git", "gh"):
        report(shutil.which(tool) is not None, f"{tool} on PATH")
    auth = sh(["gh", "auth", "status"])
    report(auth.returncode == 0, "gh authenticated", "" if auth.returncode == 0 else (auth.stderr.strip().splitlines() or ["?"])[-1])
    perm = sh(["gh", "repo", "view", cfg.repo, "--json", "viewerPermission", "--jq", ".viewerPermission"])
    level = perm.stdout.strip()
    report(level in ("WRITE", "MAINTAIN", "ADMIN"), f"push access to {cfg.repo}", level or perm.stderr.strip())

    labels = sh(["gh", "label", "list", "--repo", cfg.repo, "--limit", "200", "--json", "name", "--jq", "[.[].name]"])
    try:
        have = set(json.loads(labels.stdout or "[]"))
    except ValueError:
        have = set()
    missing = sorted(set(LABELS) - have)
    report(None if missing else True, "factory labels", f"missing {', '.join(missing)} (factory init)" if missing else "all present")

    flows = workflows(cfg.root)
    placeholder = any('run: "true"' in p.read_text() for p in flows)
    report(
        None if not flows or placeholder else True, "github workflow",
        "none: the merge stage refuses PRs with no passing check (factory init writes one)" if not flows
        else (f"{CI_WORKFLOW} still runs the placeholder step; make it run the gate commands" if placeholder else ", ".join(p.name for p in flows)),
    )

    if cfg.upstream:
        ok = sh(["git", "remote", "get-url", cfg.upstream], cwd=cfg.root).returncode == 0
        report(ok, f"upstream remote `{cfg.upstream}`", "" if ok else "add it or unset [repo].upstream")

    for label, argv_t in cfg.workers.items():
        report(shutil.which(argv_t[0]) is not None, f"worker `{label}`: {argv_t[0]}")
    report(shutil.which(cfg.reviewer[0]) is not None, f"reviewer: {cfg.reviewer[0]}")

    if cfg.checks:
        for check in cfg.checks:
            exe = check.run[0]
            found = shutil.which(exe) is not None or (cfg.root / exe).exists()
            report(found, f"gate check `{check.name}`: {exe}{' (exclusive)' if check.exclusive else ''}")
    else:
        report(None, "gate checks", "none configured; only conflict-markers and leak-scan run")

    if cfg.signoff:
        ident = sh(["git", "config", "user.name"], cwd=cfg.root).stdout.strip()
        report(bool(ident), "git identity for Signed-off-by", ident or "set user.name/user.email")

    base = cfg.llm_url.rsplit("/chat/completions", 1)[0]
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=3):
            report(True, "triage model endpoint", cfg.llm_url)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        report(None, "triage model endpoint", f"{cfg.llm_url} ({exc}); `factory triage` will not run")

    timer = sh(["systemctl", "--user", "is-active", f"{cfg.unit}.timer"]).stdout.strip()
    report(True if timer == "active" else None, f"systemd timer {cfg.unit}.timer", timer or "not installed (factory install)")

    fails = sum(r["status"] == "FAIL" for r in rows)
    if args.json:
        print(json.dumps({"ok": not fails, "version": __version__, "repo": cfg.repo, "root": str(cfg.root), "rows": rows}, indent=2))
        return 1 if fails else 0
    print(f"factory doctor: {cfg.repo} at {cfg.root}")
    for r in rows:
        detail = f": {r['detail']}" if r["detail"] else ""
        print(f"  {r['status']}  {r['label']}{detail}")
    print(f"\n{'FAIL' if fails else 'OK'}: {fails} blocking problem(s)")
    return 1 if fails else 0


# ---------------------------------------------------------------- install


def unit_dir() -> Path:
    return config.host_config_path().parents[1] / "systemd" / "user"


def units(cfg: config.Config, every: str, host: str) -> dict[str, str]:
    exe = f"{sys.executable} -m factory"
    # At boot the user manager's PATH is the systemd default (no ~/.local/bin),
    # so gh/omp/codex vanish; carry the installing shell's PATH into the units.
    # [install].env (host config) adds one line each: policy such as UV_EXCLUDE_NEWER.
    env = f"Environment=PATH={os.environ['PATH']}\n"
    env += "".join(f"Environment={k}={v}\n" for k, v in cfg.install["env"].items())
    return {
        f"{cfg.unit}.service": (
            f"[Unit]\nDescription=factory dispatcher for {cfg.repo} (one pass)\n\n"
            f"[Service]\nType=oneshot\nWorkingDirectory={cfg.root}\n{env}ExecStart={exe} dispatch\n"
        ),
        f"{cfg.unit}.timer": (
            f"[Unit]\nDescription=Run the factory dispatcher for {cfg.repo} every {every}\n\n"
            f"[Timer]\nOnBootSec=5min\nOnUnitActiveSec={every}\n\n[Install]\nWantedBy=timers.target\n"
        ),
        f"{cfg.unit}-dashboard.service": (
            f"[Unit]\nDescription=factory dashboard for {cfg.repo}\nAfter=network.target\n\n"
            f"[Service]\nWorkingDirectory={cfg.root}\n{env}"
            f"ExecStart={exe} dashboard --host {host} --port {cfg.dashboard_port} --no-open\n"
            f"Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
        ),
    }


def systemctl(*args: str) -> None:
    proc = sh(["systemctl", "--user", *args])
    if proc.returncode != 0:
        raise ConfigError(f"systemctl --user {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")


def install(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory install",
        description="Install systemd user units: a dispatcher timer and, optionally, the dashboard. "
        "Defaults come from [install] in the host config; re-running converges the unit set.",
    )
    parser.add_argument("--every", help="dispatcher interval, systemd time span (default 10min)")
    parser.add_argument(
        "--dashboard", action=argparse.BooleanOptionalAction, help="install and start the dashboard service",
    )
    parser.add_argument(
        "--host",
        help="dashboard bind address; 0.0.0.0 exposes /api/act (mutates GitHub with your gh credentials) to the LAN",
    )
    parser.add_argument("--print", action="store_true", help="print the units instead of installing them")
    cfg = config.load()
    parser.set_defaults(**{k: cfg.install[k] for k in ("every", "dashboard", "host")})
    args = parser.parse_args(argv)

    wanted = units(cfg, args.every, args.host)
    dash_unit = f"{cfg.unit}-dashboard.service"
    if not args.dashboard:
        wanted.pop(dash_unit)
    if args.print:
        for name, body in wanted.items():
            print(f"# {name}\n{body}")
        return 0
    if shutil.which("systemctl") is None:
        raise ConfigError("systemctl not found; run `factory dispatch` from cron or by hand instead")

    udir = unit_dir()
    udir.mkdir(parents=True, exist_ok=True)
    changed = set()  # existing units whose content differs: these get restarted
    for name, body in wanted.items():
        path = udir / name
        if path.exists() and path.read_text() == body:
            continue
        if path.exists():
            changed.add(name)
        path.write_text(body)
        print(f"wrote {path}")
    if not args.dashboard and (udir / dash_unit).exists():
        systemctl("disable", "--now", dash_unit)
        (udir / dash_unit).unlink()
        print(f"removed {udir / dash_unit}")
    systemctl("daemon-reload")
    timer = f"{cfg.unit}.timer"
    systemctl("enable", "--now", timer)
    if timer in changed:
        systemctl("restart", timer)
    print(f"{'restarted' if timer in changed else 'started'} {timer}")
    if args.dashboard:
        systemctl("enable", "--now", dash_unit)
        if dash_unit in changed:
            systemctl("restart", dash_unit)
        print(f"{'restarted' if dash_unit in changed else 'started'} {dash_unit}")
    if sh(["loginctl", "show-user", "--property=Linger", "--value", Path.home().name]).stdout.strip() != "yes":
        print("hint: `loginctl enable-linger` keeps user timers running after logout and at boot")
    print(f"stop with: systemctl --user disable --now {cfg.unit}.timer{f' {dash_unit}' if args.dashboard else ''}")
    return 0
