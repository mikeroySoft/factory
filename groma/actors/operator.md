---
type: C4 Actor
title: Operator
status: stable
groma:
  id: operator
---

The repository maintainer who runs the factory. Sets it up with `factory init`, `doctor`, and `install`; files **Agent task** issues on GitHub; and handles every case the pipeline hands back with `ready-for-human`. Uses the ops dashboard to watch tickets, read Factory Manager briefings, answer escalations with rationale, and adjust work limits. Can block any merge by requesting changes on a PR, and reviews and commits the lessons `factory learn` proposes.
