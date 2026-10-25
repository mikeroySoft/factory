# Prime Agent pilot

Opt-in worker setup for `mikeroySoft/rocm-cli`. No other repository or default worker changes.

## Install

```sh
./experiments/prime-agent-pilot/install.sh
```

The installer downloads the GitHub `v0.10.0` Linux x64 release, verifies the archive SHA-256
`c16bd2af5e77b53f49b914a44742c4cf6a67c5ed5000041430b78dbd4c3bcbec` and executable SHA-256
`546e71921bb0fa278c9d50dd8c61a27b835413552cb08a9359d0e320848e39d6`, then publishes the isolated
`~/.local/bin/factory-prime-agent` launcher. It never executes a downloaded installer.

Merge `host-config.toml` into `~/.config/factory/config.toml` only after the Factory build containing
this profile schema is installed. `factory doctor` must then resolve `factory-prime-agent`.

The `prime` profile is selected only when a ticket has `harness-pilot`. Prime runs in print/JSON mode;
Factory adds its per-attempt `--session-dir`, preserving the full compatible transcript for usage,
scoring, and learning. The pinned `version` is written to claim, usage, attempt, and worker lifecycle
entry evidence. `idle_timeout`, `exit_grace`, and process-group cleanup remain Factory-owned.

`DO_NOT_TRACK=1` and `PRIME_AGENT_TELEMETRY=0` are applied only to this worker. Agent, session,
daemon, `/refine`, memory, and skill state resolve beneath `<rocm-cli>/.factory/prime-agent`; Factory
never applies that ignored state to tracked files. Repository lessons still enter through the existing
reviewed `factory learn` CURATE PR path.

Before adding `harness-pilot`, put the approved Prime provider/model configuration in that contained
agent directory, run one Prime-side dry run, and verify:

```sh
PRIME_AGENT_CODING_AGENT_DIR=/path/to/rocm-cli/.factory/prime-agent \
PRIME_AGENT_SESSION_DIR=/path/to/rocm-cli/.factory/prime-agent/sessions \
PRIME_AGENT_DAEMON_SOCKET=/path/to/rocm-cli/.factory/prime-agent/daemon.sock \
DO_NOT_TRACK=1 PRIME_AGENT_TELEMETRY=0 \
factory-prime-agent status --json
```

The required clean result is `[]`. The dry run must also produce a retained transcript, reported
worker tokens, a parsed JSON review, no Prime analytics egress, and no tracked refinement changes.
Rollback removes `harness-pilot` and the `[repo."mikeroySoft/rocm-cli".workers.prime]` table; the
isolated binary and ignored state may remain for inspection.
