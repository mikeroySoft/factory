---
type: C4 Component
title: Triage
status: stable
groma:
  id: triage
  parent: factory-cli
  code:
    - scanner: python
      file: factory/triage.py
  group: Ticket pipeline
description: Labels new issues with the local model
---

Lists `needs-triage` issues and decides one of `ready-for-agent` (with an agent brief), `needs-info` (with a question), `ready-for-human`, or a `wontfix` proposal. A deterministic lint first rejects bodies under 80 characters or without acceptance criteria. The model decision is parsed as JSON with one retry; `wontfix` is only ever proposed as a comment, never applied. Its chat-completion call is also reused by Learn.
