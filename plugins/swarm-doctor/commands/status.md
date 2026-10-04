---
description: Show Swarm Doctor status and event report without changing protection.
disable-model-invocation: true
allowed-tools: Bash
---

Run these two commands separately in the current project root, then summarize their actual output:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" status
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py" report
```

This is read-only administration: do not lock, release, clear state, create a decoy, modify project files, or restart workers. If no lock exists, say so. Distinguish quarantined actors from watching actors: watching records a read of a path touched by a quarantined writer, not proven infection. Keep watching active work; shell inspection is heuristic, not a sandbox. Never suggest disabling hooks or bypassing permissions to clear a denial.

References: [plugin commands](https://code.claude.com/docs/en/plugins/components#commands), [hooks](https://code.claude.com/docs/en/hooks).
