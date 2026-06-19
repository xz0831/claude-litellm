"""fabric — read-only control-plane TUI over ai-litellm."""
from __future__ import annotations
from pathlib import Path
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, Tree, Static, RichLog, DataTable
from textual import work
from .client import FabricClient
from .safety import ACTIONS, SAFE
from .actions import ActionRunner
from .modal import ConfirmModal

CONCEPTS = [
    ("proxy", "Proxy"),
    ("harnesses", "Harnesses"),
    ("models", "Models / Routes"),
    ("runtimes", "Runtimes"),
    ("budget", "Budget & Policy"),
    ("keys", "Keys"),
]

# Friendly labels for raw --json dict keys, so panels don't read as a wall of
# camelCase. Unmapped keys fall back to a title-cased version of the key.
COLUMN_LABELS = {
    "name": "Name",
    "model": "Model",
    "backend": "Backend",
    "adapter": "Adapter",
    "valid": "Valid",
    "cliInstalled": "CLI",
    "tpm": "TPM",
    "rpm": "RPM",
    "maxOut": "Max Out",
    "maxIn": "Max In",
    "source": "Source",
}


def _label(key: str) -> str:
    return COLUMN_LABELS.get(key, key[:1].upper() + key[1:])


class FabricApp(App):
    CSS_PATH = Path(__file__).parent / "app.tcss"
    TITLE = "ai-litellm fabric"
    BINDINGS = (
        [("q", "quit", "Quit"), ("r", "refresh", "Refresh"), ("l", "launch", "Launch")]
        + [(a.key, f"do_{a.key}", a.label) for a in ACTIONS]
    )

    def __init__(self, client: FabricClient | None = None, runner: ActionRunner | None = None):
        super().__init__()
        self.client = client or FabricClient()
        self.runner = runner or ActionRunner()
        self._selected = "proxy"
        self._selected_harness: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="status")
        with Horizontal(id="body"):
            tree: Tree = Tree("Concepts", id="concepts")
            tree.show_root = False
            for node_id, label in CONCEPTS:
                tree.root.add_leaf(label, data=node_id)
            yield tree
            yield Static("", id="content")
            table: DataTable = DataTable(id="harness-table", cursor_type="row", zebra_stripes=True)
            table.display = False  # only shown on the Harnesses panel
            yield table
        yield RichLog(id="results", highlight=False, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        self.show_panel("proxy")
        self.set_interval(4.0, self.refresh_status)  # safe/read-only auto-refresh only

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        node_id = event.node.data
        if node_id:
            self._selected = node_id
            self.show_panel(node_id)

    def action_refresh(self) -> None:
        self.refresh_status()
        self.show_panel(self._selected)

    def refresh_status(self) -> None:
        s = self.client.proxy_status()
        health = s.get("health", "unknown")
        cur = s.get("configCurrency", "unknown")
        url = s.get("baseUrl", "")
        dot = {"ok": "[green]o[/]", "unreachable": "[red]x[/]"}.get(health, "[yellow]?[/]")
        badge = "[yellow]STALE -> sync[/]" if cur == "stale" else f"[dim]{cur}[/]"
        target = self._selected_harness or "select a harness"
        launch = f"[dim]launch ->[/] {target}"
        self.query_one("#status", Static).update(
            f"{dot} proxy: {health}   config: {badge}   {launch}   [dim]{url}[/]"
        )

    def show_panel(self, node_id: str) -> None:
        content = self.query_one("#content", Static)
        table = self.query_one("#harness-table", DataTable)
        # Default: text panel visible, harness table hidden.
        content.display = True
        table.display = False
        if node_id == "proxy":
            s = self.client.proxy_status()
            lines = [f"{_label(k)}: {v}" for k, v in s.items()] or ["proxy not running — start it"]
            content.update("\n".join(lines))
        elif node_id == "harnesses":
            self._fill_harness_table(table)
            if table.row_count:
                content.display = False
                table.display = True
            else:
                content.update("no harnesses")
        elif node_id == "models":
            rows = self.client.model_limits() or self.client.model_list()
            content.update(_table_text(rows) or "no models / routes (is the proxy synced?)")
        elif node_id == "runtimes":
            content.update(_table_text(self.client.runtime_status()) or "no runtimes")
        elif node_id == "budget":
            content.update(_table_text(self.client.reasoning_matrix()) or "no reasoning matrix")
        elif node_id == "keys":
            k = self.client.key_status()
            content.update(
                "\n".join(f"{name}: {info.get('source','?')}" for name, info in k.items()) or "no keys"
            )
        else:
            content.update("")

    def _fill_harness_table(self, table: DataTable) -> None:
        """Render harnesses into a selectable DataTable; first row sets the launch target."""
        table.clear(columns=True)
        rows = self.client.harness_list()
        if not rows:
            self._selected_harness = None
            return
        cols = list(rows[0].keys())
        for c in cols:
            table.add_column(_label(c), key=c)
        for r in rows:
            table.add_row(*[str(r.get(c, "")) for c in cols], key=str(r.get("name", "")))
        # Default the launch target to the first harness so 'l' has a real target.
        if self._selected_harness is None and rows:
            self._selected_harness = str(rows[0].get("name", "")) or None
            self.refresh_status()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "harness-table" and event.row_key is not None:
            self._selected_harness = str(event.row_key.value)
            self.refresh_status()

    # --- action helpers ---

    def _action_by_key(self, key: str):
        for a in ACTIONS:
            if a.key == key:
                return a
        return None

    @work
    async def _run_action(self, key: str) -> None:
        """Run an action; @work provides the worker context needed by push_screen_wait."""
        a = self._action_by_key(key)
        if a is None:
            return
        if a.needs_confirm:
            ok = await self.push_screen_wait(
                ConfirmModal(a.consequence, title=f"Confirm {a.label}", grade=a.grade)
            )
            if not ok:
                self.query_one("#results", RichLog).write(f"[dim]cancelled: {a.label}[/]")
                return
        log = self.query_one("#results", RichLog)
        log.write(f"$ ai-litellm {' '.join(a.argv)}")
        rc = self.runner.run(list(a.argv), on_line=lambda ln: log.write(ln))
        log.write(f"[{'green' if rc == 0 else 'red'}]exit {rc}[/]")
        self.refresh_status()

    # Per-key action methods (explicit, not metaprogrammed); @work makes _run_action sync-callable
    def action_do_s(self) -> None: self._run_action("s")
    def action_do_R(self) -> None: self._run_action("R")
    def action_do_S(self) -> None: self._run_action("S")
    def action_do_x(self) -> None: self._run_action("x")
    def action_do_d(self) -> None: self._run_action("d")

    @work
    async def action_launch(self) -> None:
        harness = self._selected_harness
        if not harness:
            self.query_one("#results", RichLog).write(
                "[yellow]no harness selected — open the Harnesses panel and pick one[/]"
            )
            return
        ok = await self.push_screen_wait(
            ConfirmModal(
                f"launch {harness}: cloud-backed tiers make billable provider requests.",
                title=f"Confirm launch -> {harness}",
                grade="billable",
            )
        )
        if not ok:
            return
        self.exit(result=("launch", [harness]))


def _table_text(rows: list) -> str:
    if not rows:
        return ""
    cols = list(rows[0].keys())
    head = "  ".join(f"{_label(c):<18}" for c in cols)
    rule = "  ".join("-" * 18 for _ in cols)
    body = "\n".join("  ".join(f"{str(r.get(c, '')):<18}" for c in cols) for r in rows)
    # Bold header + dim rule give the table a scannable hierarchy in a mono panel.
    return f"[b]{head}[/b]\n[dim]{rule}[/dim]\n{body}"
