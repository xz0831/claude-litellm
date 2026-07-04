# 리팩토링 설계 — 슬리밍 + Codex 실명화 + 라인업 교체 (2026-07-04)

승인 상태: 사용자 승인 완료 (브레인스토밍 세션 2026-07-04, 섹션별 승인).
관련 문서: [AI_AGENT_LITELLM_ARCHITECTURE.md](../../AI_AGENT_LITELLM_ARCHITECTURE.md), [DESIGN_RATIONALE.md](../../DESIGN_RATIONALE.md), [APPLYING_MODELS_TO_HARNESSES.md](../../APPLYING_MODELS_TO_HARNESSES.md)

## 1. 배경과 목표

이 프로젝트의 정체성은 "자체 연결만 제공하는 Claude Code/Codex에 외부 모델(OpenRouter·직결 provider·로컬 엔진)을 연결하되, native와 환경(세션/스킬/플러그인 공유 경계)을 하나처럼 유지하고 provider만 바꿀 수 있게 하는 것"이다. 실사용 관찰 결과 네 가지 드리프트가 확인되었다:

1. **OpenCode 지원은 부가가치가 없다** — OpenCode는 자체적으로 API/로컬 모델 연결이 일급 기능이라 fabric 래핑이 해결하는 문제가 없다.
2. **Codex의 gpt-* facade는 혼동 비용이 호환 이득을 넘었다** — "gpt-5.4가 사실은 Kimi"는 사용자 실사용에서 헷갈리기만 하며, 문서끼리도 매핑이 어긋나는 지경이다(README는 gpt-5.4=DeepSeek, ARCHITECTURE 예시는 gpt-5.4=Kimi). RATIONALE §3이 스스로 예고한 재론 조건이 UX 근거로 발동되었다. 로컬 모델(`Gemma4-12B-omlx`)이 실명 커스텀 엔트리로 이미 동작 중이므로 기술 토대는 존재한다.
3. **TUI(fabric 대시보드)는 원래 목적(모델 카탈로그 관리)을 달성하지 못했다** — 실제로는 read-only 관측 + router 실행 front-end가 되었고, 사용하기 불편하다. `ai-litellm router` JSON 표면도 호출자가 없다.
4. **명령 표면이 과잉이다** — deprecated flat alias가 일몰 없이 영생 중이고(RATIONALE §9.8), route/model·audit/doctor·capabilities 등 겹치는 명사가 많다.

**목표**: 지원 대상을 Claude Code + Codex 둘로 좁히고, Codex 모델 표면을 실명(`<모델>-<프로바이더>`)으로 통일하고, TUI를 제거하고 CLI를 슬리밍하며, 모델 라인업을 교체하고, 카탈로그 관리를 명령 한 줄로 만든다. repo 이름은 `ai-litellm`으로 단순화한다.

## 2. 확정 결정 요약

| 축 | 결정 |
|---|---|
| OpenCode | 지원 종료. goose 은퇴(2026-06-28) 패턴 재사용. 기존 세션 데이터 삭제 승인됨 |
| TUI | 완전 제거. `ai-litellm status` 한 방 요약 명령으로 대체. `--json` read 표면은 유지 |
| router CLI | 제거 (router_core + `ai-litellm router` — 호출자 없음) |
| Codex | gpt-* facade 완전 제거 → 실명 카탈로그 엔트리. `codex-auto-review`만 유지(사용 중 확인) + Kimi-K2.7-Code로 재지정 |
| 모델 라인업 | fable=Kimi-K2.7-Code, opus=GLM-5.2, sonnet=Mimo-V2.5, haiku=Qwen3.6-27B(omlx). Codex도 같은 4개 노출 |
| 명령 표면 | 공격적 슬리밍: deprecated alias 일몰 + route→model 통합 + doctor 단일화 등 (§7) |
| 이름 | repo → **ai-litellm**. 허브 명령·내부 식별자·패키지 디렉토리·런처명은 전부 현행 유지 |
| 카탈로그 셀프서비스 | `model add`/`model remove` 신설 — 이번 라운드 포함 |

## 3. 진행 구조 (접근 A: 단계별 브랜치)

각 Phase는 독립 브랜치에서 진행하고, **check.zsh + CI가 green인 상태로만 다음 Phase로** 넘어간다. 삭제(P1·P2)가 먼저라 이후 Phase의 대상 자체가 줄어들고, 개명(P5)은 순수 메타데이터라 뒤에 둔다.

```
P1 opencode 은퇴 → P2 TUI/router 제거 → P3 라인업+codex 실명화 → P4 명령 슬리밍 → P5 repo 개명 → P6 model add/remove
```

budget 차분테스트(`verify_budget_consistency.py`)는 lib.zsh를 라인 범위로 슬라이스하므로 삭제/수정마다 loud-fail한다 — 각 Phase에서 라인 범위를 재앵커한다(조용히 깨질 수 없는 구조이므로 안전).

## 4. Phase 1 — OpenCode 은퇴

goose 은퇴 패턴 그대로:

- **삭제**: `bin/opencode-litellm`, `config/ai-litellm/harnesses/opencode.json`, lib.zsh의 opencode 어댑터 분기(launch/sync 생성/context surface/reasoning 규칙, 약 39곳), check.zsh의 opencode 단언(7곳).
- **install.zsh**: `remove_retired_goose_support`와 나란히 `remove_retired_opencode_support` 추가 — 기존 설치본의 shim(+백업), descriptor, `state/opencode-litellm`(세션 sqlite 포함, 삭제 승인됨)을 install 시점에 정리. uninstall.zsh의 legacy shim 목록에는 opencode-litellm 유지(과거 설치본 제거용).
- **문서**: ARCHITECTURE 결정 로그에 은퇴 항목("2026-07-04 opencode 지원 종료 — opencode는 자체 API/로컬 모델 연결이 일급 기능이라 래핑의 부가가치 없음"), 실행 경로 표 6→5, harness 표·토큰 예산 표에서 opencode 행 제거, RATIONALE §6 opencode 항목을 goose처럼 retired 한 줄로.

**수용 기준**: check.zsh green / `ai-litellm harness list`에 claude·codex만 / 재설치 시 기존 opencode 설치물이 정리됨 / native 디렉토리 무접촉(기존 check가 보장).

## 5. Phase 2 — TUI + router 제거, `status` 신설

- **삭제**: `config/ai-litellm/fabric_dash/` 전체(모듈 15개+테스트), `config/ai-litellm/router_core/` 전체(+테스트), `config/ai-litellm/conftest.py`(두 패키지 전용), `bin/fabric`, lib.zsh의 `dash`·`router` 디스패치, install.zsh의 `ensure_dash_venv`+`AI_LITELLM_SKIP_DASH_VENV`, CI `dash-tests` 잡. 기존 설치본의 `state/dash-venv`·`bin/fabric` shim은 install-시점 legacy cleanup으로 제거.
- **유지**: `--json` read 표면 — formatter-only라 유지비가 거의 없고 스크립팅 가치가 있다. TUI가 죽어도 계약은 유효하다.
- **신설 `ai-litellm status`**: 기존 read 함수를 재사용(상태 재파생 금지 — `--json`과 같은 원칙)하는 한 방 텍스트 요약: proxy 건강/config currency, Claude tier→모델 매핑, Codex default, runtime 상태, key 상태, 등록 모델 수. 기존 `capabilities` 출력 내용을 흡수한다(명령 자체 제거는 P4). `status --json`은 기존 emitter들을 한 객체로 합성.
- **이름 재활용 주의**: `status`는 P4 전까지 "deprecated flat alias→proxy status"와 공존할 수 없으므로, P2에서 flat `status` alias만 선행 제거하고 새 의미로 재배정한다. 새 status의 첫 줄이 proxy 건강이므로 근육기억 충돌은 실질 무해.
- **문서**: README Dashboard 절 삭제, `docs/FABRIC_DASHBOARD.md` 삭제, RATIONALE §6a를 retired 기록으로 대체, ARCHITECTURE의 dash/`--json` 절 갱신.
- 2026-06-21자 미커밋 WIP(auto-refresh 회귀 테스트 + RATIONALE 해결 문단)는 TUI와 함께 소멸하는 내용이므로 커밋 없이 드롭했다(승인됨, 2026-07-04 실행 완료).

**수용 기준**: check.zsh green / CI에서 dash-tests 잡 부재 / `ai-litellm status`가 proxy·tier·key·runtime 요약 출력 / `fabric`·`ai-litellm dash`·`ai-litellm router` 모두 부재.

## 6. Phase 3 — 모델 라인업 교체 + Codex 실명 카탈로그

### 6.1 새 라인업 (Claude tier = 정본 축)

| Tier | 모델 | proxy alias (surface name) | direct alias (OpenRouter 원형 id) |
|---|---|---|---|
| fable | Kimi-K2.7-Code | `Kimi-K2.7-Code-openrouter` (신규 앵커) | `moonshotai/kimi-k2.7-code` (추정) |
| opus | GLM-5.2 | `GLM-5.2-openrouter` (기존) | `z-ai/glm-5.2` (기존) |
| sonnet | Mimo-V2.5 | `Mimo-V2.5-openrouter` (신규 앵커) | `xiaomi/mimo-v2.5` (추정) |
| haiku | Qwen3.6-27B (local) | `Qwen3.6-27B-omlx` (기존, thinking-off 자격검증 완료) | 로컬 불가 → `xiaomi/mimo-v2.5` 폴백 |

- **구현 첫 단계에서 OpenRouter `/models` 정본으로 신규 슬러그·한도를 확인**하고 앵커를 provider-confidence로 시딩한다("추정" 표기는 이 확인 단계로 해소). 해당 모델이 OpenRouter에 없으면 작업을 멈추고 사용자에게 대안을 묻는다.
- `directDisplayNames`/`displayNames`는 기존 컨벤션(`<모델> (<프로바이더>)`) 자동 파생.
- `subagentModel`(direct 전용 품질 핀, 현재 DeepSeek)은 opus 동행 원칙으로 `z-ai/glm-5.2`로 갱신.
- **퇴역**: `Kimi-K2.6-openrouter`(K2.7-Code로 대체), `DeepSeek-V4-Pro-openrouter`(라인업 이탈), `Gemma4-12B-omlx` 영구 엔트리(haiku가 Qwen으로 이동; oMLX가 계속 서빙하면 discovered route로 자동 재등장하므로 손실 없음) + 대응 x-limits 앵커. `Qwen3.6-Test-27B-omlx`·`PlainLocal-omlx` 등 실험 잔재는 repo 전체 grep으로 참조가 없으면 삭제, 있으면 참조 지점과 함께 유지 사유를 기록. `Qwen3.6-35B-A3B-4bit-omlx`(Phase-0 replication arm)는 유지.
- **신규 클라우드 모델 검증**: Kimi-K2.7-Code·Mimo-V2.5는 harness 품질 미검증 → 교체 후 TRIV/DOMAIN 프로브(기존 per-model 자격검증 프로토콜)를 proxy 경유로 실행하고 결과를 기록한다. 실패 시 라인업 재조정은 alias 1줄이다.

### 6.2 Codex 실명 카탈로그

**원칙 전환**: "번들 슬러그 위장(clone-and-patch)" → **"카탈로그 = proxy가 실제 서빙하는 실명 모델의 거울"**. Claude picker와 Codex picker가 같은 네이밍을 쓰게 된다.

- **Registry**: `gpt-5.5`/`gpt-5.4`/`gpt-5.4-mini`/`gpt-5.2`/`gpt-5.3-codex` 라우트 5개 삭제. `codex-auto-review`는 유지하되 백엔드를 `moonshotai/kimi-k2.7-code`(코드 리뷰 기능 ← 코드 특화 모델)로 재지정 — Codex의 `review` 기능이 이 hidden 슬러그(번들 카탈로그 `visibility: "hide"` 실확인, codex 0.142.5)를 하드코딩 요청하며, 사용자가 이 기능을 사용 중임이 확인되었다.
- **Descriptor(`codex.json`)**: `models.default` = `GLM-5.2-openrouter`(opus급 + 현 기본 백엔드 GLM 연속성). `models.localCatalogEntries` → **`models.catalogEntries`로 일반화**(클라우드+로컬 통합 노출 목록): `Kimi-K2.7-Code-openrouter`, `GLM-5.2-openrouter`, `Mimo-V2.5-openrouter`, `Qwen3.6-27B-omlx`(defaultReasoningLevel: low). Claude tier와 1:1 대칭. "harness가 보는 카탈로그는 작고 안정적으로" 원칙은 유지하되 이름만 정직해진다. 표시명은 컨벤션 자동 파생 + per-entry override 필드 허용.
- **생성기 재설계**(`codex-litellm-refresh-catalog`): `codex debug models --bundled` 페치는 유지하되 역할을 **스키마 템플릿 소스**로 축소. **보존 규칙**: 번들 엔트리 중 registry에 라우트가 있는 슬러그만 생존(gpt-* 자동 탈락, `codex-auto-review` 자동 생존) — "카탈로그에 있는데 proxy가 모르는 유령 모델"이 구조적으로 불가능해진다. `catalogEntries` 각각을 base 템플릿(catalogBaseSlug)에서 clone해 append: context window는 기존 `catalog_context_map`(x-limits − outputReservation)으로 스탬프하되 facade→backend 간접층이 사라져 실명 직결로 단순화. 도구 capability 축소(apply_patch 삭제 등)·`auto_compact_token_limit=null`은 현행 유지. 휴면 `local-*` 자동 포함 Ruby 분기는 죽은 코드로 삭제.
- **Alias(`codex-litellm/settings.json`)**: gpt* 계열 전부 삭제 → `kimi`→Kimi-K2.7-Code-openrouter, `glm`→GLM-5.2-openrouter, `mimo`→Mimo-V2.5-openrouter, `qwen`→Qwen3.6-27B-omlx.

### 6.3 스파이크 (P3 step 0 — 생성기 배선 전 검증)

생성 카탈로그를 손으로 편집해 실명 클라우드 엔트리 1개(GLM, 라우트 기존재)를 넣고 확인한다:

1. `codex-litellm GLM-5.2-openrouter exec --skip-git-repo-check --sandbox read-only 'Reply with exactly OK'` (소액 과금)
2. picker/`/model`에 표시명·reasoning level이 뜨는지
3. gpt-* 엔트리를 뺀 임시 카탈로그에서 `review`가 동작하는지(카탈로그 참조가 확인되면 codex-auto-review 엔트리를 append 대상에 추가)

로컬 Gemma 선례가 exec 경로를 이미 증명하므로, 초점은 클라우드 엔트리의 picker UX·reasoning level·review 의존성이다. **폴백**: codex core가 비-gpt 클라우드 슬러그를 필수 경로에서 거부하면 표시명-만-실명화로 후퇴(문서화된 폴백일 뿐 목표 아님).

### 6.4 트레이드오프·문서

- 전환 전 codex-litellm 세션의 resume은 사라진 gpt-* 슬러그를 참조 → 구세션 resume 불가 수용(격리 세션은 소모품).
- RATIONALE §3 facade 결정을 날짜 있는 대체 항목으로 갱신(원문의 재론 조건이 UX 근거로 발동됨을 명기). ARCHITECTURE "Codex Model Catalog"·"모델 추가 절차 step 3"·README facade 문단·APPLYING_MODELS §3 재작성.
- check.zsh 매핑 단언과 budget 차분테스트 27행 매트릭스를 실명 슬러그로 재키잉.

**수용 기준**: 스파이크 결과 기록 / sync 후 `codex-litellm --list`가 실명 4개(+auto-review, 카탈로그 참조가 확인된 경우)만 표시 / `codex-litellm glm exec … 'Reply with exactly OK'` 통과 / `claude-litellm haiku -p …`가 로컬 Qwen으로 응답 / TRIV/DOMAIN 프로브 기록 / `ai-litellm doctor` 전 배터리 green.

## 7. Phase 4 — 명령 표면 슬리밍

**목표 트리** (이것이 전부):

```
ai-litellm status                          # 신설: 한 방 요약 (+ --json)
ai-litellm sync      [--dry-run|--no-restart]
ai-litellm doctor    [--proxy|--context|--reasoning|--policy|--runtime <n>]
ai-litellm proxy     status|start|stop|restart|logs [n]
ai-litellm model     list|info [m]|limits [m]|probe [m...]|add|remove|refresh-capabilities|reasoning set/unset
ai-litellm context   matrix [f]|probe <surface|all>|observations [f]
ai-litellm reasoning matrix [m]|probe <m> [effort]
ai-litellm harness   list|info <n>|launch <n> …|alias get/set|reasoning [set/unset]
ai-litellm runtime   list|status [n]
ai-litellm key       status|set
ai-litellm uninstall
```

(`model add`/`remove`는 P6에서 구현되지만 트리의 자리는 여기서 확정한다.)

**제거/통합 목록:**

| 대상 | 처분 | 근거 |
|---|---|---|
| `route` 그룹 | `model`로 흡수 | 백엔드 컬럼은 `model list`에 통합, `route info`≡`model info`(M21에서 이미 수렴), `route probe`→`model probe`. **H6 결정의 의식적 역전**(route 명사가 죽으니 probe가 model로 귀환) — RATIONALE에 기록 |
| `codex` 그룹 | 삭제 | facade get/set뿐이었고 facade가 P3에서 소멸 |
| `router`·`dash` | 삭제 | P2에서 선행 |
| `audit` 그룹 | 삭제 | `doctor --policy`가 동일 기능 위임 중 |
| `capabilities` | `status`에 흡수 | |
| per-group doctor 동사 4개 | 사용자 표면에서 제거 | `doctor --<scope>` 단일화, 내부 함수는 위임용 유지 |
| deprecated flat alias 일몰 (잔여 13개; `status`는 P2에서 선행 재배정) | 삭제 | RATIONALE §9.8("일몰 기준 없음") 해소 기록 |
| `model reasoning [m]` 표 alias | 삭제 | `reasoning matrix`가 정본(set/unset은 유지) |
| `start-litellm`/`stop-litellm` 함수 | 삭제 | |
| bin `openrouter-key-status`·`litellm-master-key-status` | 삭제 | `key status`가 커버 |
| 런처의 `--start/--stop/--restart/--logs/--doctor` | 삭제 | proxy 소유권은 `ai-litellm proxy *`. `--list`/`--status`·`codex-litellm --refresh-catalog`는 유지 |
| `codex-litellm --route-info` | 삭제 | `model info`가 커버 |
| `model capabilities` 동사 | 출력이 `model info`/`status`와 중복이면 삭제, 유일 컬럼이 있으면 `model limits`로 이관 후 삭제 | 판정 규칙을 명시해 열린 항목이 아니게 함 |

**수용 기준**: usage 출력이 목표 트리와 일치 / 제거된 명령은 unknown-command loud-fail / check.zsh의 flat-alias 단언 갱신 후 green / README·ARCHITECTURE 명령어 절 재작성.

## 8. Phase 5 — repo 개명

- `gh repo rename ai-litellm` (GitHub이 구 URL 리다이렉트 유지) + 로컬 remote 갱신.
- README/문서 제목·클론 경로 갱신. **패키지 디렉토리는 `~/.local/share/ai-litellm-fabric` 유지** — 역사적 이름일 뿐 기능 무관임을 README에 1줄 명시(이설은 후속 라운드 선택지).
- 문서 최종 정합: ARCHITECTURE 결론 실행 경로 표·mermaid 갱신(P1·P2 반영 확인), RATIONALE 서문의 harness 수 갱신.

**수용 기준**: `gh repo view`가 새 이름 / `git remote -v` 갱신 / 문서 전체에서 stale 명칭 grep 청소(단, 패키지 디렉토리 경로와 결정 로그의 역사 기록은 예외).

## 9. Phase 6 — 카탈로그 셀프서비스 (`model add`/`model remove`)

TUI의 원래 목적("모델 카탈로그 관리")을 CLI로 완성한다. "모델 교체 = 명령 한 줄".

```zsh
ai-litellm model add <provider-id> [--name <surface>] [--claude-tier <tier>] [--codex] [--dry-run]
ai-litellm model remove <surface-name> [--dry-run]
```

- **`model add`**: OpenRouter `/models` 정본에서 한도·capability를 조회(기존 `refresh-capabilities` 엔진 재사용)해 **x-limits 앵커 + model_list 라우트를 자동 작성**(confidence=provider 라벨, 네이밍 컨벤션 자동 적용: `deepseek/deepseek-v4-pro`→`DeepSeek-V4-Pro-openrouter` 꼴은 `--name`으로 명시 가능). `--claude-tier`면 claude-litellm settings의 proxy/direct alias·displayName까지, `--codex`면 codex.json `catalogEntries`까지 갱신(descriptor 원자적 쓰기 — `reasoning set` 선례). 마지막에 `sync` 1회 실행. `--dry-run`은 sync 선례를 따라 변경 계획만 출력.
- **`model remove`**: 역방향 — 라우트·앵커(다른 라우트가 참조하지 않을 때만)·alias·catalogEntries에서 제거. tier가 참조 중인데 대체 지정이 없으면 loud-fail. `codex-auto-review`처럼 기능 슬러그가 백엔드로 참조 중이어도 loud-fail.
- 로컬 모델은 현행 runtime discovery가 이미 자동이므로 대상 외. OpenRouter 외 직결 provider는 이번 범위 밖(수동 절차 문서 유지).

**수용 기준**: `model add … --dry-run`이 정확한 변경 계획 출력 / add→`doctor` green→remove 왕복 후 `git diff` 청정 / 신규 모델이 `claude-litellm <surface>`·`codex-litellm <surface>`(--codex 지정 시)로 즉시 실행 가능.

## 10. 리스크

| 리스크 | 대응 |
|---|---|
| codex core가 비-gpt 클라우드 슬러그를 필수 경로에서 거부 | P3 스파이크가 배선 전 검출 → 표시명-만 폴백 |
| Kimi-K2.7-Code/Mimo-V2.5가 OpenRouter에 없거나 이름이 다름 | P3 첫 단계에서 정본 확인, 부재 시 중단 후 사용자 질의 |
| 신규 모델의 harness 품질(tool-calling 등) 미달 | TRIV/DOMAIN 프로브 게이트, 실패 시 alias 1줄로 재조정 |
| budget 차분테스트 라인범위 어긋남 | loud-fail 설계라 조용한 파손 불가 — Phase마다 재앵커 |
| 구 codex-litellm 세션 resume 불가 | 수용(격리 세션은 소모품) |
| `review` 기능이 카탈로그 엔트리를 요구할 가능성 | 스파이크 ③이 판정, 필요 시 append 목록에 추가 |
| 근육기억(구 명령) 파손 | 단일 사용자 승인. unknown-command가 loud-fail로 안내 |

## 11. 비-목표 (이번 라운드에서 건드리지 않는 것)

- **내부 식별자**: `AI_LITELLM_*` env, `ai_litellm_*` 함수 prefix, `config/ai-litellm/` 디렉토리명 — 전부 유지.
- **패키지 디렉토리**: `~/.local/share/ai-litellm-fabric` 유지.
- **Claude tier 메커니즘 자체**(tier 간접화, direct/proxy 이중 모드, 오버레이/lint/scrub), **격리/공유 경계**(§2 절단선), **토큰 3계층**(x-limits/예약/게이트웨이 clamp), **HARD CONSTRAINT**(native 무영향) — 매핑 값만 바뀌고 구조는 불변.
- **`--json` read 표면**: 유지(TUI 사후에도 스크립팅 계약).
- **goose legacy cleanup**: 유지.
- **Keychain 서비스명**: 이름과 무관, 무영향.
