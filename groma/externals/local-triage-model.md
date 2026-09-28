---
type: C4 System
title: Local triage model
status: stable
groma:
  id: local-triage-model
  technology: OpenAI-compatible chat completions (Ollama, vLLM, llama.cpp, LM Studio)
---

A locally hosted model endpoint configured in `[triage]` (default `qwen3:30b` on Ollama). Triage asks it to label new issues; `factory learn` asks it to distil lessons. `factory doctor` and the dashboard probe its `/models` route for health.
