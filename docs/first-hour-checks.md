# First-hour checks

These are real probes, not simulated passes. Missing keys do not block offline building.

- PASS kimi_identity_call: served model: kimi-for-coding
- PASS jev_decisions_call: served model: typesafe/jev-1.13-20260917
- PASS openrouter_models_fallback: mistralai/mistral-nemo / mistralai/mistral-nemo
- FAIL omp_noninteractive_extension: TimeoutExpired. Fallback: experiment harness; deny-reason steer; unknown shell paths
- FAIL extension_local_http: TimeoutExpired. Fallback: experiment harness; deny-reason steer; unknown shell paths
- FAIL omp_result_file_paths: TimeoutExpired. Fallback: experiment harness; deny-reason steer; unknown shell paths
- FAIL omp_steer_redirection: TimeoutExpired. Fallback: experiment harness; deny-reason steer; unknown shell paths
