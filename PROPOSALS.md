# Proposals (NOT applied — require human approval)

## P1. `tests/test_runtime.py` lines 147-149, 152-153, 160, 282-283 encode Linux-only facts

**Why not applied:** the task forbids editing these lines; `.factory.toml`
`[gate].add_only_paths = ["tests/**"]` also makes any in-place change an
`add-only` gate failure.

**Premise conflict:** each line asserts a fact that only a passive kernel lock
table can prove (`/proc/locks`). On Darwin there is no non-spawning source
(`platform_identity.py:4-7`, `lock_holders` returns `None`). The approved
contract says unknown is not free and unavailable is not zero. Making these
lines pass on Darwin would require doing exactly that:

| Line | Asserts | Darwin truth |
|---|---|---|
| 147-149 | `gpu.lock` ownership `confirmed`, owner = acquisition | holders unknown → `ownership: unknown`, `owner: null` |
| 152-153 | capacity `{"configured":2,"active":1,"complete":True}` | `locks_unavailable` → `{"active": null, "complete": false}` |
| 160 | released lock ownership `none` | passive never proves free → `unknown` |
| 282-283 | resource `observation: fresh`, owner present | `unknown` state → `observation: unavailable`, owner `null` |

Baseline evidence: `logs/baseline-full-suite.log` (see PROGRESS.md for the
exact failure lines).

**Exact minimal diff (platform-branch the expectation; Linux assertions unchanged):**

```diff
--- a/tests/test_runtime.py
+++ b/tests/test_runtime.py
@@ -146,10 +146,19 @@
         resources = {row["resource"]["lock"]["path"]: row for row in first["resources"]}
-        self.assertEqual(resources[str(self.root / "gpu.lock")]["ownership"], "confirmed")
-        self.assertEqual(resources[str(self.root / "gpu.lock")]["owner"]["acquisition_id"],
-                         acquisition["acquisition_id"])
+        gpu = resources[str(self.root / "gpu.lock")]
+        if platform_identity.darwin():
+            self.assertEqual((gpu["state"], gpu["ownership"], gpu["owner"]), ("unknown", "unknown", None))
+            self.assertEqual(gpu["evidence"]["attribution"], "unavailable")
+        else:
+            self.assertEqual(gpu["ownership"], "confirmed")
+            self.assertEqual(gpu["owner"]["acquisition_id"], acquisition["acquisition_id"])
         self.assertEqual(resources[str(self.root / "external.lock")]["ownership"], "unknown")
         self.assertIsNone(resources[str(self.root / "external.lock")]["owner"])
-        self.assertEqual(first["dispatcher"]["capacity"],
-                         {"configured": 2, "active": 1, "complete": True})
+        self.assertEqual(first["dispatcher"]["capacity"],
+                         {"configured": 2, "active": None, "complete": False} if platform_identity.darwin()
+                         else {"configured": 2, "active": 1, "complete": True})
@@ -159,3 +168,3 @@
-        self.assertEqual(released["ownership"], "none")
+        self.assertEqual(released["ownership"], "unknown" if platform_identity.darwin() else "none")
         self.assertIsNone(released["owner"])
@@ -281,3 +290,7 @@
-        self.assertEqual(resource["observation"], "fresh")
-        self.assertEqual(resource["owner"]["acquisition_id"], acquisition["acquisition_id"])
+        if platform_identity.darwin():
+            self.assertEqual((resource["observation"], resource["owner"]), ("unavailable", None))
+        else:
+            self.assertEqual(resource["observation"], "fresh")
+            self.assertEqual(resource["owner"]["acquisition_id"], acquisition["acquisition_id"])
```
(plus `platform_identity` added to the file's `from factory import ...` line.)

**Sanctioned mechanism:** `factory-add-only-override` label (README.md:357-358,
736-740, 1056-1066), granted by a human or via manager `AUTHORIZE` with
justification "Linux-only kernel-attribution assertions; Darwin contract
requires unknown/unavailable". Not `factory-protected-override` — no protected
path is involved. The normal gate and independent review then run again.

**Rejected alternative:** making the Darwin runtime report `confirmed`/`none`/
`active: 1` from journal acquisition events alone. That is passive evidence
standing in for kernel proof — prohibited.

## P1 correction (observed this run)

On this host the four P1 tests fail *earlier* than the P1 table implies: the test builds
expected keys from `tempfile` paths under `/var/folders/...`, while the runtime projects
canonical paths (`/private/var/folders/...`). Result: `KeyError` at line 147 and
`StopIteration` at lines 280 and 407. With a canonical `TMPDIR` (diagnostic only), line 407
passes and only the P1 premise lines fail (`'unknown' != 'confirmed'` at 147,
`'unavailable' != 'fresh'` at 282). So P1 needs P0 as well.

## P0. Test temp roots are the `/var` symlink, not the canonical path (not applied)

Not part of the degraded-observability contract; it is a macOS test-environment artifact.
Affects `tests/test_runtime.py` (147, 280, 407) and about 35 `tests/test_evidence.py` cases
(`'/private/var/...' != '/var/...'`). Production canonicalizes correctly.

Minimal diff (`tests/test_runtime.py:62`, one modified line; `test_evidence.py` needs the same
`.resolve()` on its temp root):
```diff
-        self.root = Path(self.temp.name)
+        self.root = Path(self.temp.name).resolve()
```
Mechanism: `factory-add-only-override` (modifies existing `tests/**` lines). Not applying it
also leaves the forbidden-line ban intact; line 62 is not one of the forbidden lines, but the
add-only gate still applies.

## P2. `tests/test_dashboard_passive.py:167` and `:169` (not applied)

Same premise as P1: they assert `lock_held is True` and `gpu_lock_held is False` from passive
evidence. Darwin cannot prove either passively, so it reports `None`. Proposed:
```diff
-            self.assertIs(tickets[7]["lock_held"], True)
+            self.assertIs(tickets[7]["lock_held"], None if platform_identity.darwin() else True)
             self.assertIsNone(tickets[8]["lock_held"])
-            self.assertIs(snapshot["gpu_lock_held"], False)
+            self.assertIs(snapshot["gpu_lock_held"], None if platform_identity.darwin() else False)
```
(+ `from factory import platform_identity`). Mechanism: `factory-add-only-override`.

## P3. `tests/test_lifecycle_consumers.py:175-177` (not applied)

Asserts exactly one `held` resource from passive evidence; on Darwin, held is not provable
passively. Proposed:
```diff
-            self.assertEqual(len(held), 1)
+            self.assertEqual(len(held), 0 if platform_identity.darwin() else 1)
-            self.assertEqual(held[0]["ownership"], "unknown")
-            self.assertIsNone(held[0]["owner"])
+            for row in held:
+                self.assertEqual((row["ownership"], row["owner"]), ("unknown", None))
```
Mechanism: `factory-add-only-override`.
