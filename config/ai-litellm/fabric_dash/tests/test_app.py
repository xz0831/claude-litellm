import json
import pytest
from fabric_dash.app import FabricApp
from fabric_dash.client import FabricClient


def make_client():
    data = {
        "ai-litellm proxy status --json": json.dumps({"health": "ok", "configCurrency": "stale", "baseUrl": "http://127.0.0.1:4000", "pid": 9288, "log": "/tmp/l.log"}),
        "ai-litellm model list --json": json.dumps([{"name": "gpt-5.5", "backend": "openrouter/x"}]),
        "ai-litellm harness list --json": json.dumps([{"name": "claude", "adapter": "a", "valid": True, "cliInstalled": True}]),
        "ai-litellm key status --json": json.dumps({"openrouter": {"source": "keychain"}, "master": {"source": "keychain"}}),
        "ai-litellm route list --json": json.dumps([]),
        "ai-litellm runtime status --json": json.dumps([]),
        "ai-litellm reasoning matrix --json": json.dumps([]),
        "ai-litellm context matrix --json": json.dumps([]),
    }

    def run(argv):
        return (0, data.get(" ".join(a for a in argv if a is not None), ""))

    return FabricClient(runner=run)


@pytest.mark.asyncio
async def test_harness_panel_sets_launch_target():
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        # No harness picked until the Harnesses panel is opened.
        assert app._selected_harness is None
        app.show_panel("harnesses")
        await pilot.pause()
        # Opening the panel populates the DataTable and sets the launch target
        # to the first harness — 'l' now has a real target, not a hardcoded one.
        from textual.widgets import DataTable
        table = app.query_one("#harness-table", DataTable)
        assert table.display is True
        assert table.row_count == 1
        assert app._selected_harness == "claude"
        # status line reflects the live launch target
        assert "claude" in str(app.query_one("#status").content)


@pytest.mark.asyncio
async def test_launch_without_selection_does_not_default():
    # harness list empty -> no target; 'l' must not silently launch anything.
    def run(argv):
        if argv[:3] == ["ai-litellm", "proxy", "status"]:
            return (0, json.dumps({"health": "ok", "configCurrency": "current"}))
        return (0, "[]")
    app = FabricApp(client=FabricClient(runner=run))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.show_panel("harnesses")
        await pilot.pause()
        assert app._selected_harness is None
        await pilot.press("l")
        await pilot.pause()
        # No confirm modal, no exit — just a guidance message.
        from fabric_dash.modal import ConfirmModal
        assert not isinstance(app.screen, ConfirmModal)
        assert app.return_value is None


@pytest.mark.asyncio
async def test_app_boots_and_shows_proxy_health():
    app = FabricApp(client=make_client())
    async with app.run_test() as pilot:
        await pilot.pause()
        # In textual 8.2.7 Static exposes .content property (not .renderable)
        status_widget = app.query_one("#status")
        status_text = str(status_widget.content)
        assert "ok" in status_text
        # concept tree has the six top nodes
        from textual.widgets import Tree
        tree = app.query_one(Tree)
        labels = [str(n.label) for n in tree.root.children]
        assert any("Proxy" in l for l in labels)
        assert any("Models" in l for l in labels)
