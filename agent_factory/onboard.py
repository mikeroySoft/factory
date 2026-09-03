"""`factory init`, `factory doctor`, `factory install`: onboarding one repository."""

from __future__ import annotations

import argparse
import os
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from agent_factory import config
from agent_factory.config import CONFIG_NAME, LABELS, ConfigError

TEMPLATES = Path(__file__).with_name("templates")
GITIGNORE_LINES = ("/.factory/", ".factory-prompt.md")
ISSUE_TEMPLATE = Path(".github/ISSUE_TEMPLATE/agent_task.md")


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


def init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory init",
        description="Prepare this repository: .factory.toml, .gitignore, issue template, labels.",
    )
    parser.add_argument("--no-labels", action="store_true", help="skip creating GitHub labels")
    args = parser.parse_args(argv)

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

    if not args.no_labels:
        failed = []
        for name, (color, desc) in LABELS.items():
            proc = sh(
                ["gh", "label", "create", name, "--repo", slug, "--color", color,
                 "--description", desc, "--force"],
            )
            if proc.returncode != 0:
                failed.append(f"{name}: {proc.stderr.strip()}")
        if failed:
            done.append("label creation FAILED:\n    " + "\n    ".join(failed))
        else:
            done.append(f"ensured {len(LABELS)} labels on {slug}")

    print(f"factory init: {slug} at {root}")
    for item in done:
        print(f"  - {item}")
    print(
        "\nnext:\n"
        f"  1. edit {CONFIG_NAME}: put your real test/lint commands in [[gate.check]]\n"
        f"  2. git add {CONFIG_NAME} .gitignore {ISSUE_TEMPLATE} && git commit\n"
        "  3. factory doctor\n"
        "  4. factory install --dashboard   # systemd user timer, every 10 min"
    )
    return 0


# ---------------------------------------------------------------- doctor


def doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory doctor", description="Check tools, auth, remotes, and the triage model."
    )
    parser.parse_args(argv)
    cfg = config.load()
    fails = 0

    def report(status: bool | None, label: str, detail: str = "") -> None:
        nonlocal fails
        tag = "PASS" if status else ("WARN" if status is None else "FAIL")
        fails += status is False
        print(f"  {tag}  {label}{f': {detail}' if detail else ''}")

    print(f"factory doctor: {cfg.repo} at {cfg.root}")

    present = (cfg.root / CONFIG_NAME).exists()
    report(present, f"{CONFIG_NAME} present", "" if present else "run `factory init`")
    tracked = sh(["git", "ls-files", "--error-unmatch", CONFIG_NAME], cwd=cfg.root).returncode == 0
    report(True if tracked else None, f"{CONFIG_NAME} committed", "" if tracked else "commit it so clones see it")

    for tool in ("git", "gh"):
        report(shutil.which(tool) is not None, f"{tool} on PATH")
    auth = sh(["gh", "auth", "status"])
    report(auth.returncode == 0, "gh authenticated", "" if auth.returncode == 0 else auth.stderr.strip().splitlines()[-1])
    perm = sh(["gh", "repo", "view", cfg.repo, "--json", "viewerPermission", "--jq", ".viewerPermission"])
    level = perm.stdout.strip()
    report(level in ("WRITE", "MAINTAIN", "ADMIN"), f"push access to {cfg.repo}", level or perm.stderr.strip())

    labels = sh(["gh", "label", "list", "--repo", cfg.repo, "--limit", "200", "--json", "name", "--jq", "[.[].name]"])
    have = set(json.loads(labels.stdout or "[]"))
    missing = sorted(set(LABELS) - have)
    report(None if missing else True, "factory labels", f"missing {', '.join(missing)} (factory init)" if missing else "all present")

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

    print(f"\n{'FAIL' if fails else 'OK'}: {fails} blocking problem(s)")
    return 1 if fails else 0


# ---------------------------------------------------------------- install


def unit_dir() -> Path:
    return config.host_config_path().parents[1] / "systemd" / "user"


def units(cfg: config.Config, every: str, host: str) -> dict[str, str]:
    exe = f"{sys.executable} -m agent_factory"
    # At boot the user manager's PATH is the systemd default (no ~/.local/bin),
    # so gh/omp/codex vanish; carry the installing shell's PATH into the units.
    # [install].env (host config) adds one line each: policy such as UV_EXCLUDE_NEWER.
    env = f"Environment=PATH={os.environ['PATH']}\n"
    env += "".join(f"Environment={k}={v}\n" for k, v in cfg.install["env"].items())
    return {
        f"{cfg.unit}.service": (
            f"[Unit]\nDescription=agent-factory dispatcher for {cfg.repo} (one pass)\n\n"
            f"[Service]\nType=oneshot\nWorkingDirectory={cfg.root}\n{env}ExecStart={exe} dispatch\n"
        ),
        f"{cfg.unit}.timer": (
            f"[Unit]\nDescription=Run the agent-factory dispatcher for {cfg.repo} every {every}\n\n"
            f"[Timer]\nOnBootSec=5min\nOnUnitActiveSec={every}\n\n[Install]\nWantedBy=timers.target\n"
        ),
        f"{cfg.unit}-dashboard.service": (
            f"[Unit]\nDescription=agent-factory dashboard for {cfg.repo}\nAfter=network.target\n\n"
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
    changed = set()
    for name, body in wanted.items():
        path = udir / name
        if not path.exists() or path.read_text() != body:
            path.write_text(body)
            changed.add(name)
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
