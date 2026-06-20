# fabric v2 — P3: Reasoning-Effort Tuning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user change a model's or harness's reasoning effort from the `fabric` TUI: select a row in the Models or Harnesses panel, press `e`, pick from the **allowed** efforts (or "unset"), and the change applies via the existing `ai-litellm … reasoning set/unset` command — through the same gate/log path as every other action.

**Architecture:** A small additive `--json` backend read exposes each model's/harness's *allowed* efforts (so the picker shows only valid choices); a `FabricClient` method reads it; an `EffortSelector` modal presents the choices; `action_effort` (bound to `e`, guarded by panel+selection) runs `model|harness reasoning set/unset …` through the existing `_run_argv` (SAFE → immediate, no confirm). The backend commands already echo "Run 'ai-litellm sync' …", so the sync reminder appears in the results log for free — no separate TUI feature.

**Tech Stack:** zsh (lib.zsh polyglot reads), Python 3 + Textual 8.2.7, pytest + Pilot, all under the package venv; `check.zsh` for backend assertions.

## Global Constraints

- ALL dash python under the package venv: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q`. Backend gate: `AI_LITELLM_SKIP_DASH_VENV=1 zsh scripts/check.zsh` (venv already exists). Branch `feat/fabric-v2-p3-tuning`; do NOT switch branches. (spec §3)
- **backend owns logic, TUI is a caller** — the TUI never edits config; it calls `ai-litellm … reasoning set/unset`. The new `--json` reads are READ-ONLY and additive (v1 `--json` philosophy); they must return `[]` on failure, never crash. (spec §3, §13)
- **Single risk oracle** — execution flows through the existing `_run_argv` (gate via `safety.classify(argv)`). `model|harness reasoning set/unset` classify as SAFE → run immediately (the effort pick IS the deliberate action); no new ConfirmModal. No new/parallel gate. (spec §12, §13)
- **No drift** — the allowed-efforts the picker shows MUST come from the same backend source that validates `reasoning set` (the model `allowed_efforts` fn; the harness `adapterReasoning[adapter].allowed`), not a TUI-side copy. (spec §13; cf. the project's differential-guard idiom)
- **key set / secrets are OUT of P3** (that is P3b). Reasoning efforts are not secrets. (spec §13 §2)
- No P1/P2 regression (reads/auto-refresh, confirm-gate, async offload + `_refresh_in_flight`, palette). Tests make ZERO real subprocess/network calls (inject fakes). Validate Textual 8.2.7 APIs and adapt (P1/P2 lesson). (spec §3, §10)

---

## File Structure

- Modify: `config/ai-litellm/lib.zsh` — add `model reasoning allowed <model> --json` and `harness reasoning allowed <name> --json` reads (reuse existing allowed-efforts logic; no duplication).
- Modify: `scripts/check.zsh` — assertions for the two new `--json` reads.
- Modify: `config/ai-litellm/fabric_dash/client.py` — `model_reasoning_allowed`, `harness_reasoning_allowed` read methods.
- Create: `config/ai-litellm/fabric_dash/effort_modal.py` — `EffortSelector(ModalScreen)`.
- Modify: `config/ai-litellm/fabric_dash/app.py` — `_selected_model` tracking; `e` binding + `action_effort`; `_actions_for` adds `[e] reasoning` on Models/Harnesses.
- Modify: `config/ai-litellm/fabric_dash/help.py` — `e` keymap entry.
- Test: `tests/test_client.py`, `tests/test_app.py` (additions).

---

## Task 1: Backend `reasoning allowed --json` reads (model + harness)

**Files:**
- Modify: `config/ai-litellm/lib.zsh` (after `ai_litellm_model_reasoning_allowed_efforts` ~L3957; inside `ai_litellm_harness_reasoning_update`'s node script ~L4490 + its tail ~L4510; the `model reasoning` dispatch ~L5968 and `harness reasoning` dispatch ~L5931)
- Modify: `scripts/check.zsh` (near the existing "route/runtime/reasoning/context --json" assertion block)

**Interfaces:**
- Produces (CLI): `ai-litellm model reasoning allowed <model> --json` → JSON array of allowed efforts (e.g. `["none","minimal","low","medium","high","xhigh"]`); `ai-litellm harness reasoning allowed <name> --json` → JSON array (e.g. `["auto","low","medium","high","xhigh","max"]`). Empty `[]` on unknown/failure, rc 0 for the read.

- [ ] **Step 1: Add the model read** — in `lib.zsh`, immediately after the `ai_litellm_model_reasoning_allowed_efforts` function, add an emitter that converts its space-separated output to a JSON array (match the file's existing `--json` idiom — there are 8 emitters; reuse `ai_litellm_ruby`/`ai_litellm_node` as neighbors do):

```zsh
ai_litellm_model_reasoning_allowed_json() {
  local allowed
  allowed="$(ai_litellm_model_reasoning_allowed_efforts "${1:-}" 2>/dev/null)" || { printf '[]'; return 0; }
  ai_litellm_ruby -rjson -e 'puts JSON.generate(ARGV[0].to_s.split)' "$allowed" 2>/dev/null || printf '[]'
}
```

- [ ] **Step 2: Add the harness read by reusing the existing node script** — in `ai_litellm_harness_reasoning_update`'s embedded node script, right AFTER the line `if (!adapterReasoning[adapter]) fail(...)` and BEFORE `if (mode === "set")`, insert an early-exit that emits the adapter's allowed list (reuses `adapterReasoning[adapter].allowed` — no duplicate map):

```js
if (mode === "allowed") { process.stdout.write(JSON.stringify(adapterReasoning[adapter].allowed)); process.exit(0); }
```

Then guard the function's post-node echoes (currently `if [[ "$mode" == "set" ]] … else … fi` + the unconditional "Run 'ai-litellm sync' …") so they do NOT fire for `allowed`:

```zsh
  if [[ "$mode" == "set" ]]; then
    echo "Updated harness reasoning default: $harness -> $effort"
  elif [[ "$mode" == "unset" ]]; then
    echo "Reset harness reasoning default: $harness"
  fi
  [[ "$mode" == "allowed" ]] || echo "Run 'ai-litellm sync' to regenerate derived configs where needed."
```

And add a thin wrapper after `ai_litellm_harness_reasoning_unset`:

```zsh
ai_litellm_harness_reasoning_allowed_json() {
  ai_litellm_harness_reasoning_update allowed "${1:-}"
}
```

> Note: `harness_reasoning_update allowed <name>` passes the mode/harness checks (both non-empty) and the node script exits before the atomic write — so it is read-only for `allowed`. Verify the `effort` (`${3:-}`) being empty does not trip a guard for `allowed` mode (the only effort guard is `mode == "set" && -z effort`).

- [ ] **Step 3: Wire the dispatch** — in the `model)` group's `reasoning) case "${1:-}" in …` block, add an `allowed)` arm (drop a trailing `--json`):

```zsh
        allowed) shift; ai_litellm_model_reasoning_allowed_json "${1:-}" ;;
```

In the `harness)` group's `reasoning) case "${1:-}" in …` block, add:

```zsh
        allowed) shift; ai_litellm_harness_reasoning_allowed_json "${1:-}" ;;
```

(Update each group's `Usage:` line to mention `reasoning allowed <x>`.)

- [ ] **Step 4: Add check.zsh assertions** — near the existing `--json` read assertions, assert both new reads return a JSON array containing a known effort. Use a model that declares `supports_reasoning: true` and a real harness. Example (adapt the model/harness names to ones present in the config — verify with `ai-litellm model list` / `ai-litellm harness list`):

```zsh
m_allowed="$(ai-litellm model reasoning allowed GLM-5.2 --json 2>/dev/null)"
echo "$m_allowed" | jq -e 'type == "array" and (index("high") != null)' >/dev/null \
  || { echo "FAIL: model reasoning allowed --json"; exit 1; }
h_allowed="$(ai-litellm harness reasoning allowed claude --json 2>/dev/null)"
echo "$h_allowed" | jq -e 'type == "array" and length > 0' >/dev/null \
  || { echo "FAIL: harness reasoning allowed --json"; exit 1; }
echo "ok: reasoning allowed --json (model+harness)"
```

- [ ] **Step 5: Run the backend gate**

Run: `AI_LITELLM_SKIP_DASH_VENV=1 zsh scripts/check.zsh`
Expected: exit 0 (`ok`), including the new "reasoning allowed --json" line. If a known model/harness name is wrong, fix the assertion's names (do not weaken the check).

- [ ] **Step 6: Commit**

```bash
git add config/ai-litellm/lib.zsh scripts/check.zsh
git commit -m "feat(cli): reasoning allowed --json read (model+harness), reused, no drift"
```

---

## Task 2: FabricClient allowed-efforts reads

**Files:**
- Modify: `config/ai-litellm/fabric_dash/client.py` (after the existing read methods)
- Test: `config/ai-litellm/fabric_dash/tests/test_client.py`

**Interfaces:**
- Consumes: the Task 1 CLI reads.
- Produces: `FabricClient.model_reasoning_allowed(model: str) -> list`; `FabricClient.harness_reasoning_allowed(name: str) -> list` — each returns the allowed-effort list, `[]` on failure (reusing `_arr`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_client.py`:

```python
def test_reasoning_allowed_reads():
    from fabric_dash.client import FabricClient
    seen = []
    def run(argv):
        seen.append(argv)
        if argv[:4] == ["ai-litellm", "model", "reasoning", "allowed"]:
            return (0, '["low","high","xhigh"]')
        if argv[:4] == ["ai-litellm", "harness", "reasoning", "allowed"]:
            return (0, '["auto","high","max"]')
        return (1, "")
    c = FabricClient(runner=run)
    assert c.model_reasoning_allowed("GLM-5.2") == ["low", "high", "xhigh"]
    assert c.harness_reasoning_allowed("claude") == ["auto", "high", "max"]
    assert ["ai-litellm", "model", "reasoning", "allowed", "GLM-5.2", "--json"] in seen
    # failure → empty list, never raises
    assert FabricClient(runner=lambda a: (1, "")).model_reasoning_allowed("x") == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_client.py::test_reasoning_allowed_reads -q`
Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement** — in `client.py`, add (mirroring the existing `_arr` readers):

```python
    def model_reasoning_allowed(self, model: str) -> list:
        return self._arr("model", "reasoning", "allowed", model, "--json")

    def harness_reasoning_allowed(self, name: str) -> list:
        return self._arr("harness", "reasoning", "allowed", name, "--json")
```

- [ ] **Step 4: Run to verify pass**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add config/ai-litellm/fabric_dash/client.py config/ai-litellm/fabric_dash/tests/test_client.py
git commit -m "feat(dash): FabricClient reads for allowed reasoning efforts"
```

---

## Task 3: `EffortSelector` modal

**Files:**
- Create: `config/ai-litellm/fabric_dash/effort_modal.py`
- Modify: `config/ai-litellm/fabric_dash/app.tcss`
- Test: `config/ai-litellm/fabric_dash/tests/test_app.py`

**Interfaces:**
- Produces: `EffortSelector(ModalScreen)` — `__init__(self, efforts: list[str], target: str)`. Lists each allowed effort plus an `"unset"` row; dismisses with the chosen effort string, the literal `"unset"`, or `None` (escape). Mirrors the CommandPalette/ConfirmModal modal pattern.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_app.py`:

```python
@pytest.mark.asyncio
async def test_effort_selector_picks_effort():
    from fabric_dash.effort_modal import EffortSelector
    captured = {}
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        async def grab():
            captured["c"] = await app.push_screen_wait(EffortSelector(["low", "high", "xhigh"], "GLM-5.2"))
        app.run_worker(grab())
        await pilot.pause()
        await pilot.press("down")          # move to "high" (index 1)
        await pilot.press("enter")
        await pilot.pause()
        assert captured["c"] == "high"

@pytest.mark.asyncio
async def test_effort_selector_unset_and_cancel():
    from fabric_dash.effort_modal import EffortSelector
    captured = {}
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        async def grab():
            captured["c"] = await app.push_screen_wait(EffortSelector(["low"], "claude"))
        app.run_worker(grab())
        await pilot.pause()
        await pilot.press("escape"); await pilot.pause()
        assert captured["c"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_app.py -k effort_selector -q`
Expected: FAIL — `fabric_dash.effort_modal` does not exist.

- [ ] **Step 3: Implement `effort_modal.py`**:

```python
"""Reasoning-effort picker — lists the allowed efforts (+ 'unset') for the
selected model/harness and returns the choice. Select-only; the app runs the
chosen `reasoning set/unset` through the shared gated path."""
from __future__ import annotations
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView


class EffortSelector(ModalScreen):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, efforts: list[str], target: str) -> None:
        super().__init__()
        self._choices = list(efforts) + ["unset"]
        self._target = target

    def compose(self) -> ComposeResult:
        with Vertical(id="effort-box"):
            yield Label(f"reasoning effort — {self._target}", id="effort-title")
            lv = ListView(id="effort-list")
            yield lv

    def on_mount(self) -> None:
        lv = self.query_one("#effort-list", ListView)
        for c in self._choices:
            lv.append(ListItem(Label(c), name=c))
        lv.index = 0
        lv.focus()

    @on(ListView.Selected, "#effort-list")
    def _picked(self, event: ListView.Selected) -> None:
        name = event.item.name if event.item is not None else None
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

> Note: verify against textual 8.2.7 (as P2 did): `ListView.append/index/focus`, `ListItem(name=…)`, `ListView.Selected.item.name`, `@on`. If `event.item.name` is unavailable, store the choices and index off `event.list_view.index`. The tests pin the behavior (down→enter returns "high"; escape returns None).

- [ ] **Step 4: Style in `app.tcss`** — append (mirror the palette/confirm modals):

```css
EffortSelector { align: center middle; background: $background 60%; }
#effort-box { width: 40; height: auto; padding: 1 2; background: $surface; border: round $primary; }
#effort-title { margin-bottom: 1; color: $secondary; }
#effort-list { height: auto; max-height: 12; }
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_app.py -k effort_selector -q`
Expected: PASS. Then run the full suite — no regression.

- [ ] **Step 6: Commit**

```bash
git add config/ai-litellm/fabric_dash/effort_modal.py config/ai-litellm/fabric_dash/app.tcss config/ai-litellm/fabric_dash/tests/test_app.py
git commit -m "feat(dash): EffortSelector modal (allowed efforts + unset)"
```

---

## Task 4: Wire `e` → `action_effort` (model + harness)

**Files:**
- Modify: `config/ai-litellm/fabric_dash/app.py` (`_selected_model` init + tracking; `BINDINGS`; `action_effort`; `_actions_for`)
- Modify: `config/ai-litellm/fabric_dash/help.py` (`_KEYS`)
- Test: `config/ai-litellm/fabric_dash/tests/test_app.py`

**Interfaces:**
- Consumes: `EffortSelector` (Task 3), `client.model_reasoning_allowed`/`harness_reasoning_allowed` (Task 2), the existing `_run_argv` (P2), `_selected_harness`, `_row_label`, the Models panel node id.
- Produces: `action_effort` (a `@work` worker); `_selected_model` state.

> First confirm the Models panel's node id and that its rows carry a `name` — read `CONCEPTS` in app.py and `show_panel`/`_fill_table`. The harnesses panel uses node id `"harnesses"` and tracks `_selected_harness` in `on_data_table_row_highlighted`; mirror that for the models panel (node id likely `"models"` — VERIFY).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_app.py`:

```python
@pytest.mark.asyncio
async def test_effort_action_on_models_runs_reasoning_set():
    calls = []
    def spawn(argv):
        calls.append(argv); return (0, ["Run 'ai-litellm sync' to apply it to the running proxy."])
    from fabric_dash.actions import ActionRunner
    client = make_client()
    # client.model_reasoning_allowed returns something non-empty
    app = FabricApp(client=client, runner=ActionRunner(spawn=spawn))
    app.client.model_reasoning_allowed = lambda m: ["low", "high"]
    async with app.run_test() as pilot:
        await pilot.pause()
        app.show_panel("models"); app._selected = "models"; app._selected_model = "GLM-5.2"
        await pilot.pause()
        await pilot.press("e"); await pilot.pause()      # opens EffortSelector
        from fabric_dash.effort_modal import EffortSelector
        assert isinstance(app.screen, EffortSelector)
        await pilot.press("down"); await pilot.press("enter"); await pilot.pause()  # pick "high"
        assert calls == [["ai-litellm", "model", "reasoning", "set", "GLM-5.2", "high"]]

@pytest.mark.asyncio
async def test_effort_action_guards_when_no_selection():
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._selected = "proxy"            # not a reasoning panel
        await pilot.press("e"); await pilot.pause()
        from fabric_dash.effort_modal import EffortSelector
        assert not isinstance(app.screen, EffortSelector)   # guarded, no modal
```

- [ ] **Step 2: Run to verify failure**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_app.py -k effort_action -q`
Expected: FAIL — no `e` binding / `action_effort` / `_selected_model`.

- [ ] **Step 3: Implement in `app.py`**:

(a) Init `_selected_model` in `__init__` next to `_selected_harness`:

```python
        self._selected_model: str | None = None
```

(b) Track it in `on_data_table_row_highlighted` — alongside the harnesses branch, add a models branch (use the real Models node id you verified):

```python
        if (
            event.data_table.id == "data-table"
            and self._selected == "models"
            and event.row_key is not None
            and event.row_key.value is not None
        ):
            self._selected_model = str(event.row_key.value).rsplit("#", 1)[0] or None
```

(c) Add the binding to the first BINDINGS list: `("e", "effort", "Reasoning")`.

(d) Add the worker action (mirrors `action_launch`'s guard + the offloaded read):

```python
    @work
    async def action_effort(self) -> None:
        if self._selected == "models" and self._selected_model:
            target, level = self._selected_model, "model"
            allowed = await asyncio.to_thread(self.client.model_reasoning_allowed, target)
        elif self._selected == "harnesses" and self._selected_harness:
            target, level = self._selected_harness, "harness"
            allowed = await asyncio.to_thread(self.client.harness_reasoning_allowed, target)
        else:
            self.query_one("#results", RichLog).write(
                "[yellow]select a model or harness row first, then press e[/]"
            )
            return
        from .effort_modal import EffortSelector
        choice = await self.push_screen_wait(EffortSelector(allowed, target))
        if choice is None:
            return
        if choice == "unset":
            argv = [level, "reasoning", "unset", target]
        else:
            argv = [level, "reasoning", "set", target, choice]
        await self._run_argv(argv, f"{level} reasoning {target}")
```

(e) In `_actions_for`, surface the action on the two panels (it is SAFE; place it in the read-only group when relevant):

```python
        if node_id in ("models", "harnesses"):
            items.append(FooterItem("e", "reasoning", SAFE, False))
```

- [ ] **Step 4: Add the help entry** — in `help.py` `_KEYS`, add `("e", "set reasoning effort (model/harness)")`. Keep the list accurate; ensure the P1 help test still passes.

- [ ] **Step 5: Run the full suite to verify pass**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q`
Expected: PASS — the effort-action tests + all prior. (`classify(["model","reasoning","set",…])` is SAFE, so no ConfirmModal; the backend's "Run sync" line lands in `#results` via `_run_argv`.)

- [ ] **Step 6: Commit**

```bash
git add config/ai-litellm/fabric_dash/app.py config/ai-litellm/fabric_dash/help.py config/ai-litellm/fabric_dash/tests/test_app.py
git commit -m "feat(dash): e -> reasoning effort on Models/Harnesses (gated via _run_argv)"
```

---

## Self-Review

**Spec coverage (§13):** Task 1 = allowed `--json` reads (model + harness, reused/no-drift). Task 2 = client reads. Task 3 = EffortSelector (allowed + unset). Task 4 = `e`/`action_effort` on both panels, SAFE immediate-apply via `_run_argv`, `_selected_model` tracking, action-bar + help. The model-vs-harness sync reminder is delivered by the backend commands' own "Run 'ai-litellm sync' …" output flowing into `#results` (DRY — no separate TUI feature). key set excluded (P3b). No P1/P2 regression.

**Placeholder scan:** The `> Note:` blocks are concrete verification instructions (Textual 8.2.7 APIs; the Models node id; the `--json` idiom) with named fallbacks — the same pattern P1/P2 used. Backend code is grounded in the real functions (`ai_litellm_model_reasoning_allowed_efforts`, `adapterReasoning`, the dispatch blocks). The check.zsh names (GLM-5.2 / claude) are flagged to verify against the live config.

**Type consistency:** `model_reasoning_allowed`/`harness_reasoning_allowed` (Task 2) → return `list`, consumed by `action_effort` (Task 4) and passed to `EffortSelector(efforts, target)` (Task 3). `EffortSelector` dismisses `str | "unset" | None`, handled in `action_effort`. `_selected_model` defined in Task 4 (d)(a) and read in (d). `_run_argv(argv, label)` matches the P2 signature.

---

*Next: P3b (key set with backend `key set --stdin` + ActionRunner stdin + masked input) → P4a/P4b (mapping editors). See docs/superpowers/specs/2026-06-20-fabric-control-surface-v2-design.md §6, §13.*
