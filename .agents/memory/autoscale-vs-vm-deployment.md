---
name: Autoscale vs VM deployment for background bots
description: How to diagnose a 24/7 background process (trading bot, Telegram poller, etc.) that mysteriously dies in production — check deployment type mismatch first.
---

A process that must run continuously (Telegram long-polling, a price-monitoring loop, any
"background worker" started from a web artifact's production run script) cannot survive on an
**autoscale** deployment. Autoscale is Cloud Run-style: idle instances get scaled to zero, which
kills every child process, wipes the ephemeral filesystem (including `.venv`), and only boots a
fresh instance again when a new HTTP request arrives. A background loop with no HTTP traffic
driving it will not be revived on its own.

**Why:** discovered when a trading bot (Flask + watchdog subprocess, launched from
`scripts/start_production.sh` alongside a Node API server) went completely silent in production —
no stdout, no crash logs, nothing — for indefinite periods. Deployment logs showed a repeating
pattern every ~1-2 hours: `.venv missing — running uv sync` followed by health-check failures, i.e.
periodic cold starts. That cadence is the signature of autoscale idle-recycling, not a code bug.

**How to apply:** when a background/always-on process shows symptoms like "randomly stops
responding," "state looks stale," "works after redeploy then dies again," or logs repeatedly show
dependency reinstallation / `.venv missing` on a schedule:
1. Call `getDeploymentInfo()` and check `deploymentType`.
2. Check the relevant artifact's `.replit-artifact/artifact.toml` for its `deploymentTarget` field
   — it can silently diverge from what's actually live (e.g. artifact.toml said `"vm"` while the
   live deployment was still `"autoscale"`, because changing artifact.toml does not retroactively
   change an already-published deployment's infra type).
3. The deployment type (autoscale/vm/scheduled/static) can only be changed by the **user** in the
   Deployments pane — it is not editable via `.replit` (direct edits are blocked) or any agent
   tool. Explain the mismatch and tell the user to switch to Reserved VM (`vm`) themselves, then
   republish.
