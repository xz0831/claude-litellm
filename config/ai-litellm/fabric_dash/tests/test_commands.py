from fabric_dash.commands import Command, COMMANDS, filter_commands, resolve_argv
from fabric_dash import safety


def test_registry_excludes_secret_and_launch_commands():
    flat = [" ".join(c.argv) for c in COMMANDS]
    assert not any("key" in f and "set" in f for f in flat), "key set leaks secrets via log echo"
    assert not any("launch" in f for f in flat), "launch uses the special exit handoff, not the palette"


def test_registry_grades_resolve_via_classify():
    # Every no-arg command's grade must be discoverable by the shared oracle.
    for c in COMMANDS:
        if not c.takes_args:
            grade = safety.classify(list(c.argv))
            assert grade in (safety.SAFE, safety.RESTART, safety.BILLABLE, safety.DESTRUCTIVE)


def test_lifecycle_entries_match_safety_actions():
    argvs = {tuple(c.argv) for c in COMMANDS}
    assert ("proxy", "restart") in argvs and ("sync",) in argvs and ("proxy", "start") in argvs


def test_filter_is_fuzzy_subsequence_case_insensitive():
    res = filter_commands(COMMANDS, "rst")  # subsequence of "restart"
    assert any(c.argv == ("proxy", "restart") for c in res)
    assert filter_commands(COMMANDS, "") == COMMANDS          # empty → all
    assert filter_commands(COMMANDS, "zzzznope") == []        # no match → empty


def test_resolve_argv_splits_args_with_shlex():
    setcmd = next(c for c in COMMANDS if c.argv == ("model", "reasoning", "set"))
    assert resolve_argv(setcmd, "GLM-5.2 high") == ["model", "reasoning", "set", "GLM-5.2", "high"]
    start = next(c for c in COMMANDS if c.argv == ("proxy", "start"))
    assert resolve_argv(start, "ignored") == ["proxy", "start"]  # no-arg ignores text


def test_reasoning_probe_is_billable():
    probe = next(c for c in COMMANDS if c.argv == ("model", "reasoning", "probe"))
    # probe + a model resolves to a BILLABLE argv (gate path exercised in app tests)
    assert safety.classify(resolve_argv(probe, "GLM-5.2")) == safety.BILLABLE
