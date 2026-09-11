"""`factory chat`: the supported read-only Factory Manager console.

A thin, installed Python launcher over the pinned upstream Pi runtime and the
reviewed read-only console extension in `<root>/console/app`. It carries no
mutation authority: the model gets the seven bounded evidence tools, no shell,
no edit/write, and inference is gated behind an explicit provider-disclosure
dialog. Node/Pi is an explicitly installed optional console dependency, never
required by ordinary Factory execution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

VERSION = "0.84.4"
MODEL = "ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M"
TOOLS = "fm_observe,fm_inspect,fm_investigate,fm_capabilities,fm_source,fm_resource,fm_sample_preview"
# Only host facilities the terminal and gh's existing keyring need; never provider secrets.
KEEP_ENV = {"HOME", "USER", "LOGNAME", "PATH", "TERM", "COLORTERM", "LANG", "LC_ALL",
            "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK"}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory chat", description=__doc__)
    p.add_argument("--root", type=Path, required=True,
                   help="Explicit Factory checkout with an installed console/app; never inferred from conversation")
    p.add_argument("--repository", required=True, help="Exact owner/name configured for that root")
    p.add_argument("--continue", dest="resume", action="store_true",
                   help="Resume this scope's latest conversation; startup always reobserves fresh evidence")
    p.add_argument("--provider", choices=("ornith", "openai", "openai-codex"), default="ornith",
                   help="Explicit inference provider; no fallback. OpenAI paths require --model and native isolated Pi authentication.")
    p.add_argument("--model", help="Exact model ID; required for OpenAI providers. Ornith is pinned.")
    return p


def prepare(args: argparse.Namespace, fail) -> tuple[list[str], dict, Path]:
    """Validate the environment and build the isolated Pi argv/env.

    Writes only console-owned settings/models into an isolated runtime dir and
    returns the launch argv, the scrubbed environment and the working dir.
    `fail(message)` aborts (argparse's `error`, i.e. exit 2).
    """
    if args.provider != "ornith" and not args.model:
        fail("--model is required for --provider openai or openai-codex; no implicit model selection")
    if args.provider == "ornith" and args.model not in (None, MODEL):
        fail(f"Ornith is pinned to {MODEL}")
    model = args.model or MODEL
    if not model or model.strip() != model or any(char.isspace() or ord(char) < 32 for char in model):
        fail("--model must be an exact nonempty model ID without whitespace")
    provider = "c0-ornith" if args.provider == "ornith" else args.provider
    endpoint = {"ornith": "http://127.0.0.1:11435/v1", "openai": "https://api.openai.com/v1",
                "openai-codex": "https://chatgpt.com/backend-api"}[args.provider]
    if sys.platform != "linux" or not sys.stdin.isatty() or not sys.stdout.isatty():
        fail("Linux interactive terminal required; piped prompts and print/RPC modes are not supported")
    root = args.root.resolve(strict=True)
    console = root / "console" / "app"
    pi = console / "node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
    extension = console / "extension.ts"
    if not pi.is_file() or not extension.is_file() or not shutil.which("node") or not shutil.which("gh"):
        fail("Requires Node >=22.19, authenticated gh, and `npm ci --ignore-scripts --prefix console/app` in the checkout")
    package = json.loads((pi.parents[2] / "package.json").read_text())
    if package.get("version") != VERSION:
        fail(f"Requires pinned upstream Pi {VERSION}; no automatic download or upgrade")

    session_scope = f"{root}\n{args.repository}"
    if args.provider != "ornith":
        session_scope += f"\n{provider}\n{model}"
    scope_key = hashlib.sha256(session_scope.encode()).hexdigest()[:16]
    runtime = console / ".runtime"
    agent = runtime / "agent"
    work = runtime / "work"
    sessions = runtime / "sessions" / scope_key
    os.umask(0o077)
    for directory in (agent, work, sessions):
        directory.mkdir(parents=True, exist_ok=True)
    # Only console-owned settings. Never copy provider auth or ambient host settings.
    settings = {
        "defaultProjectTrust": "never", "enableInstallTelemetry": False,
        "enableAnalytics": False, "quietStartup": True, "lastChangelogVersion": VERSION,
        "hideThinkingBlock": True,
        "compaction": {"enabled": False}, "retry": {"enabled": False, "provider": {"maxRetries": 0, "timeoutMs": 120000}},
        "images": {"blockImages": True}, "packages": [], "extensions": [],
        "skills": [], "prompts": [], "themes": [], "doubleEscapeAction": "none",
    }
    (agent / "settings.json").write_text(json.dumps(settings))
    (agent / "models.json").write_text(json.dumps({"providers": {"c0-ornith": {
        "baseUrl": "http://127.0.0.1:11435/v1", "api": "openai-completions",
        "apiKey": "local-no-auth", "authHeader": False,
        "models": [{"id": MODEL, "name": "Ornith · approved local console", "reasoning": False,
                    "input": ["text"], "contextWindow": 131072, "maxTokens": 4096,
                    "samplingParams": {"chat_template_kwargs": {"enable_thinking": False}},
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False, "supportsStore": False}}]
    }}}))
    keep = set(KEEP_ENV)
    if args.provider == "openai":
        keep.add("OPENAI_API_KEY")  # Native environment auth only; never persisted or copied to another provider.
    env = {key: value for key, value in os.environ.items() if key in keep}
    env.update(PI_CODING_AGENT_DIR=str(agent), PI_OFFLINE="1", PI_TELEMETRY="0",
               PYTHONDONTWRITEBYTECODE="1", FM_C0_ROOT=str(root), FM_C0_REPOSITORY=args.repository,
               FM_C0_PYTHON=sys.executable, FM_C0_PROVIDER=provider, FM_C0_MODEL=model, FM_C0_ENDPOINT=endpoint)
    argv = [shutil.which("node"), str(pi), "--offline", "--no-approve", "--no-context-files",
            "--no-extensions", "-e", str(extension), "--no-skills", "--no-prompt-templates", "--no-themes",
            "--tools", TOOLS, "--system-prompt", "Read-only Factory Manager console. No mutation authority.",
            "--append-system-prompt", "", "--provider", provider, "--model", model,
            "--models", f"{provider}/{model}", "--thinking", "off", "--session-dir", str(sessions)]
    if args.resume:
        argv.append("--continue")
    return argv, env, work


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    launch_argv, env, work = prepare(args, p.error)
    print(f"Factory Manager — READ ONLY console (upstream Pi {VERSION})\n"
          f"Scope: {env['FM_C0_REPOSITORY']}\nRoot: {env['FM_C0_ROOT']}\n"
          f"Provider: {env['FM_C0_PROVIDER']}\nModel: {env['FM_C0_MODEL']}\nEndpoint: {env['FM_C0_ENDPOINT']}\n"
          f"Native Pi auth stays isolated in {Path(env['PI_CODING_AGENT_DIR']) / 'auth.json'}; no tokens are copied.\n"
          "No model request until the terminal disclosure dialog is explicitly approved.\n", flush=True)
    os.chdir(work)
    os.execve(launch_argv[0], launch_argv, env)



if __name__ == "__main__":
    raise SystemExit(main())
