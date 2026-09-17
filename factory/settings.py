"""Safe repository settings snapshot and optimistic TOML persistence."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import stat
import tempfile
import threading
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from factory import config
from factory.config import Config

_SAVE_LOCK = threading.RLock()  # ponytail: one lock serializes local dashboard saves; split per repo if needed
_MAX_MODEL = 200
_MAX_INTEGER = 2**53 - 1

_FIELD_TABLES = {
    "dispatch.max_active": "dispatch",
    "dispatch.budget_min": "dispatch",
    "dispatch.max_attempts": "dispatch",
    "dispatch.review_rounds": "dispatch",
    "manager.model": "manager",
}
_INT_RULES = {
    "dispatch.max_active": (1, None),
    "dispatch.budget_min": (1, None),
    "dispatch.max_attempts": (1, None),
    "dispatch.review_rounds": (0, None),
}
_APPLIES = "Next dispatcher invocation; running tickets keep their captured configuration"
_MANAGER_APPLIES = "Next new Factory Manager request; in-flight requests keep their captured model"
_PERSISTENCE = "Local .factory.toml; uncommitted until the repository owner commits it"


class SettingsError(ValueError):
    """A safe, user-facing settings failure without configuration contents."""

    def __init__(self, message: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or {}


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, tomllib.TOMLDecodeError):
        return "Invalid TOML configuration"
    if isinstance(exc, config.ConfigError):
        return "Repository configuration could not be loaded safely"
    if isinstance(exc, SettingsError):
        return str(exc)[:300] or "Settings request failed"
    if isinstance(exc, (OSError, ValueError, TypeError, UnicodeError)):
        return "Settings configuration could not be loaded safely"
    return "Settings request failed"


def _failure(exc: BaseException, errors: dict[str, str] | None = None) -> dict:
    result = {"ok": False, "error": _safe_error(exc)}
    if errors:
        result["errors"] = errors
    return result


def _lstat_target(path: Path) -> os.stat_result | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError(f"Cannot inspect {path.name}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise SettingsError(f"{path.name} is a symlink; refusing to replace it")
    if not stat.S_ISREG(info.st_mode):
        raise SettingsError(f"{path.name} is not a regular file")
    return info


def _read_target(path: Path) -> bytes | None:
    if _lstat_target(path) is None:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            return stream.read()
    except OSError as exc:
        raise SettingsError(f"Cannot read {path.name}") from exc


def _host_layers(host: dict, repo: str) -> tuple[dict, dict]:
    defaults = host.get("defaults", {}) if isinstance(host, dict) else {}
    repositories = host.get("repo", {}) if isinstance(host, dict) else {}
    repo_layer = repositories.get(repo, {}) if isinstance(repositories, dict) else {}
    return config.host_filter(defaults), config.host_filter(repo_layer)


def _present(table: object, key: str) -> bool:
    return isinstance(table, dict) and key in table


def _source(
    table: str,
    key: str,
    raw_repo: dict,
    host_defaults: dict,
    host_repo: dict,
    *,
    host_allowed: bool = True,
) -> str:
    if _present(raw_repo.get(table), key):
        return "Repository"
    if host_allowed and _present(host_repo.get(table), key):
        return "Host repository override"
    if host_allowed and _present(host_defaults.get(table), key):
        return "Host default"
    return "Built-in default"


def _command_model(command: object) -> str | None:
    if isinstance(command, str):
        try:
            command = shlex.split(command)
        except ValueError:
            return None
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command):
        return None
    model = None
    for index, arg in enumerate(command):
        if arg == "--model" and index + 1 < len(command):
            model = command[index + 1]
        elif arg.startswith("--model="):
            model = arg.split("=", 1)[1]
    return model


def _manager_source(raw_repo: dict, host_defaults: dict, host_repo: dict) -> str:
    layers = (
        ("Repository", raw_repo.get("manager")),
        ("Host repository override", host_repo.get("manager")),
        ("Host default", host_defaults.get("manager")),
    )
    # An explicit model wins over every legacy command in the merged table.
    for label, table in layers:
        if isinstance(table, dict) and table.get("model") is not None:
            return label
    # A higher layer's command replaces lower commands even when it has no
    # model flag, so only inspect the winning command.
    for label, table in layers:
        if isinstance(table, dict) and "command" in table:
            return (
                f"{label} (legacy manager.command --model fallback)"
                if _command_model(table["command"]) is not None
                else "Built-in default (OMP default)"
            )
    return "Built-in default (OMP default)"


def _safe_model(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_MODEL:
        return None
    if value.startswith("-") or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    return value


def _argv_summary(argv: object) -> tuple[str, str | None]:
    if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
        return "configured program", None
    name = Path(argv[0]).name
    if not name or len(name) > 80 or any(ord(c) < 32 or c in "\r\n" for c in name):
        name = "configured program"
    model = None
    for index, arg in enumerate(argv):
        if not isinstance(arg, str):
            continue
        if arg == "--model" and index + 1 < len(argv):
            model = _safe_model(argv[index + 1])
        elif arg.startswith("--model="):
            model = _safe_model(arg.split("=", 1)[1])
    return name, model


def _origin(url: object) -> tuple[str, str]:
    if not isinstance(url, str):
        return "unavailable", "Endpoint omitted because its configured URL is invalid"
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            not parsed.scheme
            or not host
            or any(ord(char) < 32 or char in "\r\n" for char in host)
        ):
            raise ValueError
        port = parsed.port
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        origin = f"{parsed.scheme.lower()}://{host}{f':{port}' if port is not None else ''}"
    except (ValueError, UnicodeError):
        return "unavailable", "Endpoint omitted because its configured URL is invalid"
    return origin, "Origin only; path, query, fragment and credentials omitted for safety"


def _revision(repo: str, repo_bytes: bytes | None, host: dict, host_bytes: bytes | None) -> str:
    defaults, repository = _host_layers(host, repo)
    relevant = {"defaults": defaults, "repo": repository}
    digest = hashlib.sha256()
    for label, value in (
        ("repo", repo),
        ("repo-bytes", repo_bytes if repo_bytes is not None else b"<missing>"),
        ("host-bytes", host_bytes if host_bytes is not None else b"<missing>"),
        ("host-relevant", json.dumps(relevant, sort_keys=True, separators=(",", ":"), default=str)),
    ):
        digest.update(label.encode())
        digest.update(b"\0")
        digest.update(value if isinstance(value, bytes) else value.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_effective(cfg: Config, raw_repo: dict) -> None:
    table = raw_repo.get("dispatch", {})
    for key, (minimum, maximum) in _INT_RULES.items():
        field = key.split(".", 1)[1]
        if _present(table, field) and type(table[field]) is not int:
            raise SettingsError(f"{key}: configured value must be an integer")
        value = getattr(cfg, field)
        if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
            raise SettingsError(f"{key}: configured value is outside its allowed range")
        if value > _MAX_INTEGER:
            raise SettingsError(f"{key}: configured value exceeds the safe integer limit")



def _state(root: Path) -> tuple[Config, dict, dict, bytes | None, bytes | None, str]:
    repository = config.repo_root(root)
    path = repository / config.CONFIG_NAME
    repo_bytes = _read_target(path)
    cfg = config.load(repository)
    raw_repo = cfg.raw_repo
    host_path = config.host_config_path()
    try:
        host_bytes = host_path.read_bytes() if host_path.exists() else None
        host = config.host_config()
    except OSError as exc:
        raise SettingsError("Cannot read host configuration") from exc
    _validate_effective(cfg, raw_repo)
    return cfg, raw_repo, host, repo_bytes, host_bytes, _revision(cfg.repo, repo_bytes, host, host_bytes)


def _snapshot(root: Path) -> dict:
    cfg, raw_repo, host, _, _, revision = _state(root)
    host_defaults, host_repo = _host_layers(host, cfg.repo)
    fields = {}
    for dotted, table in _FIELD_TABLES.items():
        key = dotted.split(".", 1)[1]
        value = cfg.manager_model if dotted == "manager.model" else getattr(cfg, key)
        fields[dotted] = {
            "value": value,
            "source": _manager_source(raw_repo, host_defaults, host_repo)
            if dotted == "manager.model"
            else _source(table, key, raw_repo, host_defaults, host_repo, host_allowed=False),
            "applies": _MANAGER_APPLIES if dotted == "manager.model" else _APPLIES,
        }

    workers = []
    effective_workers = raw_repo.get("workers", {}) if isinstance(raw_repo.get("workers"), dict) else {}
    for label, argv in cfg.workers.items():
        program, model = _argv_summary(argv)
        if label in effective_workers:
            worker_source = "Repository"
        elif label in (host_repo.get("workers", {}) if isinstance(host_repo.get("workers"), dict) else {}):
            worker_source = "Host repository override"
        elif label in (host_defaults.get("workers", {}) if isinstance(host_defaults.get("workers"), dict) else {}):
            worker_source = "Host default"
        else:
            worker_source = "Built-in default"
        workers.append({"label": str(label), "program": program, "model": model, "source": worker_source})
    reviewer_program, reviewer_model = _argv_summary(cfg.reviewer)
    reviewer_source = _source("review", "command", raw_repo, host_defaults, host_repo)
    triage_endpoint, endpoint_note = _origin(cfg.llm_url)
    triage_source = (
        f"endpoint: {_source('triage', 'url', raw_repo, host_defaults, host_repo)}; "
        f"model: {_source('triage', 'model', raw_repo, host_defaults, host_repo)}; {endpoint_note}"
    )
    return {
        "ok": True,
        "repo": cfg.repo,
        "revision": revision,
        "fields": fields,
        "agents": {
            "workers": workers,
            "reviewer": {"program": reviewer_program, "model": reviewer_model, "source": reviewer_source},
            "triage": {"endpoint": triage_endpoint, "model": _safe_model(cfg.llm_model), "source": triage_source},
        },
        "persistence": _PERSISTENCE,
    }


def snapshot(root: Path) -> dict:
    try:
        return _snapshot(Path(root))
    except (
        config.ConfigError, OSError, ValueError, TypeError, LookupError, AttributeError, ArithmeticError,
    ) as exc:
        return _failure(exc)


def _validate_request(request: object) -> tuple[str, dict[str, object]]:
    if not isinstance(request, dict):
        raise SettingsError("request must be a JSON object")
    unknown = set(request) - {"revision", "changes"}
    if unknown:
        raise SettingsError("unknown request fields: " + ", ".join(sorted(str(key) for key in unknown)))
    revision = request.get("revision")
    if not isinstance(revision, str) or not revision or len(revision) > 128:
        raise SettingsError("revision must be a nonempty string")
    raw_changes = request.get("changes")
    if not isinstance(raw_changes, dict):
        raise SettingsError("changes must be an object")
    changes = dict(raw_changes)
    errors: dict[str, str] = {}
    for key, value in changes.items():
        if key not in _FIELD_TABLES:
            errors[str(key)] = "unknown setting"
            continue
        if key in _INT_RULES:
            minimum, maximum = _INT_RULES[key]
            if type(value) is not int:
                errors[key] = "integer required"
            elif value < minimum:
                errors[key] = f"must be at least {minimum}"
            elif maximum is not None and value > maximum:
                errors[key] = f"must be at most {maximum}"
            elif value > _MAX_INTEGER:
                errors[key] = "exceeds the safe integer limit"
        elif value is not None:
            if not isinstance(value, str):
                errors[key] = "a model selector or null is required"
            else:
                value = value.strip()
                if not value or len(value) > _MAX_MODEL:
                    errors[key] = "a nonempty model selector of at most 200 characters is required"
                elif value.startswith("-") or any(c.isspace() or ord(c) < 32 for c in value):
                    errors[key] = "model selector must not contain whitespace or control characters"
                else:
                    changes[key] = value
    if errors:
        raise SettingsError("Invalid settings values", errors)
    return revision, changes


def _document(text: str):
    import tomlkit

    try:
        return tomlkit.parse(text) if text else tomlkit.document()
    except Exception as exc:
        raise SettingsError("Invalid TOML configuration; no changes were written") from exc


def _section(document, name: str):
    import tomlkit

    section = document.get(name)
    if section is None:
        section = tomlkit.table()
        document[name] = section
    if not hasattr(section, "__setitem__") or not hasattr(section, "get"):
        raise SettingsError(f"[{name}] must be a TOML table; no changes were written")
    return section


def _apply(document, changes: dict[str, object]) -> str:
    import tomlkit

    for dotted, value in changes.items():
        table_name, key = dotted.split(".", 1)
        if dotted == "manager.model" and value is None:
            section = document.get(table_name)
            if section is not None:
                if not hasattr(section, "remove"):
                    raise SettingsError(f"[{table_name}] must be a TOML table; no changes were written")
                if key in section:
                    section.remove(key)
            continue
        section = _section(document, table_name)
        section[key] = value
    return tomlkit.dumps(document)


def _write_atomic(path: Path, content: bytes, expected: bytes | None) -> None:
    current = _read_target(path)
    if current != expected:
        raise SettingsError("Configuration changed while saving; reload settings and retry")
    info = _lstat_target(path)
    mode = stat.S_IMODE(info.st_mode) if info else 0o644
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Refuse a symlink or an external edit discovered before replacement.
        if _read_target(path) != expected:
            raise SettingsError("Configuration changed while saving; reload settings and retry")
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def save(root: Path, request: dict) -> dict:
    try:
        revision, changes = _validate_request(request)
    except SettingsError as exc:
        return _failure(exc, exc.errors)
    with _SAVE_LOCK:
        try:
            cfg, _, _, repo_bytes, _, current_revision = _state(Path(root))
            if revision != current_revision:
                return _failure(SettingsError("Settings are stale; reload before saving"))
            path = cfg.root / config.CONFIG_NAME
            original = repo_bytes or b""
            document = _document(original.decode("utf-8"))
            rendered = _apply(document, changes)
            content = rendered.encode("utf-8")
            if changes and (content != original or repo_bytes is None):
                _write_atomic(path, content, repo_bytes)
            return _snapshot(cfg.root)
        except (
            config.ConfigError, OSError, ValueError, TypeError, LookupError, AttributeError, ArithmeticError,
        ) as exc:
            return _failure(exc)
