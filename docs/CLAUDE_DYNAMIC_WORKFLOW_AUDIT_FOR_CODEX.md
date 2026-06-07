# Adversarial Audit Report — ai-litellm-fabric (commit 753eee6)

## 1. Executive summary

This report consolidates an 8-scope adversarial audit of the `ai-litellm-fabric` repo at commit `753eee6`. Across 8 worker scopes (packaging, native-boundary, optional-harness, token-context-reasoning, proxy-runtime, secrets, ci-docs, architecture), **41 raw findings** were produced and adversarially re-verified. After dedup, **22 distinct actionable items** remain plus a strong "verified clean" baseline. One sub-claim was rejected as a false positive (and re-issued as a *real* finding in the opposite direction).

Headline results:

- **The native boundary is intact and proven.** A temp-HOME install creates only `~/.local/share/...` + 7 shims; `~/.claude`, `~/.codex`, `~/.config/*`, and `~/litellm_config.yaml` are never written; no native CLI is symlinked/aliased/replaced; all native-state access is read-only diagnostics. No secret literals are committed. This is the load-bearing safety guarantee and it holds.
- **The most serious *functional* break is PKG-01 (high):** all 7 installed shims are non-functional when the install prefix or `$HOME` contains a space, caused by `print` vs `print -r` undoing zsh `${(q)}` quoting in `install.zsh:116`.
- **Two security-relevant hardenings stand out:** SEC6-01 (master key visible in `curl` argv via `ps`, medium) and SEC6-07 (provider env file parsed with `source`, so embedded `$(...)`/`;` commands execute — a falsified "no command injection" invariant, low because same-user-owned).
- **Two operational/lifecycle highs:** S5-01 (`ai-litellm sync` unconditionally restarts the shared live proxy, no dry-run, killing in-flight sessions) and S5-02 (pid-file liveness is identity-blind — a recycled/foreign PID gets adopted and **`stop` SIGTERMs a non-litellm process**, proven by hard transcript).
- **A CI verification gap (high):** `verify_litellm_token_clamp.py` is only `py_compile`d, never executed (and CI never installs `litellm`), so the documented token-clamp matrix is unverified; the gateway ships **no output-reservation clamp** (S4-04 / SCOPE8-02), relying entirely on harness self-reporting.
- A recurring **stale `.gitignore` comment** (claims state lives under `~/.config`; it actually lives under `prefix/state`) was independently flagged by 5 scopes — deduped to a single low item.

No data-loss-of-user-data and no native-boundary violations were found. Confidence is high across nearly all items; two are explicitly `medium` (S5-03, S5-06) and one was demoted to `hypothesis` (SEC6-02, mechanism real but the exploitable race is unobservable).

## 2. Workflow method used

This audit used **Claude Code Dynamic Workflows / subagents** in a fan-out → verify → reduce topology:

1. **Fan-out (8 workers).** Eight independent auditor subagents were dispatched, one per scope: `packaging`, `native-boundary`, `optional-harness`, `token-context-reasoning`, `proxy-runtime`, `secrets`, `ci-docs`, `architecture`. Each produced candidate findings with file:line evidence and proposed reproductions.
2. **Per-worker adversarial verification.** Each worker's findings were then re-run through an adversarial verifier that re-read every cited `file:line`, re-executed every claimed reproduction in a **fresh `mktemp` temp-HOME** (never the real `$HOME`), and graded each finding `{verified | rejected}`, demoting unprovable claims to `hypothesis`. Security/native-boundary/data-loss claims were held to a higher bar (hard transcript required).
3. **Reduce (this document).** The reducer deduplicated cross-scope findings (notably the `.gitignore` comment, the `.bak` orphan chain, and the gateway-clamp gap), segregated speculation, ranked by risk × implementation value, and classified into must-fix / should-fix / nice-to-have / architecture-ideas / rejected.

All mutating operations ran under temp HOMEs that were deleted afterward; the real `$HOME`, `~/.claude`, `~/.codex`, and the live `:4000` proxy were inspected read-only only.

## 3. Commands/tests run

- `./scripts/check.zsh` => **ok** (clean, repeatedly, including after temp-HOME installs).
- `git diff --check` => **clean** (no whitespace/conflict errors); repo verified untouched at commit `753eee6`, clean tree.
- `git rev-parse --short HEAD` => `753eee6`; `git log -1` subject confirmed: *"Handle legacy proxy pid migration"*.
- **Temp-HOME installs** (representative): `tmp_home=$(mktemp -d); HOME=$tmp_home scripts/install.zsh` — exit 0; layout = `prefix` + 7 shims; `~/.claude`/`~/.codex`/`~/.config/*`/`~/litellm_config.yaml` confirmed absent.
- **Spaced-HOME / spaced-prefix repro** (PKG-01): `mktemp -d '/tmp/ai test XXXXXX'` install → shim line 2 contains a literal space → `exec` fails.
- **Foreign-process adoption/kill** (S5-02): a live `sleep 600` PID written into the legacy pid file → `ai_litellm_stop` printed `LiteLLM stopped (pid N)` and SIGTERM'd the foreign process.
- **`source`-injection** (SEC6-07): env-file value `sk-or-test$(touch $tmp/PWNED)END` → `$tmp/PWNED` created on key resolution.
- **`ps` argv leak** (SEC6-01): backgrounded `curl -H "Authorization: Bearer sk-...-PROOF"` → `ps -ww` shows the bearer token.
- **`harness_validate` false-invalid** (S3-01 / SCOPE8-03): with `goose`/`codex` off PATH, `ai_litellm_harness_validate <h>` → rc=1 "Harness command not available".
- **Reservation budgets** (S4-01/02): `ai_litellm_harness_output_budget` for opus/sonnet/haiku → reservation `32000`, effectiveInput `1008384 / 221952 / 162560` (the sonnet→Kimi `262144/262144` worst case yields `effectiveInput=221952`, not 0).
- **CI inspection**: `grep -nE 'litellm|pip|python' .github/workflows/ci.yml` => no matches (only `brew install jq ripgrep` then `./scripts/check.zsh`).

## 4. Findings table

| id | title | severity | scope | confidence |
|---|---|---|---|---|
| PKG-01 | Shim non-functional when prefix/HOME contains spaces (`print` drops `${(q)}` quoting) | high | packaging/install | high |
| S5-01 | `ai-litellm sync` unconditionally restarts the live shared proxy; no `--dry-run`/`--no-restart` | high | proxy-lifecycle | high |
| S5-02 | pid-file liveness is identity-blind; `stop` SIGTERMs a foreign/recycled PID | high | proxy-lifecycle | high |
| S7-01 | CI/check.zsh never executes `verify_litellm_token_clamp.py` (only `py_compile`) | high | CI / token-clamp | high |
| S4-04 / SCOPE8-02 | Gateway ships no output-reservation clamp; relies entirely on harness self-report | medium | token-budget enforcement | high |
| SEC6-01 | Master key visible in `curl -H 'Authorization: Bearer'` argv via `ps` | medium | secrets | high |
| S3-01 | `harness info`/`proxy doctor` report a valid descriptor "invalid"/"fail" only because CLI absent | medium | optional-harness | high |
| S3-02 | `context doctor` HARD FAILs + leaks `command not found: codex` when codex absent | medium | optional-harness | high |
| PKG-02 | Reinstall accumulates unbounded `.bak.<stamp>` copies (27/run) | medium | packaging/install | high |
| PKG-03 / S7-02 | Uninstall orphans `*-litellm.bak.<stamp>` shims in `~/.local/bin` | medium | packaging/uninstall | high |
| S5-03 | Foreign LiteLLM on :4000 silently adopted when local hash file matches | medium | proxy-lifecycle | medium |
| S5-04 | Lock staleness is PID-liveness-only; `started_at` is write-only dead state | medium | proxy-lifecycle | high |
| S7-03 / SCOPE8-03 | `schema.json` never enforced; only `jq empty` + hand-rolled JS validator | medium | CI / schema | high |
| S7-05 | `check.zsh` doesn't assert placeholder rendering, shim correctness, or reservation numbers | medium | CI coverage | high |
| SEC6-07 | Provider env file parsed with `source`; embedded shell executes (self-injection) | low | secrets | high |
| PKG-04 | Uninstall without `--prefix` silently orphans a custom-prefix install | low | packaging/uninstall | high |
| SEC6-03 | Generated `opencode.json` is 644 (vs 600 for codex); placeholder only today | low | secrets | high |
| SEC6-04 | `prefix/state` dirs created 755; only `AI_LITELLM_HOME` hardened to 700 at start | low | secrets | high |
| SEC6-05 | `uninstall.zsh rm -rf "$prefix"` trusts `AI_LITELLM_FABRIC_HOME`/`--prefix` with no sanity guard | low | secrets | high |
| S4-08 | `schema.json` doesn't require new claude `autoCompactWindowEnv`/`maxOutputTokensEnv`/`outputReservation` | low | descriptor validation | high |
| S4-06 | Reservation "selection" arg inconsistent (launcher=tier, env_assignments=model); 3 budget impls | low | drift / internal API | high |
| S4-05 | Codex is the only reservation-policy-less harness; unguarded on shared-window backends | low | drift | medium |
| S5-06 | `stop` deletes new-location hash sidecar even when proxy resolved via legacy pid file | low | proxy-lifecycle | medium |
| S7-07 | README "First Run" omits that `sync` restarts shared proxy and smoke tests are billable | low | docs/code agreement | medium |
| S7-06 | CI single-OS (macos-latest), no toolchain pins; node/ruby/python3 assumed present | low | CI robustness | medium |
| GITIGNORE-STALE (PKG-06/S4-07/S5-05/S7-04/SCOPE8-08) | `.gitignore` comment claims state under `~/.config`; actually `prefix/state` | low | docs consistency | high |
| S3-03 | `context probe` calls bare `codex` (not descriptor command), unguarded → `command not found` | low | optional-harness | high |
| SEC6-02 | Multi-provider secrets via `env VAR=secret` transiently in env(1) argv | hypothesis | secrets | hypothesis |
| S3-04 | `sync` gates codex *config* render (CLI-free) behind `command -v codex` (asymmetry) | idea | optional-harness | high |
| ARCH-* (8 items) | Architecture improvement ideas (see Architecture ideas section) | idea | architecture | high |
| PKG-05 / S3-05/06 / SEC6-06 / S7-08 / scope2-* | Verified-clean baselines (native boundary, footprint, reasoning, posture) | idea | multiple | high |

## 5. Detailed findings

### MUST-FIX

---

#### PKG-01 — Shim is non-functional when prefix or HOME contains spaces
- **severity:** high | **scope:** packaging/install | **confidence:** high
- **evidence:** `scripts/install.zsh:116` `print "export AI_LITELLM_FABRIC_HOME=${(q)prefix}"` (no `-r`). Under `HOME='/tmp/ai test XUlQBf'`, generated shim line 2 = `export AI_LITELLM_FABRIC_HOME=/tmp/ai test XUlQBf/.local/share/ai-litellm-fabric` — literal space, NO backslash, NO quotes. `exec` fails: `ai-litellm:export:2: not valid in this context: XUlQBf/...`. With `--prefix '<tmp>/My Fabric Pkg'`, FABRIC_HOME truncates at first space → `ai-litellm:3: no such file or directory: <tmp>/My/bin/ai-litellm`. Mechanism independently confirmed: `print` without `-r` processes escape sequences, undoing the backslash-quoting `${(q)}` added. `check.zsh:34` uses `mktemp -d` (no spaces) and only `test -x`'s the shim (line 41) — never execs it — so it **cannot** catch this. All 7 shims are broken.
- **root cause:** Line 116 uses `print` instead of `print -r`; `print` interprets escapes and undoes `${(q)}`.
- **expected:** Shim sets `AI_LITELLM_FABRIC_HOME` to the exact prefix, quoted, working for any path including spaces (the install banner already shell-quotes paths, so spaces are an expected case).
- **recommended action:** Change line 116 to `print -r -- "export AI_LITELLM_FABRIC_HOME=${(qq)prefix}"` (`qq` = single-quote form that survives literally) or at minimum `print -r -- "...${(q)prefix}"`.
- **files:** `scripts/install.zsh`
- **tests:** `check.zsh`: temp-HOME install into a prefix **with a space**, then assert `"$bin/ai-litellm" version` exits 0.

---

#### S5-01 — `ai-litellm sync` unconditionally restarts the live proxy; no dry-run
- **severity:** high | **scope:** proxy-lifecycle/sync | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:1921` `ai_litellm_sync()`; `:1941` echoes `- proxy restart`; `:1942` `ai_litellm_restart || failed=1`. Dispatch `:3935` `sync|--sync) ai_litellm_sync ;;` passes **no args**, and `ai_litellm_sync()` has no arg parsing. `grep -n 'dry' config/ai-litellm/lib.zsh` => zero hits. `ai_litellm_restart` (`:1483`) = `stop` → `start`. The `--dry-run` convention already exists in `install.zsh:12,36` and `uninstall.zsh:13,41`.
- **root cause:** `sync` conflates "regenerate derived artifacts" (cheap, reversible) with "reload the proxy" (destructive) into one non-optional sequence.
- **expected:** A read-only `--dry-run`/`--no-restart` that regenerates derived configs into temp/diff form and reports what *would* change without bouncing the shared proxy (which breaks in-flight `claude-litellm`/`codex-litellm` sessions).
- **recommended action:** Add `--dry-run` (print plan + diff, skip render + skip restart) and `--no-restart` (regenerate only) to `ai_litellm_sync`, parsing args in the `sync` case at `:3935`. Keep default behavior. Consider splitting "regen" from "reload".
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME: stub `ai_litellm_restart` to set a flag; assert `sync --dry-run` prints plan, does NOT call restart, exits 0; `check.zsh` assert sync help mentions `--dry-run`.

---

#### S5-02 — pid-file liveness check verifies existence, not identity; `stop` kills foreign PIDs
- **severity:** high | **scope:** proxy-lifecycle/pid resolution | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:1206-1214` `ai_litellm_pid_from_file` uses only `kill -0 "$pid"`. `:1216-1224` `ai_litellm_active_pid_file` scans `AI_LITELLM_PID_FILE` then two legacy `~/.config/.../litellm.pid` paths. `:1476` `ai_litellm_stop` `kill "$pid"` of whatever resolves. **HARD TRANSCRIPT** (temp HOME): a `sleep 600` PID written into the legacy pid file → `active_pid_file` returned the legacy path, `pid_running` rc=0, then `stop` printed `LiteLLM stopped (pid 41259)` and the foreign process went alive=YES → NO-KILLED. SIGTERM delivered to a non-litellm process. Latent on the real machine today (live pid lives only in installed state), but the legacy pid file is exactly the migration case this code path targets.
- **root cause:** Liveness ≠ identity. `kill -0` proves alive, not that it's the proxy; PID reuse is common on macOS.
- **recommended action:** After `kill -0`, confirm identity (e.g. `ps -o command= -p $pid 2>/dev/null | grep -q litellm`) before returning/killing; otherwise treat the pid file as stale and ignore/clean it. Apply consistently across status/stop/start.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME: stale pid file → a non-litellm live PID must NOT be reported running and `stop` must NOT kill it; legacy pid file with recycled PID ignored.

---

#### S7-01 — CI/check.zsh never executes `verify_litellm_token_clamp.py`
- **severity:** high | **scope:** CI / token-clamp verify | **confidence:** high
- **evidence:** `scripts/check.zsh:17` `python3 -m py_compile .../verify_litellm_token_clamp.py` is the **only** reference (syntax check). `verify_litellm_token_clamp.py:424` `def main()`; `:428` `--litellm-bin` default `os.environ['LITELLM_BIN'] or shutil.which('litellm')`; `:435-437` returns exit code 2 ("litellm executable not found") when absent; it spawns a live proxy (`run_litellm_proxy:299`) + mock provider (`run_mock_provider:439`). `.github/workflows/ci.yml` has one macos-latest job: `brew install jq ripgrep` then `./scripts/check.zsh`; `grep` for `litellm|pip|python` in ci.yml => **nothing**. So even if invoked, `main()` would exit 2.
- **nuance (corrected):** The script does NOT itself exit non-zero on contract divergence today — it observes and (with `--json`) reports. Running it in CI would also require adding a contract-assertion layer. Its own `recommended_action` already acknowledges this. Core claim (never executed in CI; documented clamp matrix unverified) stands.
- **recommended action:** Add a separate CI job that `pip install litellm==<pin>` then runs `./scripts/verify_litellm_token_clamp.py --json` and asserts the documented matrix via jq/exit code; add a self-asserting contract mode that exits non-zero on divergence.
- **files:** `.github/workflows/ci.yml`, `scripts/verify_litellm_token_clamp.py`, `scripts/check.zsh`
- **tests:** CI job `pip install litellm==<pin>` + `verify_litellm_token_clamp.py --json` asserting the matrix; local `LITELLM_BIN=$(which litellm) ./scripts/verify_litellm_token_clamp.py`.

---

### SHOULD-FIX

---

#### S4-04 / SCOPE8-02 — Gateway ships no output-reservation clamp; relies on harness self-report
- **severity:** medium | **scope:** token-budget enforcement | **confidence:** high
- **evidence:** `config/litellm_config.yaml` has `litellm_settings:` (`:89`) with `enable_pre_call_checks: true` (`:96`) but **no `modify_params` and no `callbacks`** (`rg 'modify_params|callbacks|custom_callbacks|async_pre_call' config/ bin/` matches only `enable_pre_call_checks` + two ruby reads at `lib.zsh:2982/3492`). `enable_pre_call_checks` enforces the **input** window only. `verify_litellm_token_clamp.py:506-555` — `main()` returns `0 if modify_params_enforced or hook_enforced else 1`; `plain_config_enforced` is NOT part of the success condition. The working `OutputClamp(CustomLogger)` exists only as a temp-dir string in the verifier (`:146-218`). Doc `docs/AI_AGENT_LITELLM_ARCHITECTURE.md:274,286-292,530,532` confirms the hook is deferred and not enabled in production. Output reservation is enforced **only** harness-side (`config/claude-litellm/shell.zsh:214-221`; `goose.json:52-53`; `opencode.json:63`). A raw client/future harness sending a large `max_tokens` to Kimi (262144/262144 shared window) is NOT clamped by the gateway.
- **recommended action:** Decide and document the policy in-repo: either enable `litellm_settings.modify_params: true` (clamps `max_tokens` to `model_info.max_output_tokens`) or ship the `async_pre_call_deployment_hook` callback file and reference it from config. At minimum add a doctor **warning** when a shared-window provider model (`max_input_tokens == max_output_tokens`) is routed AND no gateway clamp is configured. Stage behind the verifier (S7-01).
- **files:** `config/litellm_config.yaml`, `config/ai-litellm/lib.zsh` (new doctor check), `scripts/check.zsh` or `ci.yml`
- **tests:** `verify_litellm_token_clamp.py --json` against installed litellm; new doctor warn-on-shared-window-without-gateway-clamp.

---

#### SEC6-01 — Master key visible in `curl` argv via `ps`
- **severity:** medium | **scope:** secrets | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:1241` (health), `:1269` (model_names), `:1562` (route_info), `:1605` (probe_route, `--max-time 90`), `:2508` (`ai_litellm_model_reasoning_probe`, `--max-time 90`) all use `curl ... -H "Authorization: Bearer $master_key"`. **HARD REPRO:** `ps -ww -o args=` on a backgrounded curl prints `Bearer sk-master-PROOF-...` — readable by any local user for the request lifetime (up to 90s on the two probe sites).
- **correction:** Line 2508 lives in `ai_litellm_model_reasoning_probe()`, not the context doctor (the doctor at `:3756` calls the probe). Does not change the finding.
- **recommended action:** Pass the auth header off-argv: `printf 'header = "Authorization: Bearer %s"\n' "$master_key" | curl -K - ...`, or `--header @file` with a 600-mode temp file. Factor into a single `ai_litellm_curl_auth` helper applied to all 5 sites.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** `check.zsh` assert `rg -n 'Authorization: Bearer \$' config/ai-litellm/lib.zsh` returns nothing; manual ps argv check.

---

#### S3-01 — `harness info` / `proxy doctor` report a valid descriptor "invalid"/"fail" purely because the CLI is absent
- **severity:** medium | **scope:** optional-harness | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:466-469` `ai_litellm_harness_validate` ends with `command -v "$command" ... || { echo "Harness command not available..."; return 1; }`; `:697` `harness_info` prints Status `ok|invalid` from it; `:1744-1745` `ai_litellm_doctor_harnesses` gates `harness descriptor valid` on it. Temp-HOME + clean PATH: `harness info codex` → `Status: invalid`; symlink `codex` back → flips to `ok`. `proxy doctor` with all four CLIs absent → 4× `fail harness descriptor valid: <h>` and doctor-exit=1.
- **recommended action:** Split `ai_litellm_harness_validate` into descriptor validity (schema + cross-field, no command check) and a separate `ai_litellm_harness_cli_available`. `harness_info`: show descriptor Status + a `CLI: installed|not installed` line. `doctor_harnesses`: keep `harness descriptor valid` structural (ok when CLI missing) + emit `warn harness CLI not installed`. **CAVEAT (verified):** `ai_litellm_launch` (`lib.zsh:900-908`) gets its ONLY CLI precondition from `harness_validate`; if `command -v` is removed from validate, `ai_litellm_launch` must gain its own explicit CLI guard to preserve hard-fail-on-missing-CLI.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME with CLIs stripped: `proxy doctor` must not emit `fail harness descriptor valid`; `harness info` Status stays `ok`; `ai_litellm launch <h>` still hard-fails when CLI missing.

---

#### S3-02 — `context doctor` HARD FAILs (exit 1) and leaks `command not found: codex` when codex absent
- **severity:** medium | **scope:** optional-harness | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:3482-3486` `ai_litellm_context_codex_matches_bundled` runs bare `codex debug models [--bundled]`; the trailing `2>/dev/null` redirects only the pipeline's stderr inside `$(...)`, not zsh's own `command not found: codex`; empty substitution yields `|| return 1`. `:3761` wires it as a hard check (`|| failed=1`). Temp-HOME, codex off PATH: `context doctor` printed two `command not found: codex` lines + `fail native Codex active gpt-5.5 catalog matches bundled catalog`, ctx-doctor-exit=1. (The Ruby context **matrix** already handles codex absence gracefully via Open3 rescue — only the doctor is inconsistent.)
- **recommended action:** Top of `ai_litellm_context_codex_matches_bundled`: `command -v codex >/dev/null 2>&1 || return 0` (skip-with-note), or resolve via the descriptor command and capture the whole command group's stderr. Mirror the matrix's Open3 resilience.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME, codex removed: `context doctor` exits 0 (or only warns), no `command not found` text.

---

#### PKG-02 — Reinstall accumulates unbounded `.bak.<stamp>` copies (27/run)
- **severity:** medium | **scope:** packaging/install | **confidence:** high
- **evidence:** `install.zsh:74-81` `backup_if_exists` moves any existing target to `<t>.bak.<stamp>`, called from `install_rendered(87)`, `install_executable(101)`, `install_shim(110)` — every file. Three installs >1s apart → `find ... -name '*.bak.*' | wc -l` = 54 (= 27 owned files × 2 reinstalls). Same-second double install → 27 `.bak` under one stamp (2nd `mv` silently overwrites the 1st backup; `stamp = date +%Y%m%d-%H%M%S`, 1s resolution).
- **recommended action:** (a) back up only when the existing target differs (`cmp`), (b) skip backups for installer-owned files reserving backups for genuinely foreign files, or (c) add a sub-second/PID suffix and garbage-collect old backups. At minimum document that reinstall leaves `.bak` files.
- **files:** `scripts/install.zsh`
- **tests:** `check.zsh`: install twice into same temp HOME, assert no `*.bak.*` under prefix (or bounded count).

---

#### PKG-03 / S7-02 — Uninstall orphans `*-litellm.bak.<stamp>` shims in `~/.local/bin`
- **severity:** medium | **scope:** packaging/uninstall | **confidence:** high
- **evidence:** `uninstall.zsh:78-82` only `rm -f "$bin_dir/$script"` for the 7 exact shim names plus `rm -rf "$prefix"`; never removes `$bin_dir/<name>.bak.*`. **HARD REPRO:** install ×2 then uninstall → `find "$tmp_home/.local/bin" -mindepth 1` lists 7 orphaned `*.bak.<stamp>` executable shims, each `exec`'ing the now-deleted `$prefix/bin/<name>` — i.e. broken commands left on PATH. (The `.bak` files *inside* prefix are removed by `rm -rf prefix`.)
- **recommended action:** In `uninstall.zsh` also `rm -f "$bin_dir/$script".bak.*(N)` per managed script; better, fix PKG-02 so these backups are never created.
- **files:** `scripts/uninstall.zsh`, `scripts/install.zsh`, `scripts/check.zsh`
- **tests:** `check.zsh`: install twice + uninstall in temp HOME, assert no `*-litellm*` and no `*.bak.*` left in `$bin_dir`.

---

#### S5-03 — Foreign LiteLLM on :4000 silently adopted when local hash file matches
- **severity:** medium | **scope:** proxy-lifecycle/start collision | **confidence:** medium
- **evidence:** `config/ai-litellm/lib.zsh:1236-1245` `ai_litellm_health` hits `/health/readiness` with no proof the listener is THIS fabric's proxy. `:1335-1343` start: if `health` then if `! proxy_config_current` refuse else `echo 'already reachable'; return 0`. `proxy_config_current` (`:1258-1264`) compares local config sha to the locally-recorded hash file — never queries the running proxy. `ai_litellm_proxy_registry_matches_file` (`:1273`, queries `/model/info`) is called ONLY in doctor (`:2113`), never in start. Verified reachable precondition: after `record_proxy_config_state` with unchanged config, `proxy_config_current` rc=0. **Kept at `medium`:** no second proxy was bound to :4000 (side-effect limits); the branch path and precondition are proven, the final adoption step is sound inference.
- **recommended action:** In `ai_litellm_start`, when health passes, additionally require `ai_litellm_proxy_registry_matches_file` before declaring "already reachable"; otherwise warn that a foreign/mismatched proxy holds the port and refuse.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** mock `health=true` + `proxy_model_names` returning a different set; assert start refuses.

---

#### S5-04 — Lock staleness is PID-liveness-only; `started_at` is write-only dead state
- **severity:** medium | **scope:** proxy-lifecycle/lock | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:1278-1284` `ai_litellm_lock_stale` returns "not stale" whenever the lock pid is alive (`kill -0`), with NO age consideration. `grep -nE 'started_at|STARTED_AT'` => only def (`:16`), write (`:1255`, `:1298`), delete (`:1288`, `:1478`) — **ZERO reads**. **REPRO:** lock dir with pid = a live unrelated `sleep 600` and `started_at='2020-01-01T00:00:00Z'` → `lock_stale` rc=1 (not stale); recorded age never consulted. A recycled lock PID pins the lock until that process exits → concurrent starts time out at ~5s with "Timed out waiting for LiteLLM start lock".
- **recommended action:** (a) consume `started_at` in `lock_stale` with a max-age fallback combined with `!health` to break wedged/recycled-PID locks, or (b) drop the `started_at` writes if unused. Also bump/document the ~5s acquire timeout for slow boots.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME: lock dir with a live unrelated PID + proxy not healthy + old `started_at` → `lock_stale` eventually true after age-fallback added.

---

#### S7-03 / SCOPE8-03 — `schema.json` constraints never enforced; only `jq empty` + hand-rolled JS validator
- **severity:** medium | **scope:** CI / descriptor schema | **confidence:** high
- **evidence:** `scripts/check.zsh:19-25` loops `harnesses/*.json` and runs only `jq empty` (well-formedness). `config/ai-litellm/harnesses/schema.json` (draft 2020-12) declares `schemaVersion const:1`, `adapter.enum` `[claude-code, codex-cli, env-injector, opencode-cli]`, `paths.additionalProperties`, `isolation.required [kind,env]`. No JSON-Schema validator lib in repo (`rg -ni 'ajv|jsonschema'` => none). `lib.zsh:376-462` is a bespoke node validator that hand-re-implements a subset (`:427-456`, `:398`, `:409-410`) and ignores the schema's `const`/`additionalProperties` as authority. `check.zsh` never calls `ai_litellm_harness_validate` (only `doctor_harnesses:1745` does). **HARD REPRO:** with `goose` CLI absent, `ai_litellm_harness_validate goose` rc=1 — structural validity wrongly coupled to CLI presence (ties to S3-01).
- **recommended action:** Add a `check.zsh` step validating each `harnesses/*.json` (except `schema.json`) against `schema.json` (python3 jsonschema, ajv, or ruby json-schema), or at minimum jq-assert required keys + adapter enum membership.
- **files:** `scripts/check.zsh`, `.github/workflows/ci.yml`
- **tests:** for each descriptor assert required keys + adapter enum against `schema.json`.

---

#### S7-05 — `check.zsh` doesn't assert placeholder rendering, shim correctness, or token-budget values
- **severity:** medium | **scope:** CI coverage | **confidence:** high
- **evidence:** `check.zsh:37-51` temp-HOME block asserts file existence + runs `ai_litellm_model_limits`/`ai_litellm_harness_output_budget` with output discarded (`>/dev/null`, only exit code checked) + asserts native dirs absent. It does NOT: (a) grep for leftover `__HOME__`/`__FABRIC_HOME__` placeholders; (b) assert shim content; (c) assert reservation numbers. All correct today (placeholders absent; shim sets `AI_LITELLM_FABRIC_HOME` + `exec`; haiku/GLM-5.1 reservation=32000 effectiveInput=162560; sonnet/Kimi 262144→effectiveInput=221952; opus/DeepSeek 1008384) but unguarded.
- **recommended action:** Extend `check.zsh` temp-HOME block: (1) `! grep -rq '__HOME__\|__FABRIC_HOME__' $prefix`; (2) assert a shim contains `AI_LITELLM_FABRIC_HOME=` and `exec` of `$prefix/bin`; (3) assert `ai_litellm_harness_output_budget claude sonnet Kimi-K2.6` yields `effectiveInput>0` and `reservation<capability`.
- **files:** `scripts/check.zsh`
- **tests:** placeholder grep; shim content assertion; reservation jq assertions.

---

### NICE-TO-HAVE

---

#### SEC6-07 — Provider env file parsed with `source`; embedded shell executes (self-injection)
- **severity:** low | **scope:** secrets | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:71-86` `ai_litellm_env_value` runs `source "$env_file"` inside an `emulate -L zsh; set -a` subshell, then reads `${(P)key}`. First resolution path for every secret (`resolve_secret_var:578`, `openrouter_key`/`master_key`, `harness_secret_value`:605). **HARD REPRO (temp HOME):** value `sk-or-test$(touch $tmp/PWNED)END` → resolved=`sk-or-testEND` and `$tmp/PWNED` created; the exact `value; touch $tmp/SEMI_PWNED` form (originally claimed "safe") created `SEMI_PWNED`. Threat model: env file at `$prefix/state/ai-litellm/env` is same-user-owned (self-injection, no privilege boundary crossed) — but it falsifies the "no command injection" invariant and is a footgun for tooling that writes the env file from less-trusted input.
- **recommended action:** Parse the env file with a literal reader instead of `source`: read line by line, split on first `=`, assign with `typeset "name=$value"` (no evaluation), reject lines not matching `^[A-Za-z_][A-Za-z0-9_]*=`. Keep 700/600 perms.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** `check.zsh`: write env value `x$(touch $tmp/SHOULD_NOT_EXIST)y`, resolve key in temp HOME, assert file absent and resolved value literal.

---

#### PKG-04 — Uninstall without `--prefix` silently orphans a custom-prefix install
- **severity:** low | **scope:** packaging/uninstall | **confidence:** high
- **evidence:** `uninstall.zsh:6` `prefix="${AI_LITELLM_FABRIC_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-litellm-fabric}"`. Install `--prefix $tmp/custom` then uninstall with no `--prefix` → reports "Removed", removes all 7 shims, but `$tmp/custom` is NOT removed (orphan). Mitigated by `install.zsh:193` printing the correct `uninstall.zsh --prefix <p>` hint.
- **recommended action:** Record the prefix in a manifest (e.g. `$bin_dir/.ai-litellm-fabric-prefix`) and have uninstall read it; or warn when `$prefix` does not exist.
- **files:** `scripts/uninstall.zsh`, `scripts/install.zsh`
- **tests:** install `--prefix P` then uninstall `--prefix P`, assert P gone; install `--prefix P` then uninstall (no prefix) and assert it warns rather than reporting clean success.

---

#### SEC6-03 — Generated `opencode.json` is 644 (inconsistent with codex 600)
- **severity:** low | **scope:** secrets | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:825` `File.write(config_path, ...)` has no mode arg; `apiKey` is placeholder `"{env:LITELLM_MASTER_KEY}"` (`:810`). Contrast codex `config/codex-litellm/shell.zsh:148` `{mode:0o600}` + chmod 600 catalog `:265`. **REPRO:** rendered `opencode.json` → `-rw-r--r--`, apiKey is the placeholder, no secret. Defense-in-depth, not a current leak.
- **recommended action:** Write `opencode.json` via a tmp file with mode `0o600` then rename (mirror codex).
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** `check.zsh`: after rendering, assert perms == 600 and no `sk-` secret.

---

#### SEC6-04 — `prefix/state` dirs created 755; only `AI_LITELLM_HOME` hardened to 700 at start
- **severity:** low | **scope:** secrets | **confidence:** high
- **evidence:** `scripts/install.zsh:163-168` plain `run mkdir -p` (default umask). **REPRO:** `find $prefix/state -type d` → 755 for `state`, `state/ai-litellm`, and per-harness subdirs. The user env file lives at `$AI_LITELLM_HOME/env = $prefix/state/ai-litellm/env`. `ai_litellm_start` chmods `AI_LITELLM_HOME` 700 (`lib.zsh:1332`) and log 600 (`:1333,:1382`) ONLY at proxy-start, never the parent `state/` tree.
- **recommended action:** `chmod 700 $prefix/state` and per-harness subdirs in `install.zsh` (or restrictive umask around the mkdir loop).
- **files:** `scripts/install.zsh`
- **tests:** `check.zsh`: assert `stat -f %A $prefix/state/ai-litellm` == 700 after install.

---

#### SEC6-05 — `uninstall.zsh rm -rf "$prefix"` trusts `AI_LITELLM_FABRIC_HOME`/`--prefix` with no sanity guard
- **severity:** low | **scope:** secrets | **confidence:** high
- **evidence:** `scripts/uninstall.zsh:6` (default), `:53` (`--prefix`), `:82` `run rm -rf "$prefix"`. The `:-` default only triggers on unset/empty (empty is safe). **REPRO (dry-run only):** `AI_LITELLM_FABRIC_HOME=/tmp/x scripts/uninstall.zsh --dry-run` → `dry-run rm -rf /tmp/x`. Quoting is correct; the gap is a missing value sanity check. Not a default-path data-loss — requires a hostile/mis-set value.
- **recommended action:** Before `rm -rf "$prefix"`, assert `[[ "${prefix:t}" == ai-litellm-fabric && -f "$prefix/config/ai-litellm/lib.zsh" ]]`, else error out.
- **files:** `scripts/uninstall.zsh`
- **tests:** `check.zsh`: assert `uninstall --prefix /tmp` refuses (non-fabric path) without `--force`.

---

#### S4-08 — `schema.json` doesn't require new claude reservation adapterConfig fields
- **severity:** low | **scope:** descriptor validation | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:430-432` (claude-code branch) requires only `baseUrlEnv/discoveryEnv/tierModelEnvPrefix/tierDisplayNameEnvPrefix`. `grep -nE 'autoCompactWindowEnv|maxOutputTokensEnv|outputReservation' schema.json` => NONE. Launcher falls back to literal env names if absent (`config/claude-litellm/shell.zsh:187-188`), and `ai_litellm_context_claude_reservations_ok` asserts `outputReservation.default` exists — so it's a validation gap, not a live failure. A descriptor could drop `outputReservation` entirely and pass `harness_validate`.
- **recommended action:** Add `autoCompactWindowEnv`/`maxOutputTokensEnv`/`outputReservation` (default/tokenizerHeadroom/minimumInput) to `schema.json` and/or the validator's claude-code branch; assert `outputReservation` presence for any harness referencing `{{reservation.*}}` in `adapterConfig.env`.
- **files:** `config/ai-litellm/harnesses/schema.json`, `config/ai-litellm/lib.zsh`
- **tests:** validate test that fails when `outputReservation`/`maxOutputTokensEnv` missing.

---

#### S4-06 — Reservation "selection" arg inconsistent across call sites; three budget implementations
- **severity:** low | **scope:** drift / internal API | **confidence:** high
- **evidence:** `ai_litellm_harness_output_budget(harness, selection, model)` uses `selection` for `perSelection.${selection}`/`perTier.${selection}` (`lib.zsh:262-264`). Claude launcher `config/claude-litellm/shell.zsh:215` calls it with `$claude_model_arg` = the TIER (opus/sonnet/haiku); `ai_litellm_harness_env_assignments` (`lib.zsh:495`) calls it with `$model_name`. **HARD REPRO** (scratch copy, `perTier.opus=5000` injected): `... claude opus DeepSeek-V4-Pro` → reservation 5000; `... claude DeepSeek-V4-Pro DeepSeek-V4-Pro` → 32000. They diverge. Currently HARMLESS (only `default` configured; claude's `adapterConfig.env` absent), but a future `perTier`/`perSelection` policy would make launcher env and `{{reservation.*}}` silently disagree. Three budget impls exist: node (`lib.zsh:238-306`), ruby matrix (`~:3058`), ruby doctor (`~:3585`).
- **recommended action:** Define `selection` canonically (always the tier for tier-alias harnesses, model_name otherwise) and pass it consistently from `env_assignments`. Ideally collapse the three implementations into one.
- **files:** `config/ai-litellm/lib.zsh`, `config/claude-litellm/shell.zsh`
- **tests:** doctor/test asserting launcher reservation == env_assignments reservation for the same tier when a perTier policy exists.

---

#### S4-05 — Codex is the only reservation-policy-less harness; unguarded on shared-window backends
- **severity:** low | **scope:** drift | **confidence:** medium
- **evidence:** `config/ai-litellm/harnesses/codex.json` has NO `adapterConfig.outputReservation` and no `adapterConfig.env` (claude/goose/opencode all carry `outputReservation {default:32000,tokenizerHeadroom:8192,minimumInput:32768}`). `codex-litellm-refresh-catalog` (`config/codex-litellm/shell.zsh:253-256`) maps each model to `{context_window:ctx, max_context_window:ctx, auto_compact_token_limit:null}` where `ctx` = `ai_litellm_limits_map` (the max_input_tokens anchor). Matrix: `gpt-5.4`→Kimi configured 262144/262144 effective_input=262144 (full window, NO reservation). `harness_reservations_ok`/matrix skip empty policy (`lib.zsh:3060`, `:3628`), so Codex is silently exempt. Tie this to S4-04 (a gateway clamp covers Codex automatically).
- **recommended action:** Either (a) document that Codex relies on its own compaction and is intentionally reservation-free, or (b) add `adapterConfig.outputReservation` + per-slug `max_output_tokens` reduction in the generated catalog.
- **files:** `config/ai-litellm/harnesses/codex.json`, `docs/AI_AGENT_LITELLM_ARCHITECTURE.md`
- **tests:** extend `harness_reservations_ok` or a new doctor to assert Codex's exemption is intentional.

---

#### S5-06 — `stop` deletes new-location hash sidecar even when proxy resolved via legacy pid file
- **severity:** low | **scope:** proxy-lifecycle | **confidence:** medium
- **evidence:** `config/ai-litellm/lib.zsh:1464-1481` `ai_litellm_stop`: pid via `active_pid_file` (new + 2 legacy paths), `kill "$pid"`, removes both legacy pid files, then `rm -f "$AI_LITELLM_CONFIG_HASH_FILE" "$AI_LITELLM_STARTED_AT_FILE"` (new-location only). `proxy_config_current` (`:1258`) only reads the new hash file. Asymmetry: pid resolution multi-path, config-state recording single-path. Low impact (restart re-records) but muddies status during legacy migration.
- **recommended action:** Make the identity check (S5-02) authoritative and only clear the hash/started_at belonging to the stopped proxy, or document the new-location-only bookkeeping.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME: simulate legacy-active stop; assert status doesn't claim a current hash for a gone proxy.

---

#### S7-07 — README "First Run" omits that `sync` restarts the shared proxy and smoke tests are billable
- **severity:** low | **scope:** docs/code agreement | **confidence:** medium
- **evidence:** `README.md:126-140` "First Run" lists `ai-litellm sync` (`:132`) + `claude-litellm haiku -p ...` (`:138`) + `codex-litellm gpt-5.4 exec ...` (`:139`) with no caveat. `docs/AI_AGENT_LITELLM_ARCHITECTURE.md:158`: sync takes down the shared proxy, affecting all running sessions; `:102` `ai-litellm sync # 파생 설정 재생성 + proxy 재기동`. Smoke tests make real (billable) provider calls. README understates side effects the architecture doc calls out.
- **recommended action:** Add a one-line note in README "First Run" that `sync` regenerates derived config + restarts the shared proxy (disrupting in-flight sessions), and that the harness one-liners make real (billable) provider requests.
- **files:** `README.md`
- **tests:** none.

---

#### S7-06 — CI single-OS, no toolchain pins; node/ruby/python3 assumed present
- **severity:** low | **scope:** CI robustness | **confidence:** medium
- **evidence:** `.github/workflows/ci.yml`: single job `check`, `runs-on: macos-latest` (`:11`), `brew install jq ripgrep` (`:17`), `./scripts/check.zsh` (`:20`). No matrix, no setup-node/python/ruby, no pins. `check.zsh` depends on python3 (`:17`), ruby (`:27`), jq (`:24`), rg (`:29`); lib.zsh additionally uses node — only jq+rg explicitly installed. Linux coverage intentionally absent (zsh/Keychain-centric, acceptable).
- **recommended action:** Add explicit setup/version-print steps (setup-node/setup-python or `brew install node ruby`) and pin litellm if S7-01 is adopted.
- **files:** `.github/workflows/ci.yml`
- **tests:** CI: echo node/ruby/python3/jq/rg `--version` before `check.zsh`.

---

#### GITIGNORE-STALE — `.gitignore` comment claims state under `~/.config`; actually `prefix/state`
- **severity:** low | **scope:** docs consistency | **confidence:** high
- **deduped from:** PKG-06, S4-07, S5-05, S7-04, SCOPE8-08 (five scopes independently flagged this).
- **evidence:** `.gitignore:5-6` `# Runtime state. These are created under ~/.config at install/run time, not kept // in this repository.` But `install.zsh:157-167` creates state under `$prefix/state/{ai-litellm,claude-litellm/claude-config,codex-litellm/codex-home,goose-litellm,opencode-litellm}`; `lib.zsh:3-7` `AI_LITELLM_STATE_HOME=$AI_LITELLM_FABRIC_HOME/state`. `~/.config` appears only as `AI_LITELLM_LEGACY_*` fallbacks (`lib.zsh:18-23`) and `uninstall.zsh:84-92 --legacy`. Temp-HOME install: state under prefix, `~/.config` not created. Behavior is correct; only the comment is stale. **Caveat (PKG-06):** the real `~/.config/ai-litellm/litellm.log` has a recent mtime, hinting it may still be written in dev mode — does not affect the core claim.
- **recommended action:** Update the `.gitignore` comment to say state is created under the install prefix (`${XDG_DATA_HOME:-~/.local/share}/ai-litellm-fabric/state`), with `~/.config/*` retained only as legacy migration fallbacks.
- **files:** `.gitignore`
- **tests:** none.

---

#### S3-03 — `context probe` calls bare `codex` (not descriptor command), unguarded
- **severity:** low | **scope:** optional-harness | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:3291,3294` `ai_litellm_context_probe_codex_native` runs `codex debug models | jq ...` unguarded; matrix Ruby (`:2999-3000`) also uses bare `codex` but is Open3-rescued (`:2917-2928`). None use `ai_litellm_harness_json codex command`. Temp-HOME, codex absent: `context probe codex-cli-oauth` printed two `command not found: codex` lines (probe-exit=0); matrix rendered fully.
- **recommended action:** Resolve `codex_cmd=$(ai_litellm_harness_json codex command 2>/dev/null || echo codex)` and guard the two probe lines with `command -v "$codex_cmd"`. Optionally thread the resolved command into the matrix Ruby.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME, codex absent: `context probe codex-cli-oauth` emits no `command not found`.

---

#### S3-04 — `sync` gates codex *config* render (CLI-free) behind `command -v codex`
- **severity:** idea | **scope:** optional-harness | **confidence:** high
- **evidence:** `config/ai-litellm/lib.zsh:1925-1934`: codex branch gates BOTH `--refresh-catalog` and `ai_litellm_render_codex_config` on `command -v "$codex_command"`. OpenCode branch (`1936-1939`) only checks the descriptor exists then renders. **REPRO:** with both CLIs absent, `ai_litellm_render_codex_config` wrote `config.toml` (842 B) and `ai_litellm_render_opencode_config` wrote `opencode.json` (2051 B) — proving codex config render is CLI-free (only the catalog refresh at `shell.zsh:215` needs `codex debug models --bundled`). Result: codex `config.toml` can stay stale after sync on a codex-less box. No crash.
- **recommended action:** Render codex `config.toml` even when the codex CLI is absent, gating only the catalog refresh on `command -v codex`.
- **files:** `config/ai-litellm/lib.zsh`
- **tests:** temp-HOME, codex absent: assert sync (proxy restart stubbed) re-renders codex `config.toml` and prints a catalog-only skip note.

---

## 6. Recommended implementation plan for Codex

Ordered by risk × value, sequenced so dependencies land first.

1. **PKG-01 (high, trivial fix, high value).** One-line change in `install.zsh:116` to `print -r -- "...${(qq)prefix}"`. Unblocks every install on a spaced path. Land first because it's the cheapest high-severity fix and the new `check.zsh` spaced-prefix test (S7-05/PKG-01) validates it.
2. **S5-02 (high) + S5-03/S5-04/S5-06 (proxy identity cluster).** Add a single `ai_litellm_pid_is_litellm` identity check (`ps -o command=`) and route all of `pid_from_file`/`stop`/`start`/`status`/`lock_stale` through it. This is the keystone for the whole proxy-lifecycle cluster (S5-02 stop-safety, S5-03 foreign-adoption refusal, S5-06 sidecar correctness). Do S5-02 first; S5-03/S5-06 build on the identity primitive; S5-04 adds the age fallback.
3. **S5-01 (high) — sync `--dry-run`/`--no-restart`.** Reuse the existing `run()` dry-run pattern from `install.zsh`/`uninstall.zsh`. Independent of the proxy cluster; can land in parallel.
4. **S7-01 + S4-04/SCOPE8-02 (token-clamp gateway + CI verification).** Decide the policy first (enable `modify_params`/ship the async hook vs. accept harness-only + add a doctor warning), then wire `verify_litellm_token_clamp.py` into a dedicated CI job with a pinned `litellm` and add the script's contract-assertion mode. These two are coupled: CI verification is only meaningful once the policy is decided.
5. **SEC6-01 (master key off argv) + SEC6-07 (literal env parser).** Two focused security hardenings in `lib.zsh`. SEC6-01 factors a single `ai_litellm_curl_auth` helper (5 call sites); SEC6-07 replaces `source` with a literal `KEY=VALUE` reader. Independent; land together as the "secrets hardening" change.
6. **S3-01 + S7-03/SCOPE8-03 (validation split).** Split `harness_validate` into structural validity vs. CLI-availability, add explicit CLI guards in `ai_litellm_launch` (the caveat), and wire real schema validation into `check.zsh`. S3-02/S3-03/S3-04 (codex-absent guards) ride along naturally once validation no longer conflates CLI presence with validity.
7. **PKG-02 + PKG-03/S7-02 (backup discipline).** Make `backup_if_exists` content-aware (skip installer-owned identical files) and have `uninstall.zsh` glob-remove `.bak.*`. PKG-02 should land before/with PKG-03 since fixing the root cause shrinks the uninstall cleanup.
8. **Remaining low/nice-to-have:** PKG-04 (prefix manifest), SEC6-03/04/05 (perms + rm-rf guard), S4-05/06/08 (reservation consistency + schema), S7-05/S7-06/S7-07, S3-04, and the **GITIGNORE-STALE** comment (one-line, do it anytime). Batch the docs/comment edits.
9. **Architecture ideas (see section below):** schedule after the must/should-fix items; several (doctor install, fixture test suite, bootstrap) are larger refactors.

**Rationale:** highs that are cheap (PKG-01) or that gate a whole cluster (S5-02 identity primitive) go first; the token-clamp decision is a policy gate that several CI/doctor items depend on; security hardenings are self-contained and high-trust; validation refactor unlocks the codex-absent cluster; backup discipline is paired root-cause/cleanup.

## 7. Exact files likely to edit

- `scripts/install.zsh` — PKG-01 (line 116), PKG-02 (backup_if_exists), SEC6-04 (state dir perms), PKG-04 (write prefix manifest).
- `scripts/uninstall.zsh` — PKG-03/S7-02 (`.bak.*` glob), PKG-04 (read manifest/warn), SEC6-05 (prefix sanity guard), S5-07 (optional best-effort proxy stop / warn).
- `config/ai-litellm/lib.zsh` — S5-01/02/03/04/06 (sync flags, pid identity, lock age, sidecar), SEC6-01 (`ai_litellm_curl_auth`, 5 sites: 1241/1269/1562/1605/2508), SEC6-07 (literal env parser, 71-86), S3-01/02/03/04 (validate split + codex guards), S4-04 (doctor warn), S4-06 (selection canonicalization), S4-08 (validator claude-code branch).
- `config/claude-litellm/shell.zsh` — S4-06 (selection consistency).
- `config/ai-litellm/harnesses/schema.json` — S4-08, S7-03/SCOPE8-03 (reservation fields; authoritative validation target).
- `config/ai-litellm/harnesses/codex.json` — S4-05 (optional outputReservation).
- `config/litellm_config.yaml` — S4-04/SCOPE8-02 (`modify_params`/`callbacks` if policy chosen).
- `scripts/verify_litellm_token_clamp.py` — S7-01 (contract-assertion mode).
- `scripts/check.zsh` — S7-01/03/05, PKG-01/02/03, SEC6-03/04/05 (new assertions).
- `.github/workflows/ci.yml` — S7-01, S7-03, S7-06 (token-clamp job, toolchain setup).
- `README.md` — S7-07 (First Run side-effects note).
- `docs/AI_AGENT_LITELLM_ARCHITECTURE.md` — S4-05 (Codex exemption note).
- `.gitignore` — GITIGNORE-STALE comment.

## 8. Suggested regression tests

All to run in fresh `mktemp` HOMEs; never touch real `$HOME`.

- **Spaced prefix (PKG-01):** install into a prefix WITH a space, assert `"$bin/ai-litellm" version` exits 0.
- **Reinstall idempotency (PKG-02):** install twice into same temp HOME, assert no `*.bak.*` under `$prefix` (or a bounded count).
- **Uninstall cleanliness (PKG-03/S7-02):** install twice + uninstall, assert no `*-litellm*` and no `*.bak.*` in `$bin_dir`.
- **Custom-prefix uninstall (PKG-04):** `uninstall --prefix P` removes P; `uninstall` (no prefix) on a custom install warns rather than reporting clean success.
- **pid identity (S5-02):** a non-litellm live PID in a (legacy) pid file must NOT be reported running and `stop` must NOT kill it.
- **foreign proxy (S5-03):** mock `health=true` + a differing `proxy_model_names`; assert `start` refuses instead of "already reachable".
- **lock age (S5-04):** live unrelated PID + proxy not healthy + old `started_at` → `lock_stale` eventually true.
- **sync dry-run (S5-01):** stub `ai_litellm_restart`; assert `sync --dry-run` prints plan, does not restart, exits 0; assert sync help mentions `--dry-run`.
- **token-clamp (S7-01/S4-04):** CI job `pip install litellm==<pin>` + `verify_litellm_token_clamp.py --json` asserting the documented matrix and exiting non-zero on divergence.
- **schema validation (S7-03/SCOPE8-03/S4-08):** each `harnesses/*.json` (except `schema.json`) validated against `schema.json`; a descriptor dropping `isolation.env` or `outputReservation` fails.
- **validate split (S3-01):** with harness CLIs stripped from PATH, `proxy doctor` emits no `fail harness descriptor valid`; `harness info` Status stays `ok`; `ai_litellm launch <h>` still hard-fails when CLI missing.
- **codex-absent (S3-02/03):** `context doctor` exits 0 (or only warns) with no `command not found`; `context probe codex-cli-oauth` emits no `command not found`.
- **sync codex render (S3-04):** with codex absent (proxy restart stubbed), assert sync re-renders codex `config.toml` and prints a catalog-only skip note.
- **secrets (SEC6-01/03/04/07):** `rg 'Authorization: Bearer \$' lib.zsh` returns nothing; rendered `opencode.json` is 600 with no `sk-` literal; `stat -f %A $prefix/state/ai-litellm` == 700; env value `x$(touch $tmp/SHOULD_NOT_EXIST)y` resolves literally and creates no file.
- **rendering/reservation (S7-05):** no `__HOME__`/`__FABRIC_HOME__` in rendered prefix; shim contains `AI_LITELLM_FABRIC_HOME=` + `exec` of `$prefix/bin`; `ai_litellm_harness_output_budget claude sonnet Kimi-K2.6` → `effectiveInput>0` and `reservation<capability`.
- **uninstall safety (SCOPE8-06):** assert `uninstall` (and `--dry-run`) never references/removes `~/.claude` or `~/.codex`.

## 9. Open questions / assumptions

1. **Gateway clamp policy (S4-04):** Is the project deliberately keeping output-reservation enforcement at the harness layer (and accepting that raw `:4000` callers are uncapped on shared-window providers), or should `modify_params`/the async hook be enabled? This decision gates S7-01's CI design.
2. **`started_at` intent (S5-04):** Was `started_at` always meant to feed an age-based staleness fallback (just never wired), or is it vestigial and safe to remove?
3. **Codex reservation (S4-05):** Is Codex's reservation-free model an intentional product decision (relies on native compaction) or an oversight when `outputReservation` was added to the other three?
4. **Legacy `~/.config` lifecycle (PKG-06 caveat):** The real `~/.config/ai-litellm/litellm.log` had a recent mtime — is dev mode still actively writing there, or is it purely migration-read? Affects how the `.gitignore` comment should be worded.
5. **litellm pin (S7-01/S7-06):** Which `litellm` version should CI pin? The clamp behavior (`modify_params` vs `async_pre_call_deployment_hook`) is version-sensitive.
6. **Assumption:** SEC6-02 is treated as a `hypothesis` (defense-in-depth) and not scheduled — confirm that the single-user macOS threat model makes the transient `env(1)` argv window acceptable.
7. **Assumption:** S5-03 is `medium` (sound inference, not a live two-proxy transcript) because binding a second proxy to `:4000` was outside the audit's side-effect limits; a verifier with an isolated port should confirm before implementation.

## 10. DO NOT TOUCH NATIVE STATE — explicit warning

**The native boundary is the project's core safety guarantee and was verified intact. Any implementation MUST preserve it:**

- **Never write to `~/.claude` or `~/.codex`** (nor `~/.config/*` outside the legacy-migration *read* path, nor `~/litellm_config.yaml`). A temp-HOME install must continue to create only `~/.local/share/ai-litellm-fabric/` (the prefix) + the 7 shims in `~/.local/bin`. `check.zsh:45-50` asserts these absences — do not weaken those assertions.
- **Never replace, symlink, alias, or shadow the native `claude`/`codex`/`goose`/`opencode` binaries.** A repo-wide grep for `ln -s`/`cp`/`alias`/native bin paths returned ZERO hits; keep it that way.
- **Keep isolation intact:** `CODEX_HOME`/`CLAUDE_CONFIG_DIR` must continue to resolve under `prefix/state/*` via descriptor `isolation.env` + the launchers (`config/codex-litellm/shell.zsh:161-164`, `config/claude-litellm/shell.zsh:184/195`), with cross-harness vars stripped via `env -u` in `ai_litellm_harness_exec_env` (`lib.zsh:621-643`).
- **Native-state access stays read-only.** The context doctor/probe may *read* `~/.codex`/`~/.claude` and invoke native `codex debug models` read-only for budget comparison — it must never mutate native state. The only two `File.write` calls (`lib.zsh:825`, `:2373`) target package state; do not introduce writes to native dirs. If you add the optional `command -v codex` guards (S3-02/03), do not change them into write paths.
- **`rm -rf` only the package prefix.** SEC6-05's guard must ensure `rm -rf "$prefix"` only ever deletes a genuine `ai-litellm-fabric` package dir, and S5-07's optional proxy-stop must not touch native dirs.
- **No secret literals** may be committed; secrets stay off-shell and out of generated files (placeholders only).

## 11. Appendix: useful commands

```sh
# Baseline checks
./scripts/check.zsh                                  # => ok
git rev-parse --short HEAD                            # => 753eee6
git diff --check                                      # clean

# Clean temp-HOME install (never touches real $HOME)
tmp_home=$(mktemp -d); HOME=$tmp_home /Users/xz0831/ai-litellm-fabric/scripts/install.zsh; echo $?
ls -la "$tmp_home"                                    # expect only .local
find "$tmp_home/.local/share/ai-litellm-fabric/state" -type d
for p in "$tmp_home/.codex" "$tmp_home/.claude" "$tmp_home/.config" "$tmp_home/litellm_config.yaml"; do [[ -e $p ]] && echo "LEAK $p" || echo "ok-absent $p"; done
rm -rf "$tmp_home"

# PKG-01 spaced-path repro
tmp_home="$(mktemp -d '/tmp/ai test XXXXXX')"; HOME="$tmp_home" scripts/install.zsh >/dev/null
sed -n '2p' "$tmp_home/.local/bin/ai-litellm" | od -c
HOME="$tmp_home" "$tmp_home/.local/bin/ai-litellm" version    # currently fails

# PKG-02/03 backup orphans
tmp_home=$(mktemp -d); HOME=$tmp_home scripts/install.zsh >/dev/null; sleep 1.2
HOME=$tmp_home scripts/install.zsh >/dev/null; HOME=$tmp_home scripts/uninstall.zsh
find "$tmp_home/.local/bin" -name '*.bak.*'

# S5-02 pid identity (foreign process adopted/killed) — illustrative
# write a live unrelated PID into the legacy pid file, then observe ai_litellm_stop

# SEC6-01 master-key argv leak
curl --max-time 4 -s -H "Authorization: Bearer sk-PROOF" http://10.255.255.1/ >/dev/null 2>&1 &
sleep 0.4; ps -ww -o args= -p $! | grep -o 'Bearer [^ ]*'

# SEC6-07 source-injection (value below is a benign placeholder; the $(...) is the point,
# and the literal token is broken up so this doc itself passes the repo secret scan)
printf 'OPENROUTER_API_KEY=PLACEHOLDER$(touch %s/PWNED)END\n' "$tmp_home" \
  > "$tmp_home/.local/share/ai-litellm-fabric/state/ai-litellm/env"
# resolve the key, then: [[ -e $tmp_home/PWNED ]] && echo INJECTED || echo SAFE

# Validation / schema gaps
grep -n schema scripts/check.zsh                      # no schema-validation step
rg -ni 'ajv|jsonschema' config/ scripts/              # no validator lib
grep -nE 'modify_params|callbacks' config/litellm_config.yaml   # none

# CI inspection
grep -nE 'litellm|pip|python' .github/workflows/ci.yml   # no matches

# Reservation budgets (temp HOME)
HOME=$tmp_home AI_LITELLM_FABRIC_HOME=$tmp_home/.local/share/ai-litellm-fabric \
  zsh -fc 'source $AI_LITELLM_FABRIC_HOME/config/ai-litellm/lib.zsh; ai_litellm_harness_output_budget claude sonnet Kimi-K2.6'
```

---

## Architecture ideas

These are larger, value-positive refactors (all `idea`-severity, verified, high confidence unless noted). Verdicts (`now`/`later`) from the architecture scope are preserved.

- **ARCH-01 / SCOPE8-01 — Manifest-driven install (verdict: now).** The bin list `ai-litellm claude-litellm codex-litellm goose-litellm opencode-litellm openrouter-key-status litellm-master-key-status` is repeated verbatim at `install.zsh:148`, `install.zsh:186`, and `uninstall.zsh:78`; `require_file` (`:131-146`) hardcodes 4 descriptors while `:174` glob-copies all 5 (incl. `schema.json`); `check.zsh:38-44` asserts only a subset. Drive install/uninstall/check from a single manifest so additions are one-line edits. Benefit: eliminates silent packaging drift. Complexity medium, migration risk low.
- **ARCH-02 / SCOPE8-02 — Deployable provider output-clamp hook (verdict: now).** Same root as S4-04: ship the `async_pre_call_deployment_hook` (or `modify_params`) as a real, referenced callback rather than a verifier-only string. Closes the sonnet→Kimi "input budget 0" class at the gateway regardless of harness cooperation. Complexity medium, migration risk medium (changes live `:4000` handling — stage behind a doctor check + verifier).
- **ARCH-03 / SCOPE8-03 — Authoritative schema validation (verdict: now).** Replace the hand-rolled JS validator (`lib.zsh:376-462`) + `jq empty` with real JSON-Schema validation against `schema.json`, and decouple structural validity from harness-CLI presence. Single source of descriptor truth; CI catches malformed descriptors pre-install. Complexity medium, risk low.
- **ARCH-04 / SCOPE8-04 — Generate docs tables from descriptors (verdict: later).** `docs/AI_AGENT_LITELLM_ARCHITECTURE.md:200` hardcodes reservation constants (32000/8192/32768) as prose and `:235` hand-lists per-harness env; these duplicate the descriptors and `litellm_config.yaml` with no freshness gate. `lib.zsh` already exposes `ai_litellm_limits_table` (`:2129`) and `ai_litellm_harness_output_budget` (`:230`), so the tables are machine-derivable. **Correction:** the cited `kimi_k26 {max_input_tokens:262144}` at doc line 215 is inside a fenced ```yaml example block, not free prose — the drift claim still holds via lines 200/235. Complexity medium, risk low.
- **ARCH-05 / SCOPE8-05 — `doctor install` + `sync --dry-run` (verdict: now).** `ai_litellm_doctor` (`lib.zsh:2036-2100`) has no package-layout self-check and rejects unknown args (no `install` subcommand); the layout invariants live only in `check.zsh`'s throwaway temp-HOME. Add a read-only `doctor install` (assert prefix bins executable, shims exec `prefix/bin`, state dirs exist, dev-vs-installed FABRIC_HOME consistency) reusing those invariants, plus the `sync --dry-run` from S5-01. Complexity low-medium, risk low.
- **ARCH-06 / SCOPE8-06 — Fixture-based temp-HOME integration suite (verdict: now).** `check.zsh:34-51` is a single inline smoke test; there is no coverage for uninstall safety, dry-run idempotency, `backup_if_exists`, multi-harness validate, or the legacy `~/.config` migration (the HEAD commit "Handle legacy proxy pid migration" added migration logic with zero automated tests). Build a small fixture suite. Complexity medium, risk low (test-only, all under mktemp HOME).
- **ARCH-07 / SCOPE8-07 — Single-command bootstrap with preflight (verdict: later).** No `scripts/bootstrap.zsh`; `README.md:60-64` lists prereqs as prose; `install.zsh` does no dependency preflight, yet `lib.zsh` hard-depends on node (22 sites), ruby (20 sites), jq, and a litellm python runtime. A bootstrap with fail-fast version/secret-store preflight would streamline new-Mac onboarding. **Must default to dry-run/confirm and keep `install.zsh` copy-only** (it touches the real machine). Complexity medium, risk medium.
- **ARCH-08 / S5-07 — Best-effort proxy stop on uninstall (verdict: later, idea).** `uninstall.zsh` is filesystem-only; `rm -rf "$prefix"` deletes `prefix/state/ai-litellm/litellm.pid` out from under a still-running proxy, orphaning it on `:4000` (ties into S5-03). Optionally call `ai_litellm_stop` (or warn "proxy may still be running; run `ai-litellm proxy stop` first") before `rm -rf`. Not user-data loss and does not touch native dirs. Low priority.

## False positives / rejected

- **REJECTED — SEC6-06 sub-claim #3 ("no command injection / env-file value resolves to the exact literal via printf %s + `${(P)key}`, executing nothing").** **False positive, contradicted by hard repro.** `ai_litellm_env_value` (`config/ai-litellm/lib.zsh:71-86`) resolves env-file values via `source "$env_file"`, NOT a pre-parsed literal — `source` evaluates embedded shell. In a fresh temp HOME, the value `sk-or-test$(touch $tmp/PWNED)END` created `$tmp/PWNED`, and the exact `value; touch $tmp/SEMI_PWNED` form the original called "safe" created `SEMI_PWNED`. The real behavior is captured as the (true) finding **SEC6-07**. The other SEC6-06 positives (secret scan clean, `env -i` no-leak, perl render safety, generated-file content key-free, output-reservation wiring) all reproduced and are retained as the verified-clean baseline.

- **DEMOTED to hypothesis — SEC6-02 (multi-provider secrets transiently in `env(1)` argv).** Code path and inline-vs-argv split confirmed by file:line (`lib.zsh:1389-1407`; OPENROUTER/MASTER are inline at `:1403-1404`, other vars via `env VAR=VAL` argv; same pattern in `ai_litellm_harness_exec_env` `:621-643`). But the argv-during-fork-exec exposure was **NOT observable** (`ps` returned NOT-FOUND; macOS env exec's immediately). Mechanism real, exploitable window not demonstrated → theoretical/defense-in-depth, not a proven leak. Not scheduled; optional hardening only.

**Verified-clean baselines (not defects — retained as grounding, all confidence high):**
- **PKG-05 / S3-05 / scope2-*** — Native boundary intact: temp-HOME install creates only `.local` + 7 shims; `~/.claude`/`~/.codex`/`~/.config/*`/`~/litellm_config.yaml` never created; global shim works after the source repo is deleted; CODEX_HOME/CLAUDE_CONFIG_DIR isolated into `prefix/state`; no native binary replacement; native-state access is read-only diagnostics; docs explicitly state native apps do NOT inherit LiteLLM config.
- **S3-06 / S4-01 / S4-02 / S4-03 / S7-08 / SEC6-06** — Reasoning matrix/doctor are harness-CLI-agnostic; output reservation is correctly derived from descriptor policy (never model capability) and wired into `CLAUDE_CODE_MAX_OUTPUT_TOKENS`/`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, goose/opencode `{{reservation.*}}` tokens, with all tiers having positive `effectiveInput >= minimumInput` (sonnet→Kimi 262144/262144 → 221952, the prior "input budget 0" worst case is fixed); x-limits anchors are the single capability source (no facade-number duplication outside the YAML); command surface, install paths, and reservation policy agree across README/architecture doc/code. Secret-storage posture is clean (no committed key literals; secrets never exported to the interactive shell; perl render injection-safe).
- **S3-07** — Dev-vs-installed state location is consistent via `AI_LITELLM_FABRIC_HOME`. Note: a temp-HOME `proxy doctor` reporting `ok proxy health` reflects the shared host proxy on `:4000` (live Python proxy confirmed) — environmental test-isolation caveat, not a product defect; future temp-HOME doctor tests should override host/port.
