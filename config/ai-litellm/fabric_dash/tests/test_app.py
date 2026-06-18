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
