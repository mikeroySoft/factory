#!/usr/bin/env python3
"""Freeze one reviewer-calibration case packet from a committed head.

Mirrors the layout of cases/12-before: sources/{diff.patch,issue.md,README.md,
gate-report.md}, packet.md envelope, oracle.json with provenance hashes.

usage:
  freeze_case.py --case-id 62-before --issue 62 --base SHA --head SHA \\
      --verdict REVISE --oracle oracle-draft.json [--skip-gate]
"""
from __future__ import annotations

import argparse, hashlib, json, pathlib, subprocess, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parents[3]  # factory checkout
CASES = pathlib.Path(__file__).resolve().parents[1] / "cases"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode())


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def git_show(rev: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{rev}:{path}"], cwd=REPO, check=True, capture_output=True
    ).stdout


def git_diff(base: str, head: str) -> str:
    return run(["git", "diff", "--find-renames", f"{base}..{head}"], cwd=REPO).stdout


def head_blobs(base: str, head: str) -> dict[str, str]:
    # paths changed between base and head → blob at head
    out = run(["git", "diff", "--name-only", f"{base}..{head}"], cwd=REPO).stdout.splitlines()
    blobs = {}
    for path in out:
        try:
            blobs[path] = run(["git", "rev-parse", f"{head}:{path}"], cwd=REPO).stdout.strip()
        except subprocess.CalledProcessError:
            # deleted at head
            continue
    return blobs


def fetch_issue(number: int) -> str:
    raw = run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--repo",
            "mikeroySoft/factory",
            "--json",
            "title,body,comments",
            "--jq",
            "{title,body,comments:[.comments[]|{author:.author.login,created:.createdAt,body}]}",
        ]
    ).stdout
    data = json.loads(raw)
    parts = [f"# Issue #{number}: {data['title']}", "", data["body"].rstrip(), ""]
    for c in data.get("comments") or []:
        # Keep triage / owner comments; skip huge bot dumps
        body = (c.get("body") or "").strip()
        if len(body) > 12000:
            body = body[:12000] + "\n\n… [truncated for packet] …"
        parts.append(f"## Comment by @{c['author']} ({c['created']})")
        parts.append("")
        parts.append(body)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def run_gate(base: str, head: str) -> str:
    """Real factory gate at head with origin/main → base, in a shared throwaway clone."""
    with tempfile.TemporaryDirectory(prefix="cal-gate-") as td:
        dest = pathlib.Path(td) / "wt"
        run(["git", "clone", "--quiet", "--shared", str(REPO), str(dest)])
        run(["git", "checkout", "--quiet", "--detach", head], cwd=dest)
        run(["git", "update-ref", "refs/remotes/origin/main", base], cwd=dest)
        toml = dest / ".factory.toml"
        text = toml.read_text() if toml.exists() else ""
        if "[repo]" not in text:
            text = "[repo]\nslug = \"mikeroySoft/factory\"\n" + text
        elif "slug" not in text:
            text = text.replace("[repo]", "[repo]\nslug = \"mikeroySoft/factory\"", 1)
        toml.write_text(text)
        # Prefer the checkout's own factory module via uv/python -m
        report = dest / "gate-report.md"
        # Use the live factory CLI if present; else python -m from REPO on PYTHONPATH
        # Run the factory module from the detached head itself — never the tip checkout —
        # so historical tests bind to the code under review.
        env = {**__import__("os").environ, "PYTHONPATH": str(dest)}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "factory", "gate", "--report", str(report)],
                cwd=dest,
                capture_output=True,
                text=True,
                timeout=900,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise SystemExit(f"gate timed out at {head}: {e}") from e
        if not report.exists():
            raise SystemExit(
                f"gate produced no report (rc={proc.returncode})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
        body = report.read_text()
        required = ("conflict-markers: PASS", "test: PASS", "leak-scan: PASS")
        if not all(line in body for line in required):
            raise SystemExit(
                f"gate did not fully PASS at {head}:\n{body[:2000]}\nstderr:\n{proc.stderr[-2000:]}"
            )
        return body if body.startswith("#") else "# Gate report\n\n" + body


def build_packet(case_id: str, issue: int, base: str, head: str, issue_md: str, diff: str, readme: str) -> str:
    """Match the existing 12-before envelope prose (no leading indent)."""
    header = (
        f"## Frozen review packet — case {case_id}\n\n"
        "This packet supplies, verbatim, the evidence the reviewer would otherwise read from the\n"
        "repository and GitHub. The reviewer process for this experiment has no tools, so the diff,\n"
        "the issue text and the repository README are inlined below. Cite `path:line` from the diff.\n\n"
        f"- repository: mikeroySoft/factory\n"
        f"- issue: #{issue}\n"
        f"- review base (origin/main): {base}\n"
        f"- head under review: {head}\n"
        "- documented conventions present at this head: README.md (inlined below). No AGENTS.md or\n"
        "  CONTRIBUTING.md exists at this commit. docs/manager-plan.md (if present) is NOT inlined and is\n"
        "  outside this packet; do not assume its contents.\n\n"
        "### Issue\n\n"
    )
    diff_body = diff if diff.endswith("\n") else diff + "\n"
    return (
        header
        + issue_md.rstrip()
        + "\n\n### git diff origin/main..HEAD\n\n```diff\n"
        + diff_body
        + "```\n\n### README.md at this head (documented conventions)\n\n```markdown\n"
        + readme.rstrip()
        + "\n```\n"
    )



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--issue", type=int, required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--verdict", required=True, choices=["APPROVE", "REVISE"])
    ap.add_argument("--oracle", required=True, help="JSON file with findings / unnecessary_demand_rule / adjudication")
    ap.add_argument("--skip-gate", action="store_true")
    ap.add_argument("--gate-text", default="", help="Use this gate report instead of running gate")
    args = ap.parse_args()

    draft = json.loads(pathlib.Path(args.oracle).read_text())
    issue_md = fetch_issue(args.issue)
    diff = git_diff(args.base, args.head)
    readme = git_show(args.head, "README.md").decode()
    if args.gate_text:
        gate = pathlib.Path(args.gate_text).read_text()
    elif args.skip_gate:
        gate = "# Gate report\n\n- conflict-markers: PASS\n- test: PASS\n- leak-scan: PASS\n"
    else:
        gate = run_gate(args.base, args.head)
        if not gate.endswith("\n"):
            gate += "\n"

    case_dir = CASES / args.case_id
    src = case_dir / "sources"
    src.mkdir(parents=True, exist_ok=True)
    files = {
        "sources/issue.md": issue_md if issue_md.endswith("\n") else issue_md + "\n",
        "sources/diff.patch": diff if diff.endswith("\n") else diff + "\n",
        "sources/README.md": readme if readme.endswith("\n") else readme + "\n",
        "sources/gate-report.md": gate if gate.endswith("\n") else gate + "\n",
    }
    for rel, content in files.items():
        (case_dir / rel).write_text(content)

    packet = build_packet(args.case_id, args.issue, args.base, args.head, issue_md, diff, readme)
    (case_dir / "packet.md").write_text(packet)

    provenance = []
    for rel, content in files.items():
        raw = content.encode()
        provenance.append({"path": rel, "sha256": sha256_bytes(raw), "bytes": len(raw)})

    oracle = {
        "case_id": args.case_id,
        "issue": args.issue,
        "review_base": args.base,
        "head": args.head,
        "head_blobs": head_blobs(args.base, args.head),
        "issue_url": f"https://github.com/mikeroySoft/factory/issues/{args.issue}",
        "gate_evidence": draft.get(
            "gate_evidence",
            "real `factory gate` run at this head in a throwaway --shared clone with refs/remotes/origin/main set to review_base; only a [repo].slug line was prepended to .factory.toml for slug resolution, committed gate checks unmodified"
            if not args.skip_gate
            else "HISTORICAL gate PASS recorded in dispatcher events; live re-gate deferred — replace before scoring claims",
        ),
        "expected_verdict": args.verdict,
        "findings": draft.get("findings", []),
        "unnecessary_demand_rule": draft["unnecessary_demand_rule"],
        "adjudication": draft.get(
            "adjudication",
            "independent read of the frozen source by the calibration owner; historical model verdicts were used only to discover candidates, never as truth",
        ),
        "gate_report": gate.strip(),
        "provenance": provenance,
        "packet_sha256": sha256_text(packet),
    }
    if draft.get("needs_human_adjudication"):
        oracle["needs_human_adjudication"] = draft["needs_human_adjudication"]
    (case_dir / "oracle.json").write_text(json.dumps(oracle, indent=2) + "\n")
    print(f"froze {args.case_id}: verdict={args.verdict} packet_sha={oracle['packet_sha256'][:12]} files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
