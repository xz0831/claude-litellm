# Model ↔ Harness Context/Output Interaction — Audit & Codex Handoff

Audit method: 4 parallel probes (input-window, output-reservation, name-match quirks, empirical
boundary) → per-probe adversarial verification → reduction. Live boundary probes against the running
proxy cost ~$0.02 total. Live runtime = the installed package `~/.local/share/ai-litellm-fabric`;
source of truth = this repo. Models: DeepSeek-V4-Pro, Kimi-K2.6, GLM-5.1, local gemma (oMLX).

## Codex follow-up status (2026-06-08)

- **C2/C4/C5 resolved:** Codex now has descriptor-level output reservation, generated catalog windows are safe input budgets, and the gateway C4 callback clamps both `max_tokens` and `max_completion_tokens` before provider dispatch.
- **C6 resolved as provider-authoritative:** `ai-litellm model refresh-capabilities` reconciles OpenRouter-backed anchors against OpenRouter `/api/v1/models`. Current OpenRouter top-provider truth is DeepSeek `1048576/384000`, Kimi `262142/262142`, GLM input `202752`; GLM output remains unpublished by OpenRouter and is explicitly `owned-policy` at `131072`, not provider-declared.
- **C3 closed as owned policy for now:** local Gemma remains capped at `8192/4096` despite oMLX advertising a larger runtime window because `sliding_window=1024` makes long-context quality uncertain. Raising it requires a separate quality probe.
- **C1 partially observed:** A simple-token 210K-word Claude `opus` prompt succeeded through DeepSeek and returned the tail marker (`inputTokens=211580`, cost `$1.05905`), so the hard 200K name-derived clamp hypothesis is false. Full 1M honor remains unprobed because `--max-budget-usd` did not act as a hard cap on this LiteLLM path.
- **C7 observed; no code change:** Current catalog deletion produced no provider 400 on DeepSeek/Kimi/GLM, but `apply_patch` was not exposed to Codex exec. `apply_patch_tool_type="function"` is invalid for this Codex catalog schema, and `freeform` did not 400 but failed inside Codex core with incompatible payload/aborted. Keep deletion until Codex exposes a valid non-OpenAI patch tool mode.

Important correction: the earlier GLM `204800` boundary observation is no longer used as source of truth because the requested provider refresh currently reports OpenRouter top-provider `context_length=202752`. The architecture now prefers provider-authoritative values unless a later bounded probe is recorded as `observed` and encoded with matching confidence metadata.

## TL;DR for Codex

- **No zero-risk auto-fix qualified.** Every actionable change carries a value choice or an unverified
  mechanism (see "Why nothing was auto-applied"). They are all handed to you below (C1–C7).
- **The one live exposure:** `codex-litellm` is the **only** harness with **no output reservation**, so on
  the tight shared-window model **Kimi-K2.6 (262144 in / 262144 out)** a high-input Codex request can be
  rejected by the provider (reproduced: ~240k input → provider 400). claude/goose/opencode are protected
  by a 32000 reservation; codex is not. → **C2** (highest-urgency) + **C5** (policy).
- **The highest-leverage open question:** does Claude `opus` actually use DeepSeek's real **1,048,576**
  window, or silently clamp to its name-derived ~200K belief? Unresolved; needs one live probe. → **C1**.
- **Pure waste (no risk):** `gemma` is pinned to 8192 input but the oMLX runtime serves **131072**
  (30,025-token prompts accepted). ~94% of context unused. → **C3**.

---

## Provider-accounting facts (the empirical foundation)

| Fact | Status | Evidence |
|---|---|---|
| OpenRouter enforces `prompt_tokens + reserved max_tokens ≤ context_length` | **OBSERVED** | Kimi 1-tok prompt: `mt=262143 → 200`, `mt=262144 → 400 "(1 of text input, 262144 in the output)"`. Off-by-one exact, rejected **before** generation. |
| The **full reservation** (not generated tokens) is what gates | **OBSERVED** | `mt=262143` accept generated only ~63 tokens, yet `mt=262144` rejected pre-generation. |
| LiteLLM `pre_call_checks` gates **INPUT only**, never inspects `max_tokens` | **OBSERVED** | input ~300008 + `mt=100` → LOCAL 400 `"Max Input Tokens=262144, Got=300008"` (mentions only input). |
| Provider applies an **implicit default output reservation** when client sends no `max_tokens` | **OBSERVED** | Codex Responses, no `max_output_tokens`, input ~240k (< 262144 input cap) → **provider** 400 `"maximum context length is 262144"`; input ~200k → 200. ⇒ implicit default output ≈ 22k. |
| GLM real window = **204800** (not the 202752 config anchor) | **OBSERVED** | GLM 1-tok prompt `mt=204799 → 200` (impossible at a 202752 window). |
| Claude `AUTO_COMPACT_WINDOW` is **capped at the model's actual context window**; `[1m]` is read per-variable & stripped before send; `MAX_CONTEXT_TOKENS` only effective with `DISABLE_COMPACT` | **DOC-VERIFIED** | code.claude.com/docs env-vars + model-config. |
| oMLX gemma serves **>8192** despite the 8192 config cap | **OBSERVED** | 9,025- and 30,025-token prompts both HTTP 200, `finish_reason=stop`; server advertises `max_model_len=131072`. Caveat: `sliding_window=1024`. |

**The crux:** harnesses believe `window = INPUT only` and reserve output separately; OpenRouter counts
`input + reserved_output`. `pre_call_checks` catches input overflow but **never** the input+output sum,
and the gateway has **no output clamp**. So output-reservation size is the binding safety lever.

---

## PART A — Conflict matrix (4 models × 4 harnesses)

`BW` believed window · `RW` real provider window (observed) · `RO` reserved output · `IH` effective input headroom.

### DeepSeek-V4-Pro — RW 1,048,576 in / 384,000 out (huge slack; output ≪ window)
| Harness | INPUT | OUTPUT | Numbers |
|---|---|---|---|
| claude (opus) | **wasted? (disputed)** | ok | BW=200K name-derived *or* 1,008,384 injected; RW=1,048,576; RO=32,000 |
| codex (gpt-5.5) | ok | ok | BW=1,048,576 (catalog, pct=95); RO=provider-default fits |
| goose / opencode | n/a (default route is Kimi) | ok | — |

> Central dispute: launcher injects `CLAUDE_CODE_AUTO_COMPACT_WINDOW=1,008,384` and the live matrix shows
> opus `effective_input=1,008,384`, but whether Claude Code **honors** it vs clamps to a name-derived 200K
> is **not empirically proven**. → C1.

### Kimi-K2.6 — RW 262,144 in / 262,144 out (window == output cap → the ONLY tight model)
| Harness | INPUT | OUTPUT | Numbers |
|---|---|---|---|
| claude (sonnet) | ok | ok | BW=200K; RW=262,144; RO=32,000; IH=221,952 |
| **codex (gpt-5.4 / -mini)** | **overflow-risk** | **overflow-risk** | BW=262,144 (full); **RO=none**; provider-default output crowds high input → 400 (reproduced) |
| goose | ok | ok | RO=32,000 (`GOOSE_MAX_TOKENS`); IH=221,952 |
| opencode | ok | ok | RO=32,000 (`OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX`); IH=221,952 |

> **Highest-risk model**: `out_cap == in_cap == window`. Any nonzero output reservation crowds input; a
> full-window output request rejects on any input>0. claude/goose/opencode safe; **codex unguarded**. → C2.

### GLM-5.1 — RW 204,800 in (observed) / out cap ~131,072 (output < window → fits)
| Harness | INPUT | OUTPUT | Numbers |
|---|---|---|---|
| claude (haiku) | wasted (minor) | ok | BW=200K; RW=204,800; RO=32,000; IH=162,560 |
| codex (gpt-5.3-codex / gpt-5.2) | overflow-risk (mild) | ok | BW=202,752 (under-declared); RO=none; out 131,072 < window so a max-out turn fits, but high-input + provider-default output can 400 |
| goose / opencode | ok | ok | RO=32,000 |

> Anchor `glm51.max_input_tokens: 202752` under-declares the real **204,800** by exactly 2048 (safe
> direction, minor waste). GLM output cap 131,072 is **local-configured / unobserved**. → C6.

### gemma local — config-capped 8,192 in / 4,096 out; runtime serves 131,072
| Harness | INPUT | OUTPUT | Numbers |
|---|---|---|---|
| claude / codex / goose / opencode | **wasted (~94%)** | ok | BW=8,192; RW(runtime)=131,072; output auto-bounded ~4,096 |

> Never overflow-risk (all pinned to 8192) but ~94% wasted; `pre_call` rejects legitimate 8193..131072.
> Caveat: `sliding_window=1024` may degrade long-context quality. → C3.

### Per-model one-liners
- **DeepSeek**: output never conflicts (384k ≪ 1M). Only question: does opus use the 1M? (disputed, C1).
- **Kimi**: the one truly tight model. Safe on claude/goose/opencode; **codex is the live exposure** (C2).
- **GLM**: real window 204,800; config 202,752 → 2048 wasted, safe direction (C6). Output never zeroes input.
- **gemma**: no overflow anywhere; ~94% input wasted vs runtime's 131,072 (C3).

### Name-match-quirk verdict
- **claude**: tier IDs (`DeepSeek-V4-Pro`…) don't match `claude-*`/`opus` patterns → Anthropic name-keyed
  features (extended thinking, `cache_control`, effort) **auto-disable**. **Fails safe** (disables rather
  than injecting rejected fields). The opus `[1m]`/200K belief is the only consequential quirk (C1).
  **Verdict: benign + one open question.**
- **codex**: reuses `gpt-5.x` slugs; catalog refresh strips `supports_search_tool`,
  `apply_patch_tool_type`, `web_search_tool_type`. Residual `apply_patch type:"custom"` may be rejected by
  non-OpenAI backends — **latent, unprobed** (C7). Otherwise no family special-casing leaks.
- **goose / opencode**: generic openai-compatible naming + env injection. **Verdict: clean.**

---

## Why nothing was auto-applied (Part B = none)

Under strict re-classification, "urgent-clear-fix" requires **unambiguous + low-risk + mechanically
correct**. Every candidate failed that bar:
1. The "confirmations" (codex catalog honored; goose/opencode honest; opus injection present; generic
   naming clean) recommend **no change** — a no-op isn't a fix.
2. The actionable items (opus `[1m]`, codex output protection, gemma 8192→131072, GLM 202752→204800,
   gateway clamp) **each carry a value choice or an unverified mechanism** → judgment calls (C1–C7).

The most mechanically-determined candidate is the GLM window (204800 is empirically nailed) but it still
depends on re-verifying GLM's **output** cap and recovers only 2048 tokens — so even it is a deferred
judgment call, not a blind auto-apply. **Apply nothing blind.**

---

## PART C — Codex judgment-call handoff

### C1 — Claude opus `[1m]` / DeepSeek 1M strategy ⭐ highest leverage
- **Decide:** make opus actually use DeepSeek's 1,048,576 window, or accept a possible silent 200K clamp.
- **Unresolved fact:** opus may be name-derived ~200K (gateway discovery conveys only `display_name`, no
  window, no `[1m]`) → AUTO_COMPACT doc-clamped to ~200K → **~80% wasted**. Launcher injects
  `AUTO_COMPACT_WINDOW=1,008,384` and live matrix shows `effective_input=1,008,384`. **Whether the harness
  honors the injected 1M is not proven.**
- **Options:** (a) `ANTHROPIC_DEFAULT_OPUS_MODEL=DeepSeek-V4-Pro[1m]` (`[1m]` stripped before send → harmless
  to gateway; risk: unverified whether honored on a non-`claude` gateway ID). (b)
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS=1048576` **+ `DISABLE_COMPACT=1`** (only override that raises the believed
  window; requires DISABLE_COMPACT). (c) do nothing.
- **Settle it:** one bounded live Claude→gateway→DeepSeek session with a >200K prompt; observe whether it
  compacts near 200K (clamped) or near 1M (honored).
- **Recommended default:** (a) + keep the 32000 output reservation (so input+output ≤ 1,048,576). Probe first.
- **Files:** `config/claude-litellm/settings.json` (alias map), `config/claude-litellm/shell.zsh` (~line 206).

### C2 — Codex output protection on shared-window routes (Kimi/GLM) ⭐ the live exposure
- **Decide:** how to stop high-input Codex requests on Kimi/GLM from 400-ing (reproduced: ~240k input → 400).
- **Verified Codex lever:** `codex.json` has **no `outputReservation`**, and `model_max_output_tokens` is
  **parsed-but-ignored** by Codex (it never plumbs an output ceiling into the Responses body). So a
  Codex-side output cap **does not exist**. Real levers: catalog `context_window` /
  `effective_context_window_percent` (stamped to full window at `config/codex-litellm/shell.zsh:254-258`,
  no output subtraction), OR a gateway clamp (C4).
- **Options:** (a) lower the codex catalog window for shared slugs — in the refresh, stamp
  `context_window = ctx − reservation − headroom` (or lower `effective_context_window_percent`) for
  `gpt-5.4/-mini` (Kimi) and `gpt-5.3-codex/gpt-5.2` (GLM); DeepSeek `gpt-5.5` needs none. (b) gateway
  clamp (C4) — protects all harnesses at once including Codex.
- **Settle it:** implicit default output ≈ 22k (from the EMP boundary); pick a value keeping
  input+default-output < real window (e.g. shrink Kimi codex window to ~221,952).
- **Recommended default:** (b) durable backstop **plus** (a) cheap defense-in-depth on the two Kimi slugs.
- **Files:** `config/codex-litellm/shell.zsh:254-258` → regenerates `state/codex-litellm/model-catalog.json`.

### C3 — gemma 8192 vs real serving cap 131072
- **Decide:** raise the gemma input cap toward what the runtime serves, or keep 8192.
- **Evidence:** runtime `max_model_len=131072`; 30,025-token prompts accepted. 8192 rejects legitimate
  8193..131072. **Caveat:** `sliding_window=1024` may degrade long-context quality.
- **Options:** raise `gemma_local.max_input_tokens` (`config/litellm_config.yaml:19`) to e.g. 32768 or
  131072−output, then `ai-litellm sync`. Vs keep 8192.
- **Settle it:** a long-context quality probe given `sliding_window=1024` before adopting the full 131072.
- **Recommended default:** raise to a **middle value (~32768)**, leaving output room, pending the quality check.

### C4 — Central gateway `max_tokens` clamp design
- **Decide:** add a gateway output clamp, and which mechanism.
- **Evidence (observed against installed litellm 1.81.14):** plain `litellm_params.max_tokens` only
  **injects a default** when the client omits it — does **not** clamp a larger request. `modify_params:true`
  clamps `max_tokens` but **not** `max_completion_tokens` — and Codex uses `wire_api='responses'` (the
  at-risk path). Only an **`async_pre_call_deployment_hook`** clamps **both** (referenced in
  `scripts/verify_litellm_token_clamp.py`). Live config has `drop_params:true` only, no callbacks.
- **Open decision:** the per-model output cap **values** (tie to x-limits anchors: e.g. Kimi ≤32000,
  GLM ≤64000, DeepSeek generous).
- **Settle it:** run `scripts/verify_litellm_token_clamp.py` against a **temp-copy** config (export
  `AI_LITELLM_CONFIG`) — never the live file.
- **Recommended default:** the deployment hook with per-anchor caps. **Files:** `config/litellm_config.yaml`
  (`litellm_settings.callbacks`), `scripts/verify_litellm_token_clamp.py`.

### C5 — Output-cap-vs-window policy (the strategy verdict)
- **Decide:** one coherent policy. **You cannot raise a harness's believed window above the provider's real
  window — the provider rejects (proven).** Levers = honest window + right-sized output reservation.
- **Per-model verdict:**
  - **DeepSeek (1M / 384k out):** honest window; 32000 reservation leaves >96% input; **no tight cap needed.**
  - **Kimi (262K, out==window):** **MUST cap** — keep 32000 (→ 221,952 effective input); never approach 262,144. **Binding constraint.**
  - **GLM (204,800, out 131k):** 32000 comfortable (→ ~162,560); up to ~64000 acceptable.
  - **gemma (8K, runtime 131K):** output auto-bounded ~4096; input policy is C3.
- **Recommended default:** keep window=provider-real everywhere; **standardize a 32000 output reservation
  across all four harnesses including codex** (via C2/C4, since codex has none); raise opus via C1; raise
  gemma input via C3. Never raise a harness window above provider real.

### C6 — GLM window anchor correction (low urgency)
- **Decide:** update `glm51.max_input_tokens: 202752` → **204800** (observed via `mt=204799 → 200`).
- **Tradeoff:** recovers only 2048 tokens; current value is conservative/safe. The edit **also** requires
  re-verifying GLM's **output** cap (131072 is local-configured/**unobserved**).
- **Settle it:** re-fetch OpenRouter `/api/v1/models` for `z-ai/glm-5.1` (window + `max_completion_tokens`),
  then `ai-litellm sync`.
- **Recommended default:** **defer**; bundle with the next anchor refresh. **File:** `config/litellm_config.yaml:18`.

### C7 — Codex `apply_patch` tool-type (latent, unprobed)
- **Decide:** whether to stop the catalog refresh from leaving `apply_patch_tool_type = None` (→ freeform
  `type:"custom"`), which non-OpenAI Responses backends may reject.
- **Status:** corroborated by docs (litellm#15342) but **not empirically probed** against DeepSeek/Kimi/GLM.
- **Options:** in `config/codex-litellm/shell.zsh`, set `next.apply_patch_tool_type = "function"` for
  non-OpenAI slugs instead of deleting it.
- **Settle it:** one bounded `apply_patch` probe per backend through the live proxy (no 400, diffs apply).
- **Recommended default:** **probe first**; apply `"function"` only if a 400 is observed. Do not change blind.

---

## Confidence ledger (honest)
- **Empirically observed (~$0.02 of live probes):** OpenRouter combined accounting + off-by-one; pre_call
  input-only; provider implicit-output crowding; GLM real window 204800; Codex reasoning accepted on Kimi;
  gemma serves >8192; all litellm clamp mechanisms.
- **Doc-verified (not probed):** `[1m]` stripping + AUTO_COMPACT capping + MAX_CONTEXT_TOKENS/DISABLE_COMPACT
  coupling; Claude capability pattern-matching; `model_max_output_tokens` ignored by Codex.
- **Unresolved / disputed:** whether Claude opus honors the injected 1M vs name-clamps to 200K (C1) — the
  single biggest open question. **Inference, not observation:** `apply_patch type:"custom"` rejection (C7).

## Suggested order for Codex
1. **C2 + C5** — close the codex Kimi/GLM exposure (the live risk) with a standardized reservation; decide
   gateway-clamp (C4) vs catalog-belief-shaping.
2. **C1** — one probe to settle opus 1M, then pick (a)/(b)/(c).
3. **C3** — raise gemma input after the sliding-window quality check.
4. **C6, C7** — low-urgency / probe-gated.

## DO NOT
- Do not raise any harness's believed window above the provider's real window (provider rejects).
- Do not run `scripts/verify_litellm_token_clamp.py` or test config edits against the live file — use a
  temp copy (`export AI_LITELLM_CONFIG`). Do not write to `~/.claude` or `~/.codex`.
- Re-run `ai-litellm sync` (restarts the shared proxy) only deliberately; it affects live sessions.
