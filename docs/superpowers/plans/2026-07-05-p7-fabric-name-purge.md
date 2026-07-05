# P7: fabric 이름 완전 purge + 설치본 마이그레이션 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 설치 패키지를 `~/.local/share/ai-litellm`으로 통일한다 — 경로·내부 env(`AI_LITELLM_FABRIC_HOME`→`AI_LITELLM_HOME`)·렌더 토큰(`__FABRIC_HOME__`→`__AI_LITELLM_HOME__`)에서 "fabric"을 완전히 제거하고, 실 설치본을 옛 경로에서 새 경로로 마이그레이션한다. (사용자 확정: 완전 purge.)

**Architecture:** 순수 기계적 rename(3개 문자열) + 1개 semantic 지점(rendered-path 가드의 마커 조각 조립) + 실 설치 마이그레이션. RED-first 부적합(새 동작 아님) — 대신 **rename을 소스+check.zsh에 lockstep으로 적용 후 green 게이트**(throwaway 설치가 새 경로/토큰으로 end-to-end 통과)로 검증. functional 파일만; historical plans/specs는 작성-당시 기록으로 보존.

**Tech Stack:** zsh(lib/install/uninstall/check/shell), JSON harness descriptors(`__FABRIC_HOME__` 토큰), Python callback, config 렌더링.

**결정 근거:** 사용자 지시 2026-07-05 ("설치 패키지 디렉토리 ai-litellm으로 통일, 완전 purge: env·토큰까지").

## 3개 rename 대상 (functional 파일만)

| 문자열 | → | functional 파일 |
|---|---|---|
| `ai-litellm-fabric` | `ai-litellm` | bin/{ai-litellm,claude-litellm,codex-litellm}, config/{claude,codex}-litellm/shell.zsh, config/ai-litellm/lib.zsh, config/ai_litellm_callbacks/__init__.py, scripts/{install,uninstall,check}.zsh, .gitignore + 현행 docs(README/ARCHITECTURE/RATIONALE) |
| `AI_LITELLM_FABRIC_HOME` | `AI_LITELLM_HOME` | bin/* (3), config/{claude,codex}-litellm/shell.zsh, lib.zsh, install/uninstall/check.zsh + README |
| `__FABRIC_HOME__` | `__AI_LITELLM_HOME__` | lib.zsh, harnesses/{claude,codex}.json, install/check.zsh, .gitignore + docs(ARCHITECTURE/RATIONALE) |

**주의**: `config/ai-litellm/`(내부 dir)·`AI_LITELLM_CONFIG_HOME`/`_STATE_HOME`/`_BIN_DIR`(파생 env, "FABRIC" 없음)·`ai_litellm_*` 함수·`AI_LITELLM_*` 기타 env는 **불변**. `ai-litellm-fabric`→`ai-litellm` 치환은 `config/ai-litellm/`을 건드리지 않는다(문자열이 다름).

## Global Constraints

- **historical 보존**: `docs/superpowers/plans/*`·`docs/superpowers/specs/*`는 작성-당시 상태 기록이므로 **건드리지 않는다**(dated 로그와 동급). `docs/FABRIC_REPORT.html`은 untracked scratch — 무시.
- **semantic 지점 (유일)**: `ai_litellm_assert_rendered_path`(lib.zsh ~L714-722)의 마커는 install.zsh 렌더가 가드 자신을 치환하지 못하도록 **조각으로 조립**된다(`local us="__"; fabric_marker="${us}FABRIC_HOME${us}"`). 이 조각의 `FABRIC_HOME`→`AI_LITELLM_HOME`으로 바꿔 새 토큰 `__AI_LITELLM_HOME__`을 조립하게 한다. install.zsh의 렌더 substitution에도 유사 조각이 있으면 함께. **blind 전역치환이 이 조각을 놓치므로 수동 확인.**
- lib/shell 임베디드 single-quoted 블록에 **어포스트로피 금지**; 편집 후 **clean-room 함수테이블 스모크**.
- 이전 P5 커밋(b9469ec)이 README에 넣은 "패키지 디렉토리는 역사적 이름 ai-litellm-fabric 유지" **노트를 제거**한다(이제 통일하므로 그 서술이 틀림). RATIONALE §5의 같은 노트도.
- 커밋은 check green에서만. 검증 pipefail-safe. Co-Authored-By 트레일러. 디렉토리 가드.

---

### Task 1: 전역 rename + 가드 조각 + 현행 docs + green 게이트 + 단일 커밋

**Files:** bin/{ai-litellm,claude-litellm,codex-litellm}, config/claude-litellm/shell.zsh, config/codex-litellm/shell.zsh, config/ai-litellm/lib.zsh, config/ai_litellm_callbacks/__init__.py, config/ai-litellm/harnesses/claude.json, config/ai-litellm/harnesses/codex.json, scripts/install.zsh, scripts/uninstall.zsh, scripts/check.zsh, .gitignore, README.md, docs/AI_AGENT_LITELLM_ARCHITECTURE.md, docs/DESIGN_RATIONALE.md

- [ ] **Step 1: 3개 전역 치환 (functional + 현행 docs만; superpowers/* 제외)**
  각 대상 파일에서 `ai-litellm-fabric`→`ai-litellm`, `AI_LITELLM_FABRIC_HOME`→`AI_LITELLM_HOME`, `__FABRIC_HOME__`→`__AI_LITELLM_HOME__`. **superpowers/plans·specs와 FABRIC_REPORT.html은 절대 제외.** (sed -i 대상 목록을 명시적으로 나열; 디렉토리 전역 sed 금지.)
- [ ] **Step 2: 가드 조각 수동 확인/수정 (lib.zsh ~L718-722)**
  `fabric_marker="${us}FABRIC_HOME${us}"` → `fabric_marker="${us}AI_LITELLM_HOME${us}"`. 변수명 `fabric_marker`는 그대로 둬도 무방(내부 로컬)하나, 명확성 위해 `token_marker` 등으로 바꿔도 됨(선택). install.zsh에 유사 조각-조립 마커가 있으면(grep `us}FABRIC\|FABRIC_HOME${`) 함께. **`__AI_LITELLM_HOME__` 리터럴이 소스에 직접 나타나면 install.zsh 렌더가 그걸 치환하려 하므로, 가드/렌더 로직의 마커는 반드시 조각으로.**
- [ ] **Step 3: b9469ec의 "역사적 이름 유지" 노트 제거**
  README의 "(The installed package directory keeps its historical name ... only the repository is named ai-litellm.)" 블록 삭제(또는 "설치 경로·env·토큰까지 ai-litellm으로 통일" 서술로 대체). RATIONALE §5의 대응 노트도.
- [ ] **Step 4: 잔존 확인**
  `grep -rn "ai-litellm-fabric\|AI_LITELLM_FABRIC_HOME\|__FABRIC_HOME__" bin/ config/ scripts/ README.md docs/AI_AGENT_LITELLM_ARCHITECTURE.md docs/DESIGN_RATIONALE.md` → **0** (superpowers/*·FABRIC_REPORT.html 제외). 어떤 잔존이든 판정.
- [ ] **Step 5: clean-room + 문법**
  `zsh -n` (lib.zsh, install.zsh, uninstall.zsh, check.zsh, 2 shell.zsh); `zsh -f -c 'source config/ai-litellm/lib.zsh >/dev/null 2>&1; whence -f ai_litellm ai_litellm_cmd_model ai_litellm_sync ai_litellm_assert_rendered_path >/dev/null && echo LIB_OK'`; `zsh -f -c 'source config/claude-litellm/shell.zsh >/dev/null 2>&1; whence -f claude-litellm >/dev/null && echo CLAUDE_OK'`; `zsh -f -c 'source config/codex-litellm/shell.zsh >/dev/null 2>&1; whence -f codex-litellm codex-litellm-refresh-catalog >/dev/null && echo CODEX_OK'`; JSON: `jq . config/ai-litellm/harnesses/claude.json config/ai-litellm/harnesses/codex.json >/dev/null`; 어포스트로피 스윕.
- [ ] **Step 6: 전체 check GREEN** — throwaway 설치가 새 경로 `~/.local/share/ai-litellm`/새 토큰으로 end-to-end 통과. check.zsh 자체가 `AI_LITELLM_FABRIC_HOME`/경로/`__FABRIC_HOME__` 단언을 갖고 있으므로 Step 1이 그것들도 새 이름으로 바꿨어야 함. 실패 시 잔존/가드 문제 → 해당 스텝 복귀(BLOCKED). `./scripts/check.zsh > /tmp/p7.log 2>&1; rc=$?; tail -25 /tmp/p7.log; echo "exit=$rc"`.
- [ ] **Step 7: 단일 커밋**
```
refactor!: unify install package to ~/.local/share/ai-litellm (purge fabric name)

Rename the install package directory ai-litellm-fabric -> ai-litellm and
purge the fabric name from internal plumbing: AI_LITELLM_FABRIC_HOME ->
AI_LITELLM_HOME and the render token __FABRIC_HOME__ -> __AI_LITELLM_HOME__
across bin shims, launchers, lib, installer, callback, harness descriptors,
check, and the current guides. The rendered-path guard reassembles the new
token from fragments (unchanged mechanism). Historical plan/spec docs keep
their as-of-then names. Existing installs migrate via reinstall (next task).
Internal config/ai-litellm/ dir, ai_litellm_* functions, and the derived
AI_LITELLM_{CONFIG,STATE,BIN}_* envs are unchanged.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

### Task 2: 실 설치본 마이그레이션 (옛 경로 → 새 경로)

**Files:** 없음 (운영). **proxy 재시작 포함 — 활성 세션 없음이 사용자 확인됨(2026-07-05).**

- [ ] **Step 1: 옛 설치본 상태 기록** — `ls ~/.local/share/ai-litellm-fabric`; 옛 proxy pid(`~/.local/share/ai-litellm-fabric/state/ai-litellm/litellm.pid` 있으면).
- [ ] **Step 2: 새 경로로 설치** — `./scripts/install.zsh 2>&1 | tail -8` (prefix 기본값이 이제 `~/.local/share/ai-litellm`; 모든 config를 새 경로/토큰으로 렌더, shim은 새 lib 경로를 가리킴).
- [ ] **Step 3: sync (새 경로 proxy 기동 + 옛 proxy 정리)** — 옛 proxy가 옛 경로에서 돌고 있으면 먼저 `~/.local/share/ai-litellm-fabric/scripts/uninstall.zsh` 또는 옛 proxy stop이 필요. **순서**: (a) 새 install 후 `ai-litellm proxy stop`이 어느 pid를 보는지 확인(새 경로 pid 파일은 아직 없음). 옛 proxy는 옛 경로 pid로 돌므로, `kill` 대신 옛 경로의 `ai-litellm proxy stop`을 옛 shim... 하지만 shim은 새 lib로 갱신됨. **가장 안전**: 새 `ai-litellm sync`(새 경로 proxy 기동) → 그다음 옛 proxy를 옛 pid로 정리(옛 경로 pid 파일의 pid를 `kill`, litellm 프로세스인지 `ps`로 확인 후). 포트 4000 충돌 주의 — 옛 proxy를 먼저 내리고 새로 올린다. 구현자는 이 순서를 신중히: **옛 proxy stop → 옛 dir 제거 → 새 install/sync → 새 proxy 기동 → doctor green**.
- [ ] **Step 4: 옛 dir 제거** — 새 설치·sync·doctor green 확인 후 `rm -rf ~/.local/share/ai-litellm-fabric`. (keychain 시크릿은 경로 독립 — 안전. 옛 state의 logs/pid/catalog는 재생성물.)
- [ ] **Step 5: 검증** — `ls ~/.local/share/ | grep ai-litellm`(새 것만); `ai-litellm status`(매핑 정상); `ai-litellm doctor --proxy`(config currency 포함 green); `command -v ai-litellm`이 새 shim; 옛 경로 부재.
- [ ] **Step 6: 수용 기준** — 새 경로 단일 존재 / status·doctor green / 옛 경로·옛 proxy 제거 / 명령 정상.
