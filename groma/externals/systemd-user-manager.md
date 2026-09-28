---
type: C4 System
title: systemd user manager
status: stable
groma:
  id: systemd-user-manager
  technology: systemd user units, journald
---

Hosts the factory on the operator machine. `factory install` writes a oneshot service and timer that run `factory triage` then `factory dispatch` every interval (default 10 min), plus an optional long-running dashboard service. The dashboard reads timer state and the service journal to show dispatcher runs.
