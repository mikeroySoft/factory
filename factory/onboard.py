"""`factory init`, `factory doctor`, `factory install`: onboarding one repository."""

from __future__ import annotations

import argparse
import ast
import difflib
import errno
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

from factory import __version__, config, lifecycle
from factory.config import BANDS_NAME, CONFIG_NAME, LABELS, ConfigError

TEMPLATES = Path(__file__).with_name("templates")
GITIGNORE_LINES = ("/.factory/", ".factory-prompt.md")
ISSUE_TEMPLATE = Path(".github/ISSUE_TEMPLATE/agent_task.md")
INITIATIVE_TEMPLATE = Path(".github/ISSUE_TEMPLATE/initiative.md")
WORKFLOWS = Path(".github/workflows")
CI_WORKFLOW = WORKFLOWS / "ci.yml"


def workflows(root: Path) -> list[Path]:
    return sorted(p for p in (root / WORKFLOWS).glob("*.y*ml") if p.suffix in (".yml", ".yaml"))


def sh(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def gh_login(env: dict[str, str]) -> str:
    """`gh api user` login under `env`; empty when gh cannot authenticate."""
    proc = subprocess.run(["gh", "api", "user", "--jq", ".login"], env=env, capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _ssh_key_material(root: Path, configured: str) -> str | None:
    prefixes = ("ssh-", "ecdsa-", "sk-")
    if configured.startswith("key::"):
        configured = configured.removeprefix("key::")
    if configured.startswith(prefixes):
        parts = configured.split()
        return " ".join(parts[:2]) if len(parts) >= 2 else None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = root / path
    candidates = [path] if path.suffix == ".pub" else [path.with_name(path.name + ".pub"), path]
    for candidate in candidates:
        try:
            parts = candidate.read_text().strip().split()
        except OSError:
            continue
        if len(parts) >= 2 and parts[0].startswith(prefixes):
            return " ".join(parts[:2])
    return None


def ssh_signing_key_status(cfg: config.Config) -> tuple[bool | None, str] | None:
    """Whether GitHub registers the SSH key Git uses to sign Factory commits."""
    if sh(["git", "config", "--get", "gpg.format"], cwd=cfg.root).stdout.strip().casefold() != "ssh":
        return None
    enabled = sh(["git", "config", "--bool", "--get", "commit.gpgsign"], cwd=cfg.root)
    if enabled.returncode or enabled.stdout.strip().casefold() != "true":
        return None
    configured = sh(["git", "config", "--get", "user.signingkey"], cwd=cfg.root).stdout.strip()
    if not configured:
        return None, "unknown_key: commit.gpgsign is enabled but user.signingkey is unset"
    material = _ssh_key_material(cfg.root, configured)
    if material is None:
        return None, f"unknown_key: cannot read configured SSH signing key {configured}"
    environment = {
        **os.environ,
        **cfg.install.get("env", {}),
        **cfg.install.get("dispatch_env", {}),
    }
    if "PATH" in os.environ:
        environment["PATH"] = os.environ["PATH"]
    try:
        login = sh(["gh", "api", "user", "--jq", ".login"], env=environment)
        if login.returncode or not login.stdout.strip():
            detail = login.stderr.strip() or "authenticated login unavailable"
            return None, f"unknown_key: could not determine dispatcher GitHub account ({detail})"
        registered = sh(
            [
                "gh", "api", f"users/{login.stdout.strip()}/ssh_signing_keys",
                "--paginate", "--jq", ".[].key",
            ],
            env=environment,
        )
    except OSError as exc:
        return None, f"could not verify configured SSH signing key with GitHub: {exc}"
    if registered.returncode:
        detail = registered.stderr.strip() or str(registered.returncode)
        return None, f"could not verify configured SSH signing key with GitHub: {detail}"
    keys = {
        " ".join(line.split()[:2])
        for line in registered.stdout.splitlines()
        if len(line.split()) >= 2
    }
    if material not in keys:
        return None, "unknown_key: configured SSH signing key is not registered on the dispatcher GitHub account"
    return True, "configured SSH signing key is registered on the dispatcher GitHub account"


def ensure_line(path: Path, line: str) -> bool:
    text = path.read_text() if path.exists() else ""
    if line in text.splitlines():
        return False
    sep = "" if not text or text.endswith("\n") else "\n"
    path.write_text(f"{text}{sep}{line}\n")
    return True


# ---------------------------------------------------------------- init


def ensure_labels(slug: str) -> list[str]:
    """Create or update the factory labels; returns one line per failure."""
    failed = []
    for name, (color, desc) in LABELS.items():
        proc = sh(["gh", "label", "create", name, "--repo", slug, "--color", color, "--description", desc, "--force"])
        if proc.returncode != 0:
            failed.append(f"{name}: {proc.stderr.strip()}")
    return failed


def init(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory init",
        description=f"Prepare this repository: {CONFIG_NAME}, {BANDS_NAME}, issue templates, labels.",
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
    bands_path = root / BANDS_NAME
    if bands_path.exists():
        done.append(f"kept existing {BANDS_NAME}")
    else:
        shutil.copyfile(TEMPLATES / BANDS_NAME, bands_path)
        done.append(f"wrote {BANDS_NAME}")

    for line in GITIGNORE_LINES:
        if ensure_line(root / ".gitignore", line):
            done.append(f"added `{line}` to .gitignore")

    for tmpl in (ISSUE_TEMPLATE, INITIATIVE_TEMPLATE):
        target = root / tmpl
        if target.exists():
            done.append(f"kept existing {tmpl}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / tmpl.name, target)
            done.append(f"wrote {tmpl}")

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
        f"  2. git add {CONFIG_NAME} {BANDS_NAME} .gitignore {ISSUE_TEMPLATE.parent} {WORKFLOWS} && git commit\n"
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


def foreign_host_keys(section: dict, *, defaults: bool = False) -> list[str]:
    """Report unsupported host tables/keys, excluding District's defaults.engine metadata."""
    out = [
        k for k, v in section.items()
        if isinstance(v, dict) and k not in config.HOST_TABLES and k not in config.HOST_KEYS
        and not (defaults and k == "engine") and not (k == "leak_scan" and set(v) <= {"extra"})
    ]
    out += [f"{t}.{k}" for t, keys in config.HOST_KEYS.items() for k in section.get(t, {}) if k not in keys]
    return out + config.unknown_keys(config.host_filter(section))


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def template_fix(root: Path, template: Path) -> dict:
    target = root / template
    ours = target.read_bytes().decode() if target.exists() else ""
    shipped = (TEMPLATES / template.name).read_bytes().decode()
    lines = difflib.unified_diff(
        ours.splitlines(keepends=True), shipped.splitlines(keepends=True),
        fromfile=f"a/{template}", tofile=f"b/{template}",
    )
    diff = "".join(
        line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
        for line in lines
    )
    return {"kind": "patch", "diff": diff, "advisory": True}


def relocation_fix(cfg: config.Config, *, reveal: bool) -> dict:
    import tomlkit

    def key_paths(value: dict, prefix: str) -> list[str]:
        return [
            path
            for key, item in value.items()
            for path in (key_paths(item, f"{prefix}.{key}") if isinstance(item, dict) and item
                         else [f"{prefix}.{key}"])
        ]

    hosted = config.host_filter(cfg.raw_repo)
    tables = sorted(t for t in config.HOST_TABLES if t in hosted)
    tables += [t for t in config.HOST_KEYS if t in hosted]
    fix = {"kind": "relocate", "tables": [
        {"table": f"[repo.{json.dumps(cfg.repo)}.{table}]", "keys": key_paths(hosted[table], table)}
        for table in tables
    ]}
    if reveal:
        fix["toml"] = tomlkit.dumps({"repo": {cfg.repo: hosted}})
    return fix


def unknown_key_fix(path: Path, unknown: list[str]) -> dict:
    from bisect import bisect_right

    from tomlkit.items import AbstractTable
    from tomlkit.parser import Parser

    source = path.read_text()
    newlines = [i for i, char in enumerate(source) if char == "\n"]

    # tomlkit exposes no source spans. Capture them in preserved trivia
    # at its parser boundary, including inline tables and array-table entries.
    class LocatedParser(Parser):
        def _parse_key_value(self, parse_comment=False):
            line = bisect_right(newlines, self._idx) + 1
            key, value = super()._parse_key_value(parse_comment)
            value.trivia._factory_line = line
            return key, value

        def _parse_table(self, parent_name=None, parent=None):
            line = bisect_right(newlines, self._idx) + 1
            key, value = super()._parse_table(parent_name, parent)
            value.trivia._factory_line = line
            return key, value

    locations = {}

    def locate(value, prefix=""):
        lines = [getattr(getattr(value, "trivia", None), "_factory_line", None)]
        if isinstance(value, dict):
            container = value.value if isinstance(value, AbstractTable) else value
            for key in value:
                lines.append(locate(container.item(key), f"{prefix}.{key}" if prefix else key))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                lines.append(locate(child, f"{prefix}[{i}]"))
        line = min((line for line in lines if line is not None), default=None)
        locations[prefix] = line
        return line

    locate(LocatedParser(source).parse())
    return {"kind": "keys", "keys": [{"key": key, "line": locations[key]} for key in unknown]}


def dashboard_port_error(cfg: config.Config, host: str, port: int, reason: str = "is in use") -> str:
    return (
        f'factory dashboard: port {port} on {host} {reason}; set [repo."{cfg.repo}".dashboard] port '
        f"in {config.host_config_path()} (each factory needs its own port)"
    )


def _listener_conflicts(local: str, target: str) -> bool:
    address = local.rpartition(":")[0].strip("[]").split("%", 1)[0]
    if address == "*":
        return True
    try:
        listener = ipaddress.ip_address(address)
        requested = ipaddress.ip_address(target)
    except ValueError:
        return True
    if listener.version == 6 and listener.ipv4_mapped:
        listener = listener.ipv4_mapped
    if listener.version != requested.version:
        return listener.is_unspecified
    return listener.is_unspecified or requested.is_unspecified or listener == requested


def _dashboard_port_check(cfg: config.Config, host: str, port: int) -> tuple[bool, str]:
    unit = f"{cfg.unit}-dashboard.service"
    try:
        target = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4]
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        return False, dashboard_port_error(cfg, host, port, f"cannot be bound: {exc.strerror or exc}")

    with probe:
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(target)
            collision = False
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                return False, dashboard_port_error(cfg, host, port, f"cannot be bound: {exc.strerror or exc}")
            collision = True

        # Keep a free probe held through `ss`; the later install-to-service bind
        # remains inherently racy. A collision that vanishes here fails closed.
        try:
            listeners = sh(["ss", "-ltnp", f"sport = :{port}"])
        except OSError as exc:
            return False, dashboard_port_error(cfg, host, port, f"could not be checked (ss: {exc})")
        if listeners.returncode != 0:
            detail = listeners.stderr.strip() or str(listeners.returncode)
            return False, dashboard_port_error(cfg, host, port, f"could not be checked (ss: {detail})")
        if not collision:
            return True, f"available on {host}"

    holders: list[tuple[str, int]] = []
    unknown = False
    for line in listeners.stdout.splitlines():
        fields = line.split(maxsplit=5)
        if not fields or fields[0] == "State" or len(fields) < 4 or not _listener_conflicts(fields[3], target[0]):
            continue
        found = re.findall(r'\("([^"]+)",pid=(\d+)', line)
        unknown |= not found
        holders.extend((name, int(pid)) for name, pid in found)
    if unknown or not holders:
        return False, dashboard_port_error(cfg, host, port, "is in use by an unknown process")

    names = ", ".join(dict.fromkeys(name for name, _ in holders))
    try:
        owner = sh(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit])
    except OSError as exc:
        return False, dashboard_port_error(
            cfg, host, port, f"is in use by {names} (could not verify {unit}: {exc})"
        )
    try:
        main_pid = int(owner.stdout.strip())
    except ValueError:
        main_pid = -1
    if owner.returncode != 0 or main_pid < 0:
        detail = owner.stderr.strip() or owner.stdout.strip() or str(owner.returncode)
        return False, dashboard_port_error(
            cfg, host, port, f"is in use by {names} (could not verify {unit}: {detail})"
        )
    foreign = [name for name, pid in holders if pid != main_pid]
    if foreign or main_pid == 0:
        names = ", ".join(dict.fromkeys(foreign or (name for name, _ in holders)))
        return False, dashboard_port_error(cfg, host, port, f"is in use by {names}")
    return True, f"owned by {unit} (pid {main_pid})"


def report_stage_success(cfg: config.Config, report) -> None:
    try:
        passes = lifecycle.last_successful_passes(cfg.factory / "events.jsonl")
    except (OSError, ValueError) as exc:
        report(None, "last successful pass", f"unavailable: {exc}")
        return
    detail = "; ".join(f"{stage} {timestamp or 'never'}" for stage, timestamp in passes.items())
    report(True, "last successful pass", detail, info=True)


def second_reviewer_problem(cfg: config.Config, which) -> str | None:
    command = cfg.second_reviewer
    if not command:
        return "not configured; High-risk PRs cannot merge"
    if which(command[0]) is None:
        return f"cannot launch {command[0]}; High-risk PRs cannot merge"
    primary = config.command_model(cfg.reviewer)
    second = config.command_model(command)
    if second is None:
        return "model is not explicit; High-risk PRs cannot prove reviewer independence"
    if primary is None:
        return "primary reviewer model is not explicit; High-risk PRs cannot prove reviewer independence"
    if primary == second:
        return f"same model as primary reviewer ({second}); High-risk PRs cannot merge"
    for worker in cfg.workers:
        model = cfg.worker_model(worker)
        if model is None:
            return f"worker `{worker}` model is not explicit; High-risk PRs cannot prove reviewer independence"
        if model == primary:
            return f"worker `{worker}` uses the same model as primary reviewer ({model})"
        if model == second:
            return f"worker `{worker}` uses the same model as second reviewer ({model})"
    return None


def doctor(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory doctor", description="Check tools, auth, remotes, config drift, and the triage model."
    )
    parser.add_argument("--json", action="store_true", help="emit {ok, version, repo, root, rows} instead of text")
    parser.add_argument("--reveal-fix", action="store_true", help="include host TOML values in JSON fix payloads")
    args = parser.parse_args(argv)
    cfg = config.load()
    rows: list[dict] = []

    def report(status: bool | None, label: str, detail: str = "", *, info: bool = False, fix: dict | None = None) -> None:
        tag = "INFO" if info else "PASS" if status else ("WARN" if status is None else "FAIL")
        rows.append({"status": tag, "label": label, "detail": detail})
        if args.json and fix is not None:
            rows[-1]["fix"] = fix

    present = (cfg.root / CONFIG_NAME).exists()
    report(present, f"{CONFIG_NAME} present", "" if present else "run `factory init`")
    tracked = sh(["git", "ls-files", "--error-unmatch", CONFIG_NAME], cwd=cfg.root).returncode == 0
    report(True if tracked else None, f"{CONFIG_NAME} committed", "" if tracked else "commit it so clones see it")
    bands_present = (cfg.root / BANDS_NAME).exists()
    report(
        True if bands_present else None,
        f"{BANDS_NAME} present",
        "" if bands_present else "missing; control-band alarms are disabled (run `factory init`)",
    )
    bands_tracked = sh(["git", "ls-files", "--error-unmatch", BANDS_NAME], cwd=cfg.root).returncode == 0
    report(
        True if bands_tracked else None,
        f"{BANDS_NAME} committed",
        "" if bands_tracked else "commit it so clones use the reviewed thresholds and tiers",
    )

    unknown = config.unknown_keys(cfg.raw_repo)
    report(
        None if unknown else True, f"{CONFIG_NAME} keys",
        f"unknown (ignored): {', '.join(unknown)}" if unknown else "all known",
        fix=unknown_key_fix(cfg.root / CONFIG_NAME, unknown) if args.json and unknown else None,
    )
    hosted = committed_host_keys(cfg.raw_repo)
    report(
        None if hosted else True, "host settings committed",
        f"move to {config.host_config_path()}: {', '.join(hosted)}" if hosted else "none",
        fix=relocation_fix(cfg, reveal=args.reveal_fix) if args.json and hosted else None,
    )
    mismatches = config.intended_state_mismatches(cfg)
    mismatched = {key for key, _, _ in mismatches}
    unset = [key for key in unset_repo_keys(cfg.raw_repo) if key not in mismatched]
    if unset:
        report(None, "defaults in effect", ", ".join(unset), info=True)
    for key, expected, actual in mismatches:
        report(
            False,
            f"intended state {key}",
            f"declared {str(expected).lower()}, configured {str(actual).lower()}; live config unchanged",
        )
    report_stage_success(cfg, report)
    if cfg.watch_bands:
        from factory import watch

        detail = watch.baseline_status(cfg, lifecycle.read_events(cfg.factory / "events.jsonl"))
        report(None if detail else True, "watch control bands", detail or f"{len(cfg.watch_bands)} configured")

    for tmpl in (ISSUE_TEMPLATE, INITIATIVE_TEMPLATE):
        shipped, ours = sha256(TEMPLATES / tmpl.name), sha256(cfg.root / tmpl)
        report(
            True if ours == shipped else None, f"{tmpl}",
            "matches shipped template" if ours == shipped else ("missing (factory init)" if ours is None else "differs from shipped template"),
            fix=template_fix(cfg.root, tmpl) if args.json and tmpl == ISSUE_TEMPLATE and ours != shipped else None,
        )
    host = config.host_config()
    if host:
        sections = [("defaults", host.get("defaults", {}))] + [(f'repo."{s}"', t) for s, t in host.get("repo", {}).items()]
        foreign = [f"{name}.{k}" for name, sec in sections if isinstance(sec, dict) for k in foreign_host_keys(sec, defaults=name == "defaults")]
        report(None if foreign else True, "host config", f"ignored (not host-owned): {', '.join(foreign)}" if foreign else str(config.host_config_path()))
    if cfg.install.get("python") is not None:
        ok, detail = interpreter_probe(cfg)
        report(ok, "service interpreter", detail)
    if drift := config.engine_drift(cfg):
        report(None, "installed engine", drift)


    for tool in ("git", "gh"):
        report(shutil.which(tool) is not None, f"{tool} on PATH")
    auth = sh(["gh", "auth", "status"])
    report(auth.returncode == 0, "gh authenticated", "" if auth.returncode == 0 else (auth.stderr.strip().splitlines() or ["?"])[-1])
    if signing := ssh_signing_key_status(cfg):
        registered, detail = signing
        report(registered, "SSH signing key registered", detail)
    if dispatch_env := cfg.install.get("dispatch_env"):
        dispatcher = gh_login({**os.environ, **cfg.install["env"], **dispatch_env})
        dashboard = gh_login(config.dashboard_env(cfg))
        same = dispatcher.casefold() == dashboard.casefold()
        report(
            bool(dispatcher and dashboard) and not same, "GitHub identities",
            f"dispatcher {dispatcher or '?'}, dashboard {dashboard or '?'}"
            + ("; [install].dispatch_env must select a different account" if dispatcher and same else ""),
        )
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
    if any(check.when == "release" for check in cfg.checks):
        release_flows = [p.name for p in flows if "--tier release" in p.read_text()]
        report(
            True if release_flows else None, "release gate workflow",
            ", ".join(release_flows) if release_flows
            else f'{CONFIG_NAME} has `when = "release"` checks but no workflow runs `factory gate --tier release` on a v* tag',
        )

    if cfg.upstream:
        ok = sh(["git", "remote", "get-url", cfg.upstream], cwd=cfg.root).returncode == 0
        report(ok, f"upstream remote `{cfg.upstream}`", "" if ok else "add it or unset [repo].upstream")

    # The installed service runs with the PATH baked into its unit, not the caller's.
    unit_file = unit_dir() / f"{cfg.unit}.service"
    lines = unit_file.read_text().splitlines() if unit_file.exists() else []
    unit_path = next((ln.removeprefix("Environment=PATH=") for ln in reversed(lines) if ln.startswith("Environment=PATH=")), None)
    on_path = "" if unit_path is None else f"PATH from {unit_file}"

    def which(exe: str) -> str | None:
        return shutil.which(exe, path=unit_path)

    for label, argv_t in cfg.workers.items():
        report(which(argv_t[0]) is not None, f"worker `{label}`: {argv_t[0]}", on_path)
    if cfg.worker_wrap:
        report(which(cfg.worker_wrap[0]) is not None, f"worker wrap: {cfg.worker_wrap[0]}", on_path)
    report(which(cfg.reviewer[0]) is not None, f"reviewer: {cfg.reviewer[0]}", on_path)
    second_problem = second_reviewer_problem(cfg, which)
    report(
        None if second_problem else True,
        f"second reviewer: {cfg.second_reviewer[0] if cfg.second_reviewer else 'unset'}",
        second_problem or on_path,
    )
    if not cfg.manager:
        report(None, "manager command", "[manager].command is unset; escalations get no automated diagnosis")
    else:
        problems = [
            f"missing `{{{name}}}` placeholder"
            for name in ("prompt", "cwd")
            if not any(f"{{{name}}}" in arg for arg in cfg.manager)
        ]
        if Path(cfg.manager[0]).name == "omp" and "{prompt}" in cfg.manager:
            problems.append('omp needs a prompt file; use "@{prompt}"')
        if which(cfg.manager[0]) is None:
            problems.append(f"executable not on {on_path or 'PATH'}: {cfg.manager[0]}")
        report(not problems, "manager command", "; ".join(problems) if problems else cfg.manager[0])

    if cfg.checks:
        for check in cfg.checks:
            exe = check.run[0]
            found = which(exe) is not None or (cfg.root / exe).exists()
            report(found, f"gate check `{check.name}`: {exe}{' (exclusive)' if check.exclusive else ''}", on_path)
    else:
        report(None, "gate checks", "none configured; only conflict-markers and leak-scan run")

    if cfg.signoff:
        ident = sh(["git", "config", "user.name"], cwd=cfg.root).stdout.strip()
        report(bool(ident), "git identity for Signed-off-by", ident or "set user.name/user.email")

    from factory import triage

    alive, detail = triage.probe_endpoint(cfg, opener=urllib.request.urlopen)
    report(
        True if alive else None,
        "triage model endpoint",
        f"{cfg.llm_url} ({detail})" if alive else f"{cfg.llm_url} ({detail}); `factory triage` will not run",
    )

    timer = sh(["systemctl", "--user", "is-active", f"{cfg.unit}.timer"]).stdout.strip()
    report(True if timer == "active" else None, f"systemd timer {cfg.unit}.timer", timer or "not installed (factory install)")
    if cfg.triage_service:
        probe_timer = f"{cfg.unit}-triage-probe.timer"
        active = sh(["systemctl", "--user", "is-active", probe_timer]).stdout.strip()
        report(
            True if active == "active" else None,
            "triage liveness probe",
            f"{probe_timer} {active}" if active else "not installed (factory install)",
        )
    else:
        report(
            None,
            "triage liveness probe",
            "disabled; set [triage].service to a systemd user service unit",
            info=True,
        )

    port_ok, port_detail = _dashboard_port_check(cfg, cfg.install["host"], cfg.dashboard_port)
    report(port_ok, f"dashboard port {cfg.dashboard_port}", port_detail)

    from factory.workspace import access_file_problem, key_directory_problem

    key_problem = key_directory_problem(cfg.factory) or access_file_problem(cfg.factory)
    report(key_problem is None, f"{cfg.factory.name} directory safe", key_problem or "")

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

INTERPRETER_PROBE = """
import importlib
import importlib.util
import json
import sysconfig

result = {
    "origins": {},
    "purelib": sysconfig.get_path("purelib"),
    "platlib": sysconfig.get_path("platlib"),
}
try:
    for name in ("factory", "factory.cli", "factory.triage", "factory.dispatch", "factory.dashboard"):
        spec = importlib.util.find_spec(name)
        result["origins"][name] = spec.origin if spec else None
        module = importlib.import_module(name)
        result["origins"][name] = module.__file__
    import factory
    result["version"] = getattr(factory, "__version__", "unknown")
    from factory.cli import COMMANDS
    for command in ("triage", "triage-probe", "dispatch", "dashboard"):
        entry = COMMANDS.get(command)
        if not entry:
            raise RuntimeError(f"missing {command} command")
        module, function, _ = entry
        getattr(importlib.import_module(f"factory.{module}"), function)
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result))
raise SystemExit("error" in result)
"""


def service_python(cfg: config.Config) -> Path:
    """Interpreter rendered into service units; keep a configured venv symlink intact."""
    value = cfg.install.get("python")
    if value is None:
        return Path(sys.executable)
    path = Path(value).expanduser()
    return path if path.is_absolute() else cfg.root / path


def systemd_argument(value: str) -> str:
    """Render one literal ExecStart argument, without shell or systemd expansion."""
    if any(ord(char) < 32 or 127 <= ord(char) < 160 for char in value):
        raise ConfigError("service interpreter path contains a control character")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("$", "$$").replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return escaped
    return f'"{escaped}"'


def interpreter_probe(cfg: config.Config) -> tuple[bool, str]:
    """Validate a configured interpreter under the generated services' cwd and environment."""
    python = service_python(cfg)
    try:
        systemd_argument(str(python))
    except ConfigError as exc:
        return False, str(exc)
    if not python.exists():
        return False, f"{python} does not exist"
    if not python.is_file():
        return False, f"{python} is not a file"
    if not os.access(python, os.X_OK):
        return False, f"{python} is not executable"

    command = "systemctl --user show-environment"
    try:
        manager = sh(["systemctl", "--user", "show-environment"])
    except (OSError, UnicodeError) as exc:
        return False, f"{command} failed: {exc}; check the user systemd manager"
    if manager.returncode:
        return False, (
            f"{command} failed: {manager.stderr.strip() or manager.returncode}; "
            "check the user systemd manager"
        )
    env = {}
    lines = manager.stdout.removesuffix("\n").split("\n") if manager.stdout else []
    for number, line in enumerate(lines, 1):
        try:
            name, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError("expected NAME=VALUE")
            if name in env:
                raise ValueError(f"duplicate assignment for {name}")
            if value.startswith("$'"):
                if not re.fullmatch(
                    r"\$'(?:[^'\\\x00-\x1f]|\\(?:[abfnrtv\\'\"]|[0-7]{3}|x[0-9a-fA-F]{2}))*'",
                    value,
                ):
                    raise ValueError("invalid ANSI-C quoted value")
                values = [ast.literal_eval(value[1:])]
            else:
                values = shlex.split(value, comments=False, posix=True)
            if len(values) > 1 or (value and not values):
                raise ValueError("expected one shell-quoted value")
            decoded = values[0] if values else ""
            if "\0" in decoded:
                raise ValueError("environment value contains NUL")
            env[name] = decoded
        except (ValueError, SyntaxError) as exc:
            return False, (
                f"{command} returned invalid assignment output at line {number}: {exc}; "
                "inspect and correct the user manager environment"
            )
    env["PATH"] = os.environ["PATH"]
    env.update(cfg.install["env"])
    try:
        proc = subprocess.run(
            [str(python), "-P", "-c", INTERPRETER_PROBE],
            cwd=cfg.root, env=env, capture_output=True, text=True, check=False, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, f"{python} probe timed out after 10 seconds"
    except OSError as exc:
        return False, f"{python} could not run: {exc}"

    try:
        result = json.loads(proc.stdout.splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        detail = " ".join((proc.stderr.strip() or f"exit {proc.returncode} without probe output").split())
        return False, f"{python} could not load Factory service commands: {detail[:500]}"

    origins = result.get("origins", {})
    location = origins.get("factory")
    version = result.get("version", "unknown")
    identity = f"Factory {version} at {location}" if location else "Factory not importable"
    details = ", ".join(
        f"{name}={origins.get(name)}"
        for name in ("factory", "factory.cli", "factory.triage", "factory.dispatch", "factory.dashboard")
    )
    details += f", purelib={result.get('purelib')}, platlib={result.get('platlib')}"
    identity += f" ({details})"
    installed = [Path(result[key]) for key in ("purelib", "platlib") if result.get(key)]
    for name, origin in origins.items():
        if not origin:
            continue
        # Check both spellings: resolving alone hides lexical checkout imports,
        # while lexical checks alone miss symlinks back into the checkout.
        for resolve in (False, True):
            root = cfg.root.resolve() if resolve else Path(os.path.abspath(cfg.root))
            loaded = Path(origin).resolve() if resolve else Path(os.path.abspath(origin))
            package_roots = [
                path.resolve() if resolve else Path(os.path.abspath(path)) for path in installed
            ]
            if loaded.is_relative_to(root) and not any(
                loaded.is_relative_to(path) for path in package_roots
            ):
                return False, (
                    f"{python} loaded {identity}; {name} comes from the repository checkout; "
                    "remove the PYTHONPATH/import override and install Factory into the interpreter"
                )
    if proc.returncode:
        error = " ".join(
            str(result.get("error") or proc.stderr.strip() or f"exit {proc.returncode}").split()
        )
        if location:
            return False, f"{python} loaded {identity}, but service commands failed: {error[:500]}"
        return False, f"{python} could not load Factory service commands: {error[:500]} ({details})"
    return True, f"{python} loads {identity}"


def systemd_environment(key: str, value: str) -> str:
    """One `Environment=` line; systemd splits unquoted assignments on whitespace, so
    `UV_EXCLUDE_NEWER=24 hours` would set `24` and drop `hours`."""
    assignment = f"{key}={value}".replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if re.fullmatch(r"[A-Za-z0-9_./:@+,=-]+", assignment):
        return f"Environment={assignment}\n"
    return f'Environment="{assignment}"\n'


def units(cfg: config.Config, every: str, host: str) -> dict[str, str]:
    exe = f"{systemd_argument(str(service_python(cfg)))} -P -m factory"
    # At boot the user manager's PATH is the systemd default (no ~/.local/bin),
    # so gh/omp/codex vanish; carry the installing shell's PATH into the units.
    # [install].env (host config) adds one line each: policy such as UV_EXCLUDE_NEWER.
    # [install].dispatch_env goes only into the triage+dispatcher unit: its GitHub identity.
    env = f"Environment=PATH={os.environ['PATH']}\n"
    env += "".join(systemd_environment(k, v) for k, v in cfg.install["env"].items())
    dispatch_env = env + "".join(systemd_environment(k, v) for k, v in cfg.install.get("dispatch_env", {}).items())
    result = {
        f"{cfg.unit}.service": (
            f"[Unit]\nDescription=factory triage + dispatcher for {cfg.repo} (one pass)\n\n"
            # `-` prefix: an offline triage model must not stop the dispatch pass.
            f"[Service]\nType=oneshot\nWorkingDirectory={cfg.root}\n{dispatch_env}"
            f"ExecStart=-{exe} triage\nExecStart={exe} dispatch\n"
            # Post-pass monitor, after success or failure: systemd hands it $SERVICE_RESULT,
            # so a crashed pass files itself. `-`: the monitor never fails the unit.
            f"ExecStopPost=-{exe} watch\n"
        ),
        f"{cfg.unit}.timer": (
            f"[Unit]\nDescription=Run the factory pass for {cfg.repo} every {every}\n\n"
            f"[Timer]\nOnBootSec=5min\nOnUnitActiveSec={every}\nRandomizedDelaySec=90\n\n"
            f"[Install]\nWantedBy=timers.target\n"
        ),
        f"{cfg.unit}-dashboard.service": (
            f"[Unit]\nDescription=factory dashboard for {cfg.repo}\nAfter=network.target\n\n"
            f"[Service]\nWorkingDirectory={cfg.root}\n{env}"
            f"ExecStart={exe} dashboard --host {host} --port {cfg.dashboard_port} --no-open\n"
            f"Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n"
        ),
    }
    if cfg.triage_service:
        result.update({
            f"{cfg.unit}-triage-probe.service": (
                f"[Unit]\nDescription=Probe the triage model for {cfg.repo}\n\n"
                f"[Service]\nType=oneshot\nWorkingDirectory={cfg.root}\n{env}"
                f"ExecStart={exe} triage-probe\n"
            ),
            f"{cfg.unit}-triage-probe.timer": (
                f"[Unit]\nDescription=Probe the triage model for {cfg.repo} every 2 minutes\n\n"
                "[Timer]\nOnBootSec=2min\nOnUnitActiveSec=2min\nRandomizedDelaySec=30\n\n"
                "[Install]\nWantedBy=timers.target\n"
            ),
        })
    if cfg.eval_schedule:
        profile = systemd_argument(cfg.eval_profile)
        result.update({
            f"{cfg.unit}-eval.service": (
                f"[Unit]\nDescription=Run the frozen worker eval suite for {cfg.repo}\n\n"
                f"[Service]\nType=oneshot\nWorkingDirectory={cfg.root}\n{env}"
                f"ExecStart={exe} eval --profile {profile} --scheduled\n"
            ),
            f"{cfg.unit}-eval.timer": (
                f"[Unit]\nDescription=Run the frozen worker eval suite for {cfg.repo} weekly\n\n"
                "[Timer]\nOnCalendar=weekly\nPersistent=true\nRandomizedDelaySec=30min\n\n"
                "[Install]\nWantedBy=timers.target\n"
            ),
        })
    return result


def systemctl(*args: str) -> None:
    proc = sh(["systemctl", "--user", *args])
    if proc.returncode != 0:
        raise ConfigError(f"systemctl --user {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")


def install(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="factory install",
        description="Install systemd user units: a dispatcher timer and, optionally, the dashboard. "
        "Defaults come from [install] in the host config; [install].python selects the service "
        "interpreter. --print only renders units and does not validate the interpreter.",
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
    probe_service = f"{cfg.unit}-triage-probe.service"
    probe_timer = f"{cfg.unit}-triage-probe.timer"
    eval_service = f"{cfg.unit}-eval.service"
    eval_timer = f"{cfg.unit}-eval.timer"
    if not args.dashboard:
        wanted.pop(dash_unit)
    if args.print:
        for name, body in wanted.items():
            print(f"# {name}\n{body}")
        return 0
    if cfg.install.get("python") is not None:
        ok, detail = interpreter_probe(cfg)
        if not ok:
            raise ConfigError(f"service interpreter: {detail}")
        print(f"validated service interpreter: {detail}")
    if shutil.which("systemctl") is None:
        raise ConfigError("systemctl not found; run `factory dispatch` from cron or by hand instead")
    if args.dashboard:
        port_ok, port_detail = _dashboard_port_check(cfg, args.host, cfg.dashboard_port)
        if not port_ok:
            print(port_detail, file=sys.stderr)
            return 1

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
    if not cfg.triage_service:
        if (udir / probe_timer).exists():
            systemctl("disable", "--now", probe_timer)
        for name in (probe_service, probe_timer):
            if (udir / name).exists():
                (udir / name).unlink()
                print(f"removed {udir / name}")
    if not cfg.eval_schedule:
        if (udir / eval_timer).exists():
            systemctl("disable", "--now", eval_timer)
        for name in (eval_service, eval_timer):
            if (udir / name).exists():
                (udir / name).unlink()
                print(f"removed {udir / name}")
    systemctl("daemon-reload")
    timer = f"{cfg.unit}.timer"
    systemctl("enable", "--now", timer)
    if timer in changed:
        systemctl("restart", timer)
    print(f"{'restarted' if timer in changed else 'started'} {timer}")
    if cfg.triage_service:
        systemctl("enable", "--now", probe_timer)
        if probe_timer in changed:
            systemctl("restart", probe_timer)
        print(f"{'restarted' if probe_timer in changed else 'started'} {probe_timer}")
    if cfg.eval_schedule:
        systemctl("enable", "--now", eval_timer)
        if eval_timer in changed:
            systemctl("restart", eval_timer)
        print(f"{'restarted' if eval_timer in changed else 'started'} {eval_timer}")
    if args.dashboard:
        systemctl("enable", "--now", dash_unit)
        if dash_unit in changed:
            systemctl("restart", dash_unit)
        print(f"{'restarted' if dash_unit in changed else 'started'} {dash_unit}")
    if sh(["loginctl", "show-user", "--property=Linger", "--value", Path.home().name]).stdout.strip() != "yes":
        print("hint: `loginctl enable-linger` keeps user timers running after logout and at boot")
    enabled = [f"{cfg.unit}.timer"]
    if cfg.triage_service:
        enabled.append(probe_timer)
    if cfg.eval_schedule:
        enabled.append(eval_timer)
    if args.dashboard:
        enabled.append(dash_unit)
    print(f"stop with: systemctl --user disable --now {' '.join(enabled)}")
    return 0
