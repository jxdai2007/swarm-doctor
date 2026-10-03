# First-hour checks

These are real probes, not simulated passes. Missing keys do not block offline building.

- FAIL kimi_identity_call: operator action needed: KIMI_API_KEY missing. Fallback: synthetic development responses; cached-only replay; live runs blocked
- FAIL jev_decisions_call: operator action needed: OPENROUTER_API_KEY missing. Fallback: synthetic development responses; cached-only replay; live runs blocked
- PASS openrouter_models_fallback: mistralai/mistral-nemo / mistralai/mistral-nemo
- PASS omp_noninteractive_extension: exit=0; extension observed=True; builder Codex probe, not Kimi experiment
- PASS extension_local_http: 3 local callbacks observed
- PASS omp_result_file_paths: tool result includes path in input or content
- PASS omp_steer_redirection: steer delivered and corrected final output
