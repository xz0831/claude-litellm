# fabric v2 — P1: Design & Discoverability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the v1 dashboard's "투박 + 뭘 할 수 있는지 안 보임" complaints WITHOUT adding new actions: clean table cells (kill the raw-dict dump), framed panels + a deliberate theme, a prominent labeled action bar, and a `?` help overlay.

**Architecture:** Pure presentation/discoverability changes to the existing `config/ai-litellm/fabric_dash/` Textual app — no backend calls change, no new mutating commands (those are v2 P2–P4). Build on v1: `app.py` (FabricApp, `_cell`, `_fill_table`, `show_panel`, `_footer_items`), `app.tcss`, `footer.py` (StatusFooter).

**Tech Stack:** Python 3, Textual 8.2.7, pytest + Textual Pilot — all under the package venv.

## Global Constraints

- ALL python runs under the package venv: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q`. Never system python3 (PEP-668). Branch feat/fabric-dash; do NOT switch branches. (spec §3)
- P1 adds NO new actions and NO mutating commands — discoverability + design only. The action bar shows actions that ALREADY work (sync/restart/start/stop/doctor/launch). (spec §6 P1)
- Read-only auto-refresh and the confirm-gate invariant from v1 must keep working unchanged. (spec §3)
- Status color system is load-bearing: green=ok/ready, amber=stale/disruptive, red=fail/missing/billable — keep it consistent across panels, action bar, and theme. (spec §9)
- Tests make ZERO real subprocess/network calls (inject a fake FabricClient). The brief Textual code targets a generic API — VALIDATE against installed textual 8.2.7 and adapt until green (e.g. `Static.content` not `.renderable`, `App.export_screenshot()` for snapshots, CSS via `Path(__file__).parent`). (v1 lessons)

---

## File Structure

- Modify: `config/ai-litellm/fabric_dash/app.py` — cell formatter (`_cell` / a new `_format_value`), the contextual action bar (`_footer_items` → `_actions_for(node_id)`), a `?` binding + help action.
- Modify: `config/ai-litellm/fabric_dash/app.tcss` — panel borders/titles, the deliberate theme, action-bar prominence.
- Create: `config/ai-litellm/fabric_dash/help.py` — `HelpOverlay(ModalScreen)` listing the keymap.
- Modify/extend: `config/ai-litellm/fabric_dash/tests/test_app.py` — cell-formatting + contextual-action-bar + help-overlay tests.

No backend / lib.zsh changes in P1.

---

## Task 1: Clean table cell values (kill the raw-dict Sources dump)

**Files:**
- Modify: `config/ai-litellm/fabric_dash/app.py` (`_cell` ~L55-63; add `_format_value`)
- Test: `config/ai-litellm/fabric_dash/tests/test_app.py`

**Interfaces:**
- Produces: `_format_value(value) -> str` — renders dict/list/scalar values as compact human text (dict → `"k:v / k:v"` of its values joined by `/` when keys are uniform-ish, else `k=v`); used by `_cell`.

The bug: `model limits --json` rows carry `sources={"context":"provider","output":"provider"}`; `_cell` does `str(value)` → the literal `{'context': 'provider', 'output': 'provider'}` dumps into the table. Render it as `provider / provider`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_app.py`:

```python
def test_cell_formats_dict_value_not_raw_repr():
    from fabric_dash.app import _cell
    cell = _cell("sources", {"context": "provider", "output": "owned-policy"})
    plain = cell.plain
    assert "{" not in plain and "'" not in plain          # no python repr leakage
    assert "provider" in plain and "owned-policy" in plain  # values shown
    assert plain == "provider / owned-policy"               # compact, ordered by dict order

def test_cell_formats_scalars_and_none():
    from fabric_dash.app import _cell
    assert _cell("context", 1048576).plain == "1048576"
    assert _cell("model", None).plain == ""
    assert _cell("backend", "openrouter/x").plain == "openrouter/x"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_app.py::test_cell_formats_dict_value_not_raw_repr -q`
Expected: FAIL — current `_cell` returns the raw dict repr.

- [ ] **Step 3: Add `_format_value` and use it in `_cell`** — in `app.py`, add before `_cell` and call it:

```python
def _format_value(value) -> str:
    """Human-readable scalar for a table cell. Dicts/lists are flattened to a
    compact ' / '-joined string of their values (the --json `sources` field is a
    dict like {'context': 'provider', 'output': 'owned-policy'}) instead of a raw
    Python repr leaking into the UI."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return " / ".join(_format_value(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " / ".join(_format_value(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)
```

Then change `_cell`'s scalar branch to use it:

```python
def _cell(key: str, value) -> Text:
    """Render one table cell, coloring readiness signals per the status system."""
    if key in _BOOL_READY_KEYS or isinstance(value, bool):
        truthy = value is True or str(value).strip().lower() in ("true", "yes", "1")
        return Text("✓" if truthy else "✗", style=_OK if truthy else _BAD)
    text = _format_value(value)
    if key in ("source", "sources") and text.strip().lower() in _BAD_SOURCES:
        return Text(text, style=_BAD)
    return Text(text)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q`
Expected: PASS (all, including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add config/ai-litellm/fabric_dash/app.py config/ai-litellm/fabric_dash/tests/test_app.py
git commit -m "fix(dash): format dict/list cell values (kill raw-dict Sources dump)"
```

---

## Task 2: Framed panels + a deliberate theme

**Files:**
- Modify: `config/ai-litellm/fabric_dash/app.tcss`
- Modify: `config/ai-litellm/fabric_dash/app.py` (give `#content`/`#data-table`/`#results` `border_title`s; register a theme in `on_mount` if used)
- Test: `tests/test_app.py` (snapshot-rendered design check)

**Interfaces:**
- Consumes: existing widget ids `#status`, `#concepts` (Tree), `#content`, `#data-table`, `#results`, `#footer`.
- Produces: visually separated, titled panels; a consistent intentional palette.

This is a design task — the implementer VALIDATES the look by rendering snapshots (Textual `App.export_screenshot()` SVG, as the v1 review did) and iterates until the panels read as distinct, titled regions with a deliberate (non-default) accent, while preserving the load-bearing green/amber/red status colors.

- [ ] **Step 1: Add panel framing to `app.tcss`** — give the content/table/results real borders + titles, and a calmer divider system. Replace the layout block:

```css
#body { height: 1fr; }

Tree {
    width: 30;
    border: round $primary 50%;
    border-title-color: $secondary;
    padding: 0 1;
}
#content, #data-table {
    width: 1fr;
    height: 1fr;
    border: round $primary 50%;
    border-title-color: $secondary;
    padding: 0 1;
}
#data-table > .datatable--header { text-style: bold; color: $secondary; }
#data-table > .datatable--cursor { background: $primary; color: $text; }

#results {
    height: 8;
    border: round $primary 50%;
    border-title-color: $secondary;
    padding: 0 1;
}
```

> Note: `border: round` + `border_title` gives each panel a titled frame (the "구분선 없음" fix). Set the titles in `app.py` (Step 2). Validate the exact CSS keys against textual 8.2.7 (`border-title-color` vs `border-title-color:` — adapt if the property name differs in 8.2.7).

- [ ] **Step 2: Set panel titles in `app.py`** — in `on_mount` (after widgets exist), title each panel; and set the table/results titles. Add:

```python
def on_mount(self) -> None:
    self.query_one("#concepts", Tree).border_title = "Concepts"
    self.query_one("#results", RichLog).border_title = "Results"
    self.query_one("#footer", StatusFooter).set_items(self._actions_for(self._selected))
    self.refresh_status()
    self.show_panel("proxy")
    self.set_interval(4.0, self.refresh_status)
```

And in `show_panel`, set the content/table title to the human panel name (find the CONCEPTS label):

```python
# at the top of show_panel(self, node_id):
title = next((lbl for nid, lbl in CONCEPTS if nid == node_id), node_id)
self.query_one("#content", Static).border_title = title
self.query_one("#data-table", DataTable).border_title = title
```

- [ ] **Step 3: Deliberate theme** — register and set an intentional theme in `__init__`/`on_mount` so the app isn't the stock textual look, keeping status colors meaningful. In `app.py`:

```python
from textual.theme import Theme  # validate import path against textual 8.2.7

_FABRIC_THEME = Theme(
    name="fabric",
    primary="#4c9aff",      # calm steel-blue accent (panels/borders)
    secondary="#9aa7b3",    # muted slate (titles/headers)
    success="#3fb950",      # green  = ok / ready
    warning="#d29922",      # amber  = stale / disruptive
    error="#f85149",        # red    = fail / missing / billable
    background="#0d1117",
    surface="#161b22",
    panel="#1c2128",
    dark=True,
)
```

In `on_mount` (before set_items / show_panel): `self.register_theme(_FABRIC_THEME); self.theme = "fabric"`.

> Note: `Theme` API and field names can differ in textual 8.2.7 — read `textual.theme` in the venv and adapt (some versions take a dict or different kwargs). If `Theme` is unavailable, fall back to overriding the `$primary/$secondary/...` design tokens directly in app.tcss `:root`/`Screen {}`. The REQUIREMENT is a deliberate, non-default palette with the three status colors intact — not this exact API.

- [ ] **Step 4: Snapshot-validate the design** — render the proxy/models/budget panels + the action bar to `/tmp/fabric-p1/*.svg` via a venv script using `app.run_test()` + `app.export_screenshot()`, READ the SVGs, and confirm: each panel has a visible titled frame, the Sources column is clean text, the palette is the fabric theme (not stock), and status colors still apply. Iterate the tcss until it reads as intentional. (No automated assertion — this is the human-design loop; record what the snapshots show.)

- [ ] **Step 5: Run tests + commit**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q` → PASS (panels still compose; the row-key/launch/refresh tests unaffected).

```bash
git add config/ai-litellm/fabric_dash/app.tcss config/ai-litellm/fabric_dash/app.py
git commit -m "feat(dash): framed titled panels + deliberate fabric theme"
```

---

## Task 3: Prominent, contextual action bar

**Files:**
- Modify: `config/ai-litellm/fabric_dash/app.py` (`_footer_items` → `_actions_for(node_id)`; refresh the bar on panel change)
- Modify: `config/ai-litellm/fabric_dash/app.tcss` (action bar prominence)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `StatusFooter.set_items`, `FooterItem(key,label,grade,mutating)` (footer.py), `ACTIONS`/`SAFE`/`BILLABLE` (safety.py).
- Produces: `_actions_for(node_id: str) -> list[FooterItem]` — the global actions PLUS the current panel's primary action (e.g. `l launch` highlighted on `harnesses`), so the bar shows what's relevant where; called on mount and on panel change.

P1 adds no NEW actions — it makes the EXISTING ones (q, r, s sync, d doctor, S start, R restart, X stop, l launch) discoverable and contextual: always show the global set, and lead with the panel's most relevant action.

- [ ] **Step 1: Write the failing test** — append to `tests/test_app.py`:

```python
@pytest.mark.asyncio
async def test_action_bar_is_contextual_per_panel():
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        from fabric_dash.footer import StatusFooter
        footer = app.query_one("#footer", StatusFooter)
        # On Harnesses, launch must be present and labelled in the bar text.
        app.show_panel("harnesses"); await pilot.pause()
        app.query_one("#footer", StatusFooter).set_items(app._actions_for("harnesses"))
        bar = footer.render().plain if hasattr(footer.render(), "plain") else str(footer.renderable)
        assert "launch" in bar and "sync" in bar      # panel action + a global action, both labelled
        assert " l " in bar                            # the key is shown, not hidden
```

- [ ] **Step 2: Run to verify failure**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_app.py::test_action_bar_is_contextual_per_panel -q`
Expected: FAIL — `_actions_for` doesn't exist yet (only the fixed `_footer_items`).

- [ ] **Step 3: Add `_actions_for` and refresh the bar on panel change** — in `app.py`, rename the intent of `_footer_items` to a contextual builder. Add:

```python
# Which panel cares about which existing action (P1 = existing actions only).
_PANEL_PRIMARY = {
    "harnesses": ("l", "launch", BILLABLE, True),
    "proxy": ("s", "sync", "restart", True),
}

def _actions_for(self, node_id: str) -> list[FooterItem]:
    """The action bar for a panel: quit/refresh + SAFE actions, a divider, then
    the mutating actions — with the current panel's primary action included so
    'what can I do here' is visible. P1 surfaces only actions that already work."""
    items = [FooterItem("q", "quit", "quit", False), FooterItem("r", "refresh", SAFE, False)]
    items += [FooterItem(a.key, a.label, a.grade, False) for a in ACTIONS if a.grade == SAFE]
    items.append(FooterItem("l", "launch", BILLABLE, True))
    items += [FooterItem(a.key, a.label, a.grade, True) for a in ACTIONS if a.grade != SAFE]
    items.append(FooterItem("?", "help", "quit", False))
    return items
```

> Note: keep `_footer_items` as a thin alias (`return self._actions_for(self._selected)`) if other code calls it, or replace its callers. In `on_tree_node_selected` (after `self._selected = node_id`) and in `show_panel`, refresh the bar: `self.query_one("#footer", StatusFooter).set_items(self._actions_for(node_id))`. (P1's contextual bit is including `?` help + keeping the bar refreshed per panel; richer per-item actions like `reasoning set` arrive in P3.)

- [ ] **Step 4: Make the bar prominent** — in `app.tcss`, give `#footer` a top border + bold so it reads as the action bar, not a faint strip:

```css
#footer {
    dock: bottom;
    height: 1;
    background: $panel;
    color: $text;
    border-top: solid $primary 50%;
    padding: 0 1;
}
```

- [ ] **Step 5: Run tests + commit**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q` → PASS.

```bash
git add config/ai-litellm/fabric_dash/app.py config/ai-litellm/fabric_dash/app.tcss
git commit -m "feat(dash): prominent contextual action bar (existing actions, discoverable)"
```

---

## Task 4: `?` help overlay

**Files:**
- Create: `config/ai-litellm/fabric_dash/help.py`
- Modify: `config/ai-litellm/fabric_dash/app.py` (bind `?` → push HelpOverlay)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `HelpOverlay(ModalScreen)` listing every key + what it does; dismissed by `?`/`escape`/`q`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_app.py`:

```python
@pytest.mark.asyncio
async def test_help_overlay_opens_and_lists_keys():
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        from fabric_dash.help import HelpOverlay
        assert isinstance(app.screen, HelpOverlay)
        text = app.screen.query_one("#help-body").renderable
        body = text.plain if hasattr(text, "plain") else str(text)
        for token in ("sync", "restart", "launch", "doctor", "quit"):
            assert token in body
        await pilot.press("escape"); await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/test_app.py::test_help_overlay_opens_and_lists_keys -q`
Expected: FAIL — `fabric_dash.help` does not exist.

- [ ] **Step 3: Create `help.py`**:

```python
"""`?` keymap overlay — makes every binding discoverable in one place."""
from __future__ import annotations
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static

_KEYS = [
    ("q", "quit"), ("r", "refresh"), ("?", "this help"),
    ("s", "sync (restart proxy)"), ("S", "start proxy"),
    ("R", "restart proxy"), ("X", "stop proxy"), ("d", "doctor (full battery)"),
    ("l", "launch selected harness (billable)"),
    ("↑/↓", "move selection"), ("enter", "select / drill in"),
]


class HelpOverlay(ModalScreen):
    BINDINGS = [("question_mark", "dismiss", "Close"), ("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        lines = "\n".join(f"  [b]{k:<6}[/b]  {desc}" for k, desc in _KEYS)
        yield Static(f"[b]fabric — keys[/b]\n\n{lines}\n\n[dim]?/esc/q to close[/dim]", id="help-body")

    def action_dismiss(self) -> None:
        self.dismiss(None)
```

> Note: the binding name for `?` may be `"question_mark"` in textual 8.2.7 — verify; bind whatever the framework names that key. Style `#help-body` with a centered bordered box in app.tcss (reuse the ConfirmModal pattern: `HelpOverlay { align: center middle; } #help-body { width: 60; padding: 1 2; background: $surface; border: round $primary; }`).

- [ ] **Step 4: Bind `?` in `app.py`** — add to `FabricApp.BINDINGS`: `("question_mark", "help", "Help")`, and:

```python
def action_help(self) -> None:
    from .help import HelpOverlay
    self.push_screen(HelpOverlay())
```

Add the `HelpOverlay` CSS to `app.tcss` (per the Step 3 note).

- [ ] **Step 5: Run tests + commit**

Run: `cd config/ai-litellm && "$HOME/.local/share/ai-litellm-fabric/state/dash-venv/bin/python" -m pytest fabric_dash/tests/ -q` → PASS.

```bash
git add config/ai-litellm/fabric_dash/help.py config/ai-litellm/fabric_dash/app.py config/ai-litellm/fabric_dash/app.tcss
git commit -m "feat(dash): ? help overlay listing the full keymap"
```

---

## Self-Review

**Spec coverage (§6 P1, §9):** Task 1 = column format / Sources raw-dict fix (§9). Task 2 = panel borders/titles + deliberate theme (§9). Task 3 = prominent contextual action bar from existing actions (§1 discoverability). Task 4 = `?` help overlay (§5.4). No new actions / no mutating commands (§6 P1) — those are P2–P4. Read-only auto-refresh + confirm-gate untouched (§3).

**Placeholder scan:** The `> Note:` blocks point to verifying Textual-8.2.7 API names (Theme, border-title-color, question_mark) with concrete fallbacks — verification instructions, not unspecified work. Every code step has runnable code.

**Type consistency:** `_format_value` (Task 1) used by `_cell`. `_actions_for(node_id)` (Task 3) returns `list[FooterItem]` (footer.py namedtuple), consumed by `StatusFooter.set_items`. `HelpOverlay` (Task 4) id `#help-body` matches the test. The `?` binding name (`question_mark`) is used consistently in Tasks 3/4 and HelpOverlay.

---

*Next plans in the v2 series (each its own writing-plans cycle): P2 command palette → P3 tuning mutations (reasoning/key set) → P4a claude tier mapping → P4b codex facade mapping. See docs/superpowers/specs/2026-06-20-fabric-control-surface-v2-design.md.*
