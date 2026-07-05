# P6: 카탈로그 셀프서비스 (`model add` / `model remove`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ai-litellm model add <provider-id> [opts]` / `ai-litellm model remove <surface> [--dry-run]` — 모델 카탈로그를 명령 한 줄로 추가/제거한다(x-limits 앵커 + model_list 라우트 자동 생성, 선택적 claude tier·codex catalog 배선, sync). "나중엔 제가 직접 쉽게 수정"의 응답.

**Architecture:** 기능 추가(삭제가 아니라 TDD 적합). check RED(fixture 주입으로 오프라인 add/remove 단언) → `model add` 구현 → `model remove` 구현 → 디스패치·usage 배선 + 전체 check GREEN 단일 커밋 → docs → 머신 반영(install + `--dry-run` 검증만; 실 registry는 사용자가 직접 mutate). OpenRouter 페치는 `AI_LITELLM_OPENROUTER_MODELS_JSON` env로 fixture 주입 가능(refresh-capabilities 선례 그대로).

**Tech Stack:** zsh + 임베디드 Ruby(YAML 조작, atomic write) in lib.zsh; JSON(claude settings, codex.json); OpenRouter /models.

**Spec:** `docs/superpowers/specs/2026-07-04-ai-litellm-refactor-design.md` §9

## 재사용 토대 (7a03f23/1fbe8d1 기준)

- **Fixture 주입**: `AI_LITELLM_OPENROUTER_MODELS_JSON=<path>`가 있으면 curl 대신 그 파일을 읽는다(`ai_litellm_model_refresh_capabilities` L3146-3157). check.zsh L411-413이 이미 이 env로 refresh-capabilities를 오프라인 테스트한다 — P6 add도 같은 env를 소비.
- **Atomic write**: `tmp="#{path}.tmp.#{$$}"; File.write(tmp, ...); File.chmod(File.stat(path).mode & 0o777, tmp); File.rename(tmp, path)` (L3366-3370). 모든 config mutation은 이 패턴.
- **디스패치**: `ai_litellm_cmd_model()` L5518 — `add)`/`remove)` verb 추가 지점.
- **정본 식별자**: LiteLLM surface `model_name`. route `litellm_params.model` = `openrouter/<provider-id>`.

## Global Constraints

- 라인 번호는 `1fbe8d1` 기준 앵커. 어긋나면 앵커 텍스트로.
- lib.zsh 임베디드 single-quoted Ruby 블록에 **어포스트로피 금지**; 편집 후 **clean-room 함수테이블 스모크**(zsh -f source + whence -f) 필수(P3-T5 교훈; zsh -n은 EOF-heredoc 못 잡음).
- 모든 config 쓰기는 **atomic**(tmp+rename) + 기존 퍼미션 보존. 실패 시 원본 무손상.
- **managed discovered-routes 블록(BEGIN/END 마커)은 절대 손대지 않는다** — add는 그 위에 append, remove는 그 블록 밖 라우트만.
- 삭제/추가 시 **callee caller-count 재스윕**(습관). 검증 pipefail-safe. 커밋은 check green에서만. Co-Authored-By 트레일러.
- 디렉토리 가드: 모든 서브에이전트 편집 전 `git rev-parse --show-toplevel`이 워크트리(p6-model)로 끝나는지 확인.
- **비-목표**: local/discovered 라우트 추가(runtime discovery가 자동), OpenRouter 외 직결 provider(수동 절차 유지), P5 개명.

## 명령 계약 (구현 정본 — 구현자는 이대로)

### `ai-litellm model add <provider-id> [--name <surface>] [--claude-tier <tier>] [--codex] [--dry-run]`
1. payload 확보(fixture env 우선, else curl). `data[].id == <provider-id>` 검색. 없으면 loud-fail(`model not found in OpenRouter catalog: <id>`).
2. 추출: `max_input = top_provider.context_length || context_length`. `max_out_pub = top_provider.max_completion_tokens`(있으면). `reasoning = supported_parameters includes "reasoning" or "reasoning_effort"`.
3. 출력캡 정책: `max_out_pub` 있으면 `max_output_tokens=max_out_pub` + `x_output_confidence: provider` + `x_output_source: openrouter.top_provider.max_completion_tokens`. 없으면 `max_output_tokens = [max_input, 32768].min` + `x_output_confidence: owned-policy` + `x_output_source: openrouter-unpublished; conservative default, review recommended` + **stderr 경고** "output cap not published by OpenRouter — set a conservative <N>; review with 'ai-litellm model limits <surface>'".
4. surface 이름: `--name` 우선. 없으면 파생 `<last-segment title-cased by hyphen>-openrouter`(예: `deepseek/deepseek-v4-pro` → `Deepseek-V4-Pro-openrouter`) + **stderr 안내** "derived name '<X>'; use --name for exact casing (e.g. DeepSeek-V4-Pro-openrouter)". 중복 model_name이면 loud-fail.
5. anchor 이름: provider-id 마지막 세그먼트를 snake_case(`[^a-z0-9]+`→`_`, 소문자)(예: `deepseek_v4_pro`). 중복 앵커명이면 `_2` suffix.
6. YAML 생성: `x-limits:` 아래 새 앵커(`&<anchor>` + 필드들, input은 항상 provider-confidence), `model_list:` 아래 새 route(`model_name`/`litellm_params.model: openrouter/<id>`/`api_key: os.environ/OPENROUTER_API_KEY`/`model_info: *<anchor>`). atomic write.
7. `--claude-tier <tier>`(∈ fable/opus/sonnet/haiku; else loud-fail): claude-litellm/settings.json의 `aliases.<tier>`=surface, `directAliases.<tier>`=`<provider-id>`, `displayNames.<tier>`/`directDisplayNames.<tier>`=`<name-stem> (openrouter)`. atomic JSON write.
8. `--codex`: codex.json `models.catalogEntries`에 `{slug: surface, displayName: "<stem> (openrouter)", description: "<id> via OpenRouter through LiteLLM.", priority: 88}` append.
9. `--dry-run`: 1-8의 **계획만** stdout에 출력(무쓰기), exit 0. else: 쓰기 후 `ai_litellm_sync` 실행(sync가 proxy 재기동 — 이는 실 실행 시에만).

### `ai-litellm model remove <surface> [--dry-run]`
1. `model_list`에서 `model_name == <surface>` 검색. 없으면 loud-fail.
2. **가드**: discovered 블록 내부 라우트면 loud-fail("discovered route — managed by runtime discovery"). `codex-auto-review`면 loud-fail("functional slug required by codex review"). local(`api_key: none`) 라우트면 loud-fail("local route — remove via runtime").
3. **참조 검사**: claude settings의 aliases/directAliases 어느 tier가 이 surface(또는 그 provider-id)를 가리키면 loud-fail(참조 tier 나열 + "reassign the tier first"). codex.json catalogEntries에 이 surface가 있으면 loud-fail("in codex catalogEntries; remove --codex wiring first" — 또는 자동 제거? **loud-fail 채택**, 명시성 우선).
4. anchor 판정: 이 route의 `model_info: *<anchor>`. 같은 앵커를 참조하는 **다른** route가 있으면 앵커 유지, 없으면 앵커도 제거.
5. YAML: route 블록 제거(+ 없어진 앵커 제거). atomic write.
6. `--dry-run`: 계획만 출력. else: 쓰기 후 `ai_litellm_sync`.

---

### Task 1: check.zsh RED — add/remove 단언 (fixture 주입)

**Files:** Modify `scripts/check.zsh`

- [ ] **Step 1:** 기존 refresh-capabilities fixture(L411-413, `AI_LITELLM_OPENROUTER_MODELS_JSON`) 근처 또는 새 블록에서, add/remove를 오프라인 검증하는 단언 추가. Fixture에 테스트용 모델 1개 추가(예: `{"id":"testorg/test-model-x","context_length":100000,"top_provider":{"context_length":100000,"max_completion_tokens":8000},"supported_parameters":["reasoning"]}`).
- [ ] **Step 2:** `--dry-run` 단언(무쓰기 확인):
```zsh
add_plan="$(AI_LITELLM_OPENROUTER_MODELS_JSON=$openrouter_models_fixture "$HOME/.local/bin/ai-litellm" model add testorg/test-model-x --name Test-Model-X-openrouter --dry-run 2>&1)"
print -r -- "$add_plan" | grep -q "Test-Model-X-openrouter"
print -r -- "$add_plan" | grep -q "max_input_tokens: 100000"
! grep -q "Test-Model-X-openrouter" "$AI_LITELLM_CONFIG"   # dry-run wrote nothing
echo "ok: model add --dry-run plans without writing"
```
- [ ] **Step 3:** add→remove 왕복 단언(실 쓰기, throwaway config — sync 없이 registry만 확인하려면 add/remove가 `--dry-run` 아닐 때 sync를 부르므로, **check용 env `AI_LITELLM_SKIP_SYNC=1`을 add/remove가 존중하도록** T2/T3에 요구; 그러면 registry write만 검증 가능):
```zsh
AI_LITELLM_SKIP_SYNC=1 AI_LITELLM_OPENROUTER_MODELS_JSON=$openrouter_models_fixture "$HOME/.local/bin/ai-litellm" model add testorg/test-model-x --name Test-Model-X-openrouter >/dev/null 2>&1
grep -q "model_name: Test-Model-X-openrouter" "$AI_LITELLM_CONFIG"
grep -q "max_input_tokens: 100000" "$AI_LITELLM_CONFIG"
ai_litellm_ruby -ryaml -e 'YAML.load_file(ARGV[0], aliases: true)' "$AI_LITELLM_CONFIG"   # still valid YAML w/ aliases
AI_LITELLM_SKIP_SYNC=1 "$HOME/.local/bin/ai-litellm" model remove Test-Model-X-openrouter >/dev/null 2>&1
! grep -q "Test-Model-X-openrouter" "$AI_LITELLM_CONFIG"
echo "ok: model add/remove round-trip writes+reverts registry"
```
- [ ] **Step 4:** guard 단언: tier가 참조하는 surface remove는 loud-fail:
```zsh
if AI_LITELLM_SKIP_SYNC=1 "$HOME/.local/bin/ai-litellm" model remove GLM-5.2-openrouter >/dev/null 2>&1; then echo "FAIL: removed a tier-referenced model"; exit 1; fi
echo "ok: model remove refuses tier-referenced surface"
```
(GLM-5.2-openrouter = opus tier in the check's installed config.)
- [ ] **Step 5:** RED 확인(pipefail-safe). 첫 실패는 add/dry-run 단언(add 미구현). 커밋 금지.

### Task 2: `model add` 구현

**Files:** Modify `config/ai-litellm/lib.zsh` (새 `ai_litellm_model_add` 함수, refresh-capabilities 근처)

- [ ] payload 확보(fixture env 재사용 — 헬퍼로 추출하거나 인라인), 명령 계약 §add 1-9 구현. YAML 생성은 임베디드 Ruby(atomic write). `--dry-run`/`AI_LITELLM_SKIP_SYNC` 존중. tier/codex 배선은 각각 claude settings·codex.json atomic write.
- [ ] 검증: `zsh -n`; clean-room 함수테이블 스모크; 어포스트로피 스윕; fixture로 단독 dry-run+실행 후 YAML 유효성(aliases:true 파싱) + `ai-litellm model limits <surface>` 동작 확인.

### Task 3: `model remove` 구현

**Files:** Modify `config/ai-litellm/lib.zsh` (새 `ai_litellm_model_remove`)

- [ ] 명령 계약 §remove 1-6 구현: 검색·가드(discovered/local/codex-auto-review)·참조 검사(tier/catalog loud-fail)·anchor 공유 판정·route/anchor 제거·atomic write·dry-run/skip-sync.
- [ ] 검증: 동일(zsh -n, clean-room, 어포스트로피, fixture 왕복, guard loud-fail).

### Task 4: 디스패치·usage 배선 + 전체 check GREEN + 단일 커밋

**Files:** Modify `config/ai-litellm/lib.zsh`(dispatch+usage), `scripts/verify_budget_consistency.py`(재앵커 필요시)

- [ ] `ai_litellm_cmd_model`에 `add)`/`remove)` verb 추가(→ `ai_litellm_model_add`/`_remove`). cmd_model usage 문자열 + `ai_litellm_usage` Model 줄에 `add <id> [opts]`/`remove <surface>` 추가.
- [ ] budget 슬라이스 재앵런 필요시(add/remove 함수가 lib.zsh에 라인 추가 → RUBY_MAT/RUBY_RES 이동 가능; 가드 loud-fail 유도→갱신→green).
- [ ] 전체 check GREEN(pipefail-safe) — 새 add/remove 단언 포함. 실패 시 BLOCKED(단언 약화 금지).
- [ ] `git add -A`, 커밋:
```
feat: add 'model add' / 'model remove' catalog self-service

ai-litellm model add <provider-id> fetches OpenRouter capabilities
(fixture-injectable via AI_LITELLM_OPENROUTER_MODELS_JSON), writes an
x-limits anchor + model_list route (provider-confidence, owned-policy
fallback for unpublished output caps), optionally wires a Claude tier
(--claude-tier) and codex catalogEntries (--codex), then syncs. model
remove reverses it, refusing tier-referenced / discovered / local /
functional slugs. --dry-run plans without writing; AI_LITELLM_SKIP_SYNC
gates the proxy reload for offline testing.

Spec: docs/superpowers/specs/2026-07-04-ai-litellm-refactor-design.md §9

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

### Task 5: 문서 스윕 + 커밋

**Files:** `README.md`(Maintenance Boundary / 모델 추가 절차를 `model add` 한 줄로 갱신 + 수동 절차는 보조로), `docs/AI_AGENT_LITELLM_ARCHITECTURE.md`(모델 추가 절차 절 + 명령어 체계 Model 줄 + 새 dated 로그), `docs/APPLYING_MODELS_TO_HARNESSES.md`(레시피에 `model add` 경로 추가).

- [ ] 명령어 체계/usage에 add/remove 반영; "모델 추가 절차"를 `ai-litellm model add <id> --claude-tier <tier> --codex` 우선 + 수동 YAML 편집은 대안으로. dated 로그 항목 추가(P6: 카탈로그 셀프서비스). 커밋 `docs: document model add/remove self-service`.

### Task 6: 머신 반영 + 최종 게이트 (실 registry 무변경)

**Files:** 없음(운영)

- [ ] `./scripts/install.zsh`(명령 추가만 — sync/proxy 재시작·실 registry mutate **하지 않음**). 검증: `ai-litellm model add --help` 또는 인자 없는 usage가 add/remove 노출; **`ai-litellm model add z-ai/glm-5.2 --name TmpDup-openrouter --dry-run`**(실 OpenRouter 페치 1회 — 무과금 /models, 무쓰기)이 계획 출력; `ai-litellm model remove Test-Nonexistent`가 loud-fail. **실제 add/remove(쓰기)는 실행하지 않는다** — 사용자가 직접 쓸 기능이므로 dry-run까지만 검증.
- [ ] 수용 기준(§9): dry-run 계획 정확 / add→doctor→remove 왕복은 check(throwaway)에서 검증됨 / 신규 모델이 `--claude-tier`·`--codex`로 즉시 실행 가능(문서화). 기록.
