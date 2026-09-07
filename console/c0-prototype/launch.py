#!/usr/bin/env python3
"""Disposable Linux C0 launcher. No production installation or arbitrary Pi argv."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
PI = HERE / "node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
VERSION = "0.84.4"
MODEL = "ornith-ai/Ornith-1.5-35B-A3B-GGUF:Q4_K_M"
TOOLS = "fm_observe,fm_inspect,fm_investigate,fm_capabilities,fm_source,fm_resource,fm_sample_preview"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Explicit main Factory checkout; never inferred from conversation")
    parser.add_argument("--repository", required=True, help="Exact owner/name configured for that root")
    parser.add_argument("--continue", dest="resume", action="store_true", help="Resume this scope's latest Pi conversation; always reobserve")
    parser.add_argument("--provider", choices=("ornith", "openai", "openai-codex"), default="ornith",
                        help="Explicit inference provider; no fallback. OpenAI paths require --model and native isolated Pi authentication.")
    parser.add_argument("--model", help="Exact model ID; required for OpenAI providers. Ornith is pinned.")
    args = parser.parse_args()
    if args.provider != "ornith" and not args.model:
        parser.error("--model is required for --provider openai or openai-codex; no implicit model selection")
    if args.provider == "ornith" and args.model not in (None, MODEL):
        parser.error(f"Ornith is pinned to {MODEL}")
    model = args.model or MODEL
    if not model or model.strip() != model or any(char.isspace() or ord(char) < 32 for char in model):
        parser.error("--model must be an exact nonempty model ID without whitespace")
    provider = "c0-ornith" if args.provider == "ornith" else args.provider
    endpoint = {"ornith": "http://127.0.0.1:11435/v1", "openai": "https://api.openai.com/v1",
                "openai-codex": "https://chatgpt.com/backend-api"}[args.provider]
    if sys.platform != "linux" or not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error("Linux interactive terminal required; piped prompts and print/RPC modes are not supported")
    if not PI.is_file() or not shutil.which("node") or not shutil.which("gh"):
        parser.error("Requires Node >=22.19, authenticated gh, and npm ci --ignore-scripts --prefix console/c0-prototype")
    package = json.loads((PI.parents[2] / "package.json").read_text())
    if package["version"] != VERSION:
        parser.error(f"Requires pinned upstream Pi {VERSION}; no automatic download or upgrade")
    root = args.root.resolve(strict=True)
    session_scope = f"{root}\n{args.repository}"
    if args.provider != "ornith":
        session_scope += f"\n{provider}\n{model}"
    scope_key = hashlib.sha256(session_scope.encode()).hexdigest()[:16]
    runtime = HERE / ".runtime"
    agent = runtime / "agent"
    work = runtime / "work"
    sessions = runtime / "sessions" / scope_key
    os.umask(0o077)
    for directory in (agent, work, sessions):
        directory.mkdir(parents=True, exist_ok=True)
    # Only prototype-owned settings. Never copy provider auth or ambient settings.
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
        "models": [{"id": MODEL, "name": "Ornith · approved local C0", "reasoning": False,
                    "input": ["text"], "contextWindow": 131072, "maxTokens": 4096,
                    "samplingParams": {"chat_template_kwargs": {"enable_thinking": False}},
                    "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False, "supportsStore": False}}]
    }}}))
    # Keep only host facilities required by the terminal and gh's existing keyring.
    keep = {"HOME", "USER", "LOGNAME", "PATH", "TERM", "COLORTERM", "LANG", "LC_ALL", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK"}
    if args.provider == "openai":
        keep.add("OPENAI_API_KEY")  # Native environment auth only; never persisted or copied to another provider.
    env = {key: value for key, value in os.environ.items() if key in keep}
    env.update(PI_CODING_AGENT_DIR=str(agent), PI_OFFLINE="1", PI_TELEMETRY="0",
               PYTHONDONTWRITEBYTECODE="1", FM_C0_ROOT=str(root), FM_C0_REPOSITORY=args.repository,
               FM_C0_PYTHON=sys.executable, FM_C0_PROVIDER=provider, FM_C0_MODEL=model, FM_C0_ENDPOINT=endpoint)
    argv = [shutil.which("node"), str(PI), "--offline", "--no-approve", "--no-context-files",
            "--no-extensions", "-e", str(HERE / "extension.ts"), "--no-skills", "--no-prompt-templates", "--no-themes",
            "--tools", TOOLS, "--system-prompt", "Disposable C0 read-only Factory Manager. No mutation authority.",
            "--append-system-prompt", "", "--provider", provider, "--model", model,
            "--models", f"{provider}/{model}", "--thinking", "off", "--session-dir", str(sessions)]
    if args.resume:
        argv.append("--continue")
    print(f"C0 DISPOSABLE / READ ONLY — upstream Pi {VERSION}\nScope: {args.repository}\nRoot: {root}\nProvider: {provider}\nModel: {model}\nEndpoint: {endpoint}\nNative Pi auth remains isolated in {agent / 'auth.json'}; no tokens are copied.\nNo model request until the terminal disclosure dialog is explicitly approved.\n", flush=True)
    os.chdir(work)
    os.execve(argv[0], argv, env)


if __name__ == "__main__":
    main()
