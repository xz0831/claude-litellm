from fabric_dash.actions import ActionRunner


def test_runner_streams_and_returns_rc():
    def spawn(argv):
        assert argv[0] == "ai-litellm"
        return (0, ["line1", "line2"])
    seen = []
    rc = ActionRunner(spawn=spawn).run(["proxy", "start"], on_line=seen.append)
    assert rc == 0
    assert seen == ["line1", "line2"]
