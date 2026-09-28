"""Exercise the accepted pure parser, not an unimplemented admission path.

Run: python experiments/handoff-replanning/probe.py
Uses only AST-selected parse/constants plus stdlib re from the frozen source.
No Factory import, GitHub calls, configuration reads, or production mutations.
"""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
source_bytes = (ROOT / "evidence/plan-b05c356.py").read_bytes()
identity = json.loads((ROOT / "evidence/plan-source.json").read_text())
assert hashlib.sha256(source_bytes).hexdigest() == identity["sha256"]
names = {"LINKS", "SECTION_CAP", "STATUSES", "SECTIONS", "HEADER", "LOGIN", "LINK"}
nodes = []
for node in ast.parse(source_bytes).body:
    if isinstance(node, ast.Import) and all(alias.name == "re" for alias in node.names):
        nodes.append(node)
    elif isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id in names for target in node.targets):
        nodes.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name == "parse":
        nodes.append(node)
namespace = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), identity["url"], "exec"), namespace)

plans = ["x" * namespace["SECTION_CAP"] + suffix for suffix in ("A", "B")]
parsed = []
for plan in plans:
    values = {name: "unchanged" for name in namespace["SECTIONS"]}
    values.update(Status="ready", Owner="mikeroySoft", Plan=plan)
    body = "\n\n".join(f"**{name}**\n{value}" for name, value in values.items())
    parsed.append(namespace["parse"](body))
result = {
    "source": identity,
    "input_plan_lengths": [len(plan) for plan in plans],
    "display_plan_lengths": [len(row["sections"]["Plan"]) for row in parsed],
    "complete_plan_digests_differ": hashlib.sha256(plans[0].encode()).digest() != hashlib.sha256(plans[1].encode()).digest(),
    "display_plan_digests_equal": hashlib.sha256(parsed[0]["sections"]["Plan"].encode()).digest() == hashlib.sha256(parsed[1]["sections"]["Plan"].encode()).digest(),
    "malformed": [row["malformed"] for row in parsed],
    "problems": [row["problems"] for row in parsed],
    "interpretation": "The reader flags truncation. A future binding consumer must respect that flag or obtain complete content. No existing #57 admission bug is demonstrated.",
}
assert result["complete_plan_digests_differ"] and result["display_plan_digests_equal"]
assert all(result["malformed"])
assert all("truncated section: Plan cut to 4000 characters" in problems for problems in result["problems"])
print(json.dumps(result, indent=2))
