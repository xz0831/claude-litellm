# P4: 명령 표면 슬리밍 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `ai-litellm`의 명령 표면을 스펙 §7의 목표 트리로 슬리밍한다 — route→model 흡수, audit/capabilities/per-group doctor 동사/deprecated flat alias 일몰, 런처 deprecated 플래그·key-status bin shim 제거 — 그리고 P3 최종리뷰 carry-over 4건을 처리한다.

**Architecture:** P1-P3 패턴 그대로: check RED flip → lib.zsh 본체 → 런처/bin/인스톨러 → carry-over → budget 재앵커 + 전체 check GREEN 단일 커밋 → docs → 머신 반영(install만; sync/proxy 재시작 불요 — 명령 표면 변경뿐이라 proxy 소비물 무변).

**Tech Stack:** zsh (lib.zsh 5,825줄, 런처 shell.zsh), Python(budget), docs.

**Spec:** `docs/superpowers/specs/2026-07-04-ai-litellm-refactor-design.md` §7 + P3 carry-over (원장 `.superpowers/sdd/p3-lineup/progress.md` 말미)

## 목표 트리 (스펙 §7 — 이것이 전부)

```
ai-litellm status                          # 기존 (P2)
ai-litellm sync      [--dry-run|--no-restart]
ai-litellm doctor    [--proxy|--context|--reasoning|--policy|--runtime <n>]
ai-litellm proxy     status|start|stop|restart|logs [n]
ai-litellm model     list|info [m]|limits [m]|probe [m...]|refresh-capabilities|reasoning set/unset/probe
ai-litellm context   matrix [f]|probe <surface|all>|observations [f]
ai-litellm reasoning matrix [m]|probe <m> [effort]
ai-litellm harness   list|info <n>|launch <n> …|alias get/set|reasoning [set/unset]
ai-litellm runtime   list|status [n]
ai-litellm key       status|set
ai-litellm uninstall
```

(`model add`/`remove`는 P6에서 이 트리의 model 그룹에 추가된다.)

## Global Constraints

- 라인 번호는 커밋 `7a03f23` 기준 앵커. 어긋나면 앵커 텍스트로.
- 삭제 시 **callee caller-count 재스윕** (P2/P3 습관) — 새 고아 금지.
- lib.zsh/check.zsh/shell.zsh의 single-quoted 임베디드 블록에 **어포스트로피 금지**; 편집 후 **clean-room 함수테이블 스모크**(zsh -f source + whence -f) 필수.
- 검증 pipefail-safe. 커밋은 check green에서만. Co-Authored-By 트레일러.
- 디렉토리 가드: 모든 서브에이전트는 편집 전 `git rev-parse --show-toplevel`이 워크트리(p4-slim)로 끝나는지 확인.

---

### Task 1: check.zsh 기대값 뒤집기 (RED)

**Files:** Modify `scripts/check.zsh`

- [ ] **Step 1: deprecated flat alias 일몰 단언** — 기존에 flat alias의 *작동*을 단언하는 체크가 있으면(grep `ai_litellm_deprecated`·`route-info`·flat 사용 예) 제거를 반영해 뒤집는다. 새 단언 추가(설치본 스모크 영역):

```zsh
if "$HOME/.local/bin/ai-litellm" start >/dev/null 2>&1; then echo "FAIL: retired flat alias still dispatches"; exit 1; fi
if "$HOME/.local/bin/ai-litellm" capabilities >/dev/null 2>&1; then echo "FAIL: retired capabilities still dispatches"; exit 1; fi
if "$HOME/.local/bin/ai-litellm" route list >/dev/null 2>&1; then echo "FAIL: retired route group still dispatches"; exit 1; fi
if "$HOME/.local/bin/ai-litellm" audit model-policy >/dev/null 2>&1; then echo "FAIL: retired audit group still dispatches"; exit 1; fi
echo "ok: retired surfaces loud-fail (P4 slimming)"
```

- [ ] **Step 2: 이전 표면을 쓰는 기존 체크 재키잉** — `route probe`/`route list`/`route info`/`audit model-policy`/`proxy doctor`/`context doctor`/`reasoning doctor`/`runtime doctor`/`capabilities`를 호출하는 모든 체크 줄을 grep으로 전수 찾아 새 표면으로 치환: `route probe`→`model probe`, `route list --json`→`model list --json`(스키마 확장 후), `audit model-policy`→`doctor --policy`, per-group doctor 호출→`doctor --<scope>`(단, lib 내부 함수 직접 호출(`ai_litellm_context_doctor` 등)은 유지 — 사용자 표면 호출만 교체), `capabilities`→`status` 경유 단언으로. H4/H5/H6 라벨 체크(usage 문구 단언)는 새 usage에 맞게 갱신.
- [ ] **Step 3: model list --json 백엔드 필드 단언 추가** — route list --json이 죽는 대신 model list --json 항목에 `backend` 키가 생기는 것을 단언(T2에서 구현).
- [ ] **Step 4: 런처 deprecated 플래그 부재 단언** — `claude-litellm --start`/`codex-litellm --doctor`가 loud-fail(비-0 + 안내)하는 단언; `--list`/`--status`/`--refresh-catalog` 생존 단언 유지·확인. `codex-litellm --route-info` 부재 단언.
- [ ] **Step 5: RED 확인** — pipefail-safe 전체 실행; 첫 실패는 Step 1-4 중 가장 이른 새 단언(구 표면이 아직 작동하므로). 커밋 금지.

### Task 2: lib.zsh 슬리밍 본체

**Files:** Modify `config/ai-litellm/lib.zsh`

- [ ] **Step 1: route→model 흡수** — `ai_litellm_cmd_route` 삭제, `route)` 디스패치 삭제. `ai_litellm_cmd_model`에 `probe` 동사 추가(`ai_litellm_probe_routes` 위임 — H6 의식적 역전, 주석 기록). `ai_litellm_list`/`ai_litellm_list_json`에 backend 컬럼/키 추가(`route list`가 보여주던 model_name→backend 매핑 통합; `ai_litellm_route_list_json` 본문을 재사용해 model list json에 병합하거나 대체 — 재파생 금지 원칙 유지). `route info`는 `model info`와 수렴 완료(M21)이므로 함수 `ai_litellm_route_info`는 다른 호출자 grep 후 무호출이면 삭제.
- [ ] **Step 2: audit/capabilities 삭제** — `ai_litellm_cmd_audit`+`audit)` 삭제(`ai_litellm_audit_model_policy` 내부 함수는 doctor --policy가 사용 — 유지 확인). `capabilities|--capabilities)` 디스패치 삭제; `ai_litellm_capabilities` 함수는 **status가 소비하므로 유지**.
- [ ] **Step 3: per-group doctor 동사 제거** — cmd_proxy/cmd_runtime/cmd_context/cmd_reasoning의 `doctor` 브랜치를 제거하고 usage 오류로 유도(`doctor --<scope>` 안내). 내부 doctor 함수들(unified doctor가 위임)은 유지.
- [ ] **Step 4: deprecated flat alias 13개 + `ai_litellm_deprecated` 헬퍼 + `start-litellm`/`stop-litellm`/`openrouter-key-status`/`litellm-master-key-status` 함수 삭제** — 각 삭제 전 callee 재스윕(예: `ai_litellm_start` 등은 proxy 그룹이 계속 사용 — 함수는 남고 alias만 죽는다; `ai_litellm_route_info`류는 Step 1 판정에 따름). usage의 "Flat forms ..." 문단·Route/Audit/Capabilities 줄 삭제, Model 줄에 probe 추가, Doctor 줄 유지.
- [ ] **Step 5: `model capabilities` 동사 판정** — cmd_model의 `capabilities` 브랜치 존재 확인; 출력이 `model info`/`status`와 중복이면 삭제, 유일 컬럼이 있으면 `model limits`로 이관 후 삭제 (스펙 §7 판정 규칙; 근거를 보고서에).
- [ ] **Step 6: 검증** — zsh -n; clean-room 함수테이블 스모크(ai_litellm/cmd_status/cmd_model/cmd_doctor/sync/launch 등); `grep -c "cmd_route\|cmd_audit\|ai_litellm_deprecated" ` == 0; 어포스트로피 스윕.

### Task 3: 런처/bin/인스톨러

**Files:** Modify `config/claude-litellm/shell.zsh`, `config/codex-litellm/shell.zsh`; Delete `bin/openrouter-key-status`, `bin/litellm-master-key-status`; Modify `scripts/install.zsh`(shim 루프 2곳에서 두 이름 제거 + retired-shim legacy cleanup 함수 추가), verify-only `scripts/uninstall.zsh`(legacy 목록 유지)

- [ ] claude-litellm/codex-litellm의 deprecated `--start|--stop|--restart|--logs|--doctor` 브랜치 삭제(경고문 포함), `--list`/`--status` 유지, codex `--refresh-catalog` 유지, codex `--route-info` 삭제(+`codex-litellm-route-info` 함수 callee 재스윕). P3 carry-over 동시 처리: shell.zsh L134/L171의 dead `gpt-5.5` default-fallback 리터럴을 loud-fail(`echo "codex descriptor missing models.default" >&2; return 1`)로 교체.
- [ ] `remove_retired_key_status_shims()` — goose/opencode/dash 패턴으로 두 shim(+백업) 제거, dash_router 함수 뒤에 배치.
- [ ] 검증: zsh -n 3파일, clean-room 스모크(codex-litellm 함수테이블), 어포스트로피 스윕, install.zsh grep(잔존은 cleanup 함수 내부만).

### Task 4: carry-over — owned-policy warn 일반화 + budget 라벨 + generator visibility

**Files:** Modify `config/ai-litellm/lib.zsh`(:5364-5383 부근 GLM-키 warn), `docs/AI_AGENT_LITELLM_ARCHITECTURE.md`(L378 doctor bullet 짝 — T6 docs가 아니라 여기서 코드와 동시 수정), `scripts/verify_budget_consistency.py`(L1-L3 라벨), `config/codex-litellm/shell.zsh`(append 경로 visibility 스탬프)

- [ ] warn을 GLM 하드코딩에서 `x_output_confidence == "owned-policy"`인 모든 앵커로 일반화(현재 해당: mimo_v25, qwen 로컬들) — 문구도 일반형으로; ARCHITECTURE L378 bullet을 새 동작 서술로 갱신.
- [ ] budget ROWS L1-L3 라벨에서 은퇴 이름 제거(체제 서술형으로: "Large 1M/384000" 등) + `dict(id="L5", regime="Kimi-K2.7-Code exact (cap<reservation)", ctx=262144, cap=16384, families="A,B,D")` 행 추가(28행 — 스크립트 출력의 행 수 인용도 갱신; RATIONALE §4a "27행" 언급은 T6 docs에서).
- [ ] generator append 경로에 `visibility: entry.visibility ?? base.visibility ?? null` 스탬프(번들 소실 시 hidden 엔트리가 picker에 노출되는 것 방지).
- [ ] 검증: budget 스크립트 단독 실행(28행 green), zsh -n + clean-room, ARCHITECTURE 해당 절 diff 확인.

### Task 5: budget 재앵커 + 전체 check GREEN + 단일 커밋

- [ ] T2/T4 삭제·삽입으로 밀린 RUBY_MAT/RUBY_RES 재앵커(가드 loud-fail 유도→갱신→green; NODE/RUBY_CAT는 편집 위치 확인 후 판단).
- [ ] 전체 check green(pipefail-safe) — 실패 시 BLOCKED 보고(단언 약화 금지).
- [ ] `git add -A` 후 스테이징 목록 확인, 커밋:

```
feat!: slim the command surface to the target tree

Absorb route into model (probe returns to model — conscious H6
reversal; list gains the backend column), retire audit/capabilities/
per-group doctor verbs, sunset all deprecated flat aliases and launcher
lifecycle flags, remove the standalone key-status shims. Folds P3
carry-overs: owned-policy warn generalized by confidence label, budget
matrix relabeled + exact Kimi-K2.7-Code clamp row, generator visibility
stamp, dead default fallbacks now loud-fail.

Spec: docs/superpowers/specs/2026-07-04-ai-litellm-refactor-design.md §7

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

### Task 6: 문서 스윕 + 커밋

- [ ] README(machine-readable 목록에서 route list --json→model list --json backend 키 서술, 명령 예시 정리), ARCHITECTURE(명령어 체계 블록을 목표 트리로 재작성, H4/H5/H6/M21/M6 결정 로그 보존 + 새 dated 로그 항목: 슬리밍 내역+H6 역전+"deprecated alias 일몰 기준 없음(§9.8)" 해소, 확인 명령 예시 갱신), RATIONALE(§9.8 항목 해소 표기, §4a 27→28행, §8 표의 H6/H5 행 갱신), APPLYING(명령 예시 grep 정리).
- [ ] 검증: `grep -rEn "ai-litellm (route|audit|capabilities)\b|--route-info|key-status" README.md docs/*.md` 잔존 전수 판정(결정 로그만 허용). 커밋 `docs: slim command surface across guides (decision logs preserved)`.

### Task 7: 머신 반영 + 최종 게이트

- [ ] `./scripts/install.zsh`(sync/proxy 재시작 불요 — 명령 표면만) → shim 2개 제거 확인, `ai-litellm route list`/`capabilities`/`start` loud-fail, `ai-litellm model probe Qwen3.6-27B-omlx`(무과금 로컬) 동작, `ai-litellm status` 정상, usage가 목표 트리와 일치.
- [ ] 수용 기준(스펙 §7): usage==목표 트리 / 제거 명령 loud-fail / check green / README·ARCHITECTURE 재작성 ✓ 기록.
