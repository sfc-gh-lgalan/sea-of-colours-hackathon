"""`soc doctor` must not call a credential healthy because it exists.

The bug this pins (v1.48) cost an afternoon twice, in different hands. The
check stopped at "a token is configured" and printed **present**, so a PAT
that was expired, revoked or scoped to another account passed the one command
you run when your agent will not think. Every request then failed in ~130ms
with HTTP 401, the seat fell back to the built-in heuristic, the heuristic
passed the night — and a passing agent never harvests, never banks blue and
never fires a weapon. That reads exactly like a broken agent, which is where
the afternoon goes.

So the property under test is not "doctor detects a bad token". It is that
doctor's *headline* distinguishes three states a user must act on differently:
no token, a token that works, and a token that is refused.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

import pytest

_SOC = Path(__file__).resolve().parents[1] / "scripts" / "soc.py"


def _load_soc():
    spec = importlib.util.spec_from_file_location("_soc_under_test", _SOC)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


soc = _load_soc()


class _Args:
    """Stand-in for the parsed argv `cmd_doctor` reads."""

    def __init__(self, no_llm_call: bool = False) -> None:
        self.no_llm_call = no_llm_call


def _run_doctor(monkeypatch, capsys, *, creds, call, no_llm_call=False) -> str:
    """Run doctor with the credential gate and the live call both stubbed."""
    monkeypatch.setattr(
        "sea_of_colours.orchestrator_2.cortex_chat.credentials_status",
        lambda: creds,
    )
    monkeypatch.setattr(soc, "_llm_answers_a_real_call", lambda: call)
    soc.cmd_doctor(_Args(no_llm_call=no_llm_call))
    return capsys.readouterr().out


def _llm_line(out: str) -> str:
    return next(l for l in out.splitlines() if "LLM credentials" in l)


def test_a_token_that_is_refused_is_not_reported_as_present(monkeypatch, capsys):
    """The whole bug, in one assertion.

    A configured-but-rejected token used to render identically to a working
    one. Whatever the wording, these two states must never share a headline.
    """
    out = _run_doctor(
        monkeypatch, capsys,
        creds=(True, ""),
        call=(False, "claude-haiku-4-5: HTTP 401: Invalid OAuth access token"),
    )
    line = _llm_line(out)
    assert "present" not in line.lower(), (
        "a refused token must not be described as present — that is the "
        "false pass this test exists to prevent"
    )
    assert "401" in out, "the provider's own reason must reach the user"


def test_a_refused_token_is_raised_as_a_problem_not_just_printed(
    monkeypatch, capsys,
):
    """It has to fail the command, not merely appear in the output.

    People scan doctor for `No problems found` and act on that alone; the
    guides and the leader checklist both tell them to. A line they must
    notice unaided is not a check.
    """
    out = _run_doctor(
        monkeypatch, capsys,
        creds=(True, ""),
        call=(False, "HTTP 401: Invalid OAuth access token"),
    )
    assert "No problems found" not in out
    assert "PROBLEMS" in out
    assert "fall back to the built-in heuristic" in out, (
        "the message must name the symptom, because the symptom (an agent "
        "that passes every night) is what sends people to the wrong bug"
    )


def test_a_working_token_stays_quiet(monkeypatch, capsys):
    out = _run_doctor(
        monkeypatch, capsys,
        creds=(True, ""),
        call=(True, "claude-haiku-4-5 answered in 1867ms"),
    )
    assert "No problems found" in out
    assert "REFUSED" not in out


def test_no_token_at_all_is_still_a_calm_report(monkeypatch, capsys):
    """Absent is the normal state for most of the room and must not alarm.

    Playing, testing, minting and the lab need no PAT at all, so a missing
    credential is not a fault — and doctor turning red for it would teach
    people to ignore doctor.
    """
    called = False

    def _boom():
        nonlocal called
        called = True
        return (False, "should never be reached")

    monkeypatch.setattr(
        "sea_of_colours.orchestrator_2.cortex_chat.credentials_status",
        lambda: (False, "SNOWFLAKE_PAT"),
    )
    monkeypatch.setattr(soc, "_llm_answers_a_real_call", _boom)
    soc.cmd_doctor(_Args())
    out = capsys.readouterr().out

    assert "absent" in _llm_line(out)
    assert "heuristic agents still run" in out
    assert not called, (
        "with no credential there is nothing to verify — doctor must not "
        "reach the network to prove a negative"
    )


def test_the_live_call_can_be_declined(monkeypatch, capsys):
    """`--no-llm-call` keeps doctor usable on a plane, and says so.

    The escape hatch must not quietly re-introduce the original bug, so the
    unverified state is labelled as unverified rather than as working.
    """
    called = False

    def _boom():
        nonlocal called
        called = True
        return (True, "unreachable")

    monkeypatch.setattr(
        "sea_of_colours.orchestrator_2.cortex_chat.credentials_status",
        lambda: (True, ""),
    )
    monkeypatch.setattr(soc, "_llm_answers_a_real_call", _boom)
    soc.cmd_doctor(_Args(no_llm_call=True))
    out = capsys.readouterr().out

    assert not called
    assert "NOT verified" in _llm_line(out)
    assert "working" not in _llm_line(out)


def test_the_probe_never_raises_even_with_no_network(monkeypatch):
    """Doctor runs on machines with no route to anywhere.

    A diagnostic that crashes is worse than one that reports a failure, so
    the probe swallows everything and converts it to a readable verdict.
    """
    import sea_of_colours.orchestrator_2.cortex_chat as cc

    class _Exploding:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise OSError("Network is unreachable")

    monkeypatch.setattr(cc, "CortexChatInvoker", _Exploding)
    ok, detail = soc._llm_answers_a_real_call()
    assert ok is False
    assert "OSError" in detail


def test_a_providers_multiline_complaint_is_flattened(monkeypatch):
    """Errors arrive as pretty-printed JSON; the PROBLEMS list is a list."""
    import sea_of_colours.orchestrator_2.cortex_chat as cc

    class _Refusing:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        def invoke(self, *a: Any, **k: Any) -> Dict[str, Any]:
            return {"ok": False,
                    "error": 'HTTP 401: {\n  "code" : "390303",\n'
                             '  "message" : "Invalid OAuth access token."\n}'}

    monkeypatch.setattr(cc, "CortexChatInvoker", _Refusing)
    ok, detail = soc._llm_answers_a_real_call()
    assert ok is False
    assert "\n" not in detail
    assert "390303" in detail


@pytest.mark.parametrize("verdict", [True, False])
def test_the_headline_always_states_which_of_the_three_states_it_found(
    monkeypatch, capsys, verdict,
):
    """No silent fourth state. Every path must commit to a verdict."""
    out = _run_doctor(
        monkeypatch, capsys, creds=(True, ""), call=(verdict, "detail"),
    )
    line = _llm_line(out).lower()
    assert ("working" in line) is verdict
    assert ("refused" in line) is (not verdict)
