"""Local pace/aggression mode state and effective ticket limits."""
from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from factory import config, lifecycle

MODE_FILE = "mode.json"
MODES = ("normal", "maintenance", "feature-drive")


@dataclass(frozen=True)
class Mode:
    name: str
    feature: int | None = None

    def __post_init__(self) -> None:
        if self.name not in MODES:
            raise ValueError(f"unknown mode: {self.name}")
        if self.name == "feature-drive":
            if type(self.feature) is not int or self.feature < 1:
                raise ValueError("feature-drive needs a positive feature number")
        elif self.feature is not None:
            raise ValueError(f"{self.name} mode cannot name a feature")

    def __str__(self) -> str:
        return f"{self.name} #{self.feature}" if self.feature is not None else self.name


def _path(cfg: config.Config) -> Path:
    return cfg.factory / MODE_FILE


def read(cfg: config.Config) -> Mode:
    """Read the current local mode; a missing file is normal mode."""
    try:
        value = json.loads(_path(cfg).read_text())
    except FileNotFoundError:
        return Mode("normal")
    except (OSError, ValueError, TypeError) as exc:
        raise config.ConfigError(f"{_path(cfg)}: invalid mode state") from exc
    if not isinstance(value, dict) or set(value) != {"mode", "feature"}:
        raise config.ConfigError(f"{_path(cfg)}: invalid mode state")
    try:
        return Mode(value["mode"], value["feature"])
    except (TypeError, ValueError) as exc:
        raise config.ConfigError(f"{_path(cfg)}: invalid mode state") from exc


def set_mode(
    cfg: config.Config,
    mode: Mode,
    *,
    who: str,
    expected: Mode | None = None,
) -> bool:
    """Atomically change mode and journal it; return False when unchanged or superseded."""
    path = _path(cfg)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (cfg.factory / "mode.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read(cfg)
        if expected is not None and current != expected:
            return False
        if current == mode:
            return False
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w") as output:
                json.dump({"mode": mode.name, "feature": mode.feature}, output, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        lifecycle.append(
            cfg.factory / "events.jsonl",
            {
                "event": "mode",
                "who": who,
                "mode": mode.name,
                "feature": mode.feature,
                "at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
        )
    return True


def descendants(feature: int, parents: dict[int, int]) -> set[int]:
    tree = {feature}
    while children := {child for child, parent in parents.items() if parent in tree} - tree:
        tree.update(children)
    return tree


def covers(mode: Mode, ticket: int, parents: dict[int, int] | None = None) -> bool:
    return mode.name == "feature-drive" and ticket in descendants(mode.feature, parents or {})


def capacity(cfg: config.Config, mode: Mode) -> int:
    pace = "low" if mode.name == "maintenance" else "normal"
    fallback = 1 if pace == "low" else cfg.max_active
    value = cfg.throttle_pace.get(pace, fallback)
    return min(value, cfg.manager_max_active_cap) if cfg.manager_max_active_cap is not None else value


def limits(cfg: config.Config, mode: Mode, ticket: int, parents: dict[int, int] | None = None) -> dict[str, int]:
    setting = "high" if covers(mode, ticket, parents) else "normal"
    fallback = (
        {"review_rounds": 5, "manager_rounds": 5, "max_attempts": 4, "budget_min": 150}
        if setting == "high"
        else {
            "review_rounds": cfg.review_rounds,
            "manager_rounds": cfg.manager_rounds,
            "max_attempts": cfg.max_attempts,
            "budget_min": cfg.budget_min,
        }
    )
    return dict(cfg.throttle_aggression.get(setting, fallback))


def auto_reset(
    cfg: config.Config,
    parents: dict[int, int],
    state: Callable[[int], str],
) -> bool:
    mode = read(cfg)
    if mode.name != "feature-drive":
        return False
    if any(state(number).upper() != "CLOSED" for number in descendants(mode.feature, parents)):
        return False
    return set_mode(cfg, Mode("normal"), who="auto", expected=mode)


def issue(cfg: config.Config, number: int) -> dict:
    result = subprocess.run(
        [
            "gh", "issue", "view", str(number), "--repo", cfg.repo,
            "--json", "state,labels,url",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def feature_error(cfg: config.Config, number: int, value: dict) -> str | None:
    url = value.get("url") or ""
    expected = f"https://github.com/{cfg.repo}/issues/{number}"
    if url.casefold() != expected.casefold():
        return f"#{number} belongs to another repository"
    if str(value.get("state", "")).upper() != "OPEN":
        return f"#{number} is closed"
    labels = {row.get("name") for row in value.get("labels") or []}
    if config.LABEL_AGENT not in labels and config.LABEL_INITIATIVE not in labels:
        return f"#{number} is not ready-for-agent or an initiative"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="show or set the local factory throttle mode")
    parser.add_argument("mode", nargs="?", choices=MODES)
    parser.add_argument("feature", nargs="?", type=int)
    args = parser.parse_args(argv)
    cfg = config.load()
    if args.mode is None:
        print(read(cfg))
        return 0
    if args.mode == "feature-drive" and args.feature is None:
        parser.error("feature-drive needs a feature number")
    if args.mode != "feature-drive" and args.feature is not None:
        parser.error(f"{args.mode} does not take a feature number")
    selected = Mode(args.mode, args.feature)
    if selected.feature is not None:
        try:
            value = issue(cfg, selected.feature)
        except (OSError, subprocess.CalledProcessError, ValueError) as exc:
            print(f"factory mode: cannot read #{selected.feature}: {exc}", file=sys.stderr)
            return 2
        if error := feature_error(cfg, selected.feature, value):
            print(f"factory mode: {error}", file=sys.stderr)
            return 2
    set_mode(cfg, selected, who=getpass.getuser())
    print(selected)
    return 0
