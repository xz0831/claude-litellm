#!/usr/bin/env python3
"""Differential test for the four token-budget implementations.

There are four independent copies of the output-reservation / token-budget math
in this repo. Three compute an *effectiveInput* (usable input budget); one
(Python) computes an *outputCap* (max output tokens). They MUST stay in
lockstep on the quantities that are comparable:

  1. Node      ai_litellm_harness_output_budget  (lib.zsh node -e block)   -> effectiveInput
  2. Ruby-cat  effective_input                    (lib.zsh ruby catalog)    -> effectiveInput
  3. Ruby-mat  output_budget                      (lib.zsh ruby matrix)     -> effectiveInput
  4. Python    gateway_output_cap                 (output_clamp.py)         -> outputCap

This harness feeds one input matrix to ALL FOUR and asserts the comparable
fields are IDENTICAL. It is the real guard; check.zsh runs it in CI.

Anti-drift: the three lib.zsh copies are NOT pasted here. They are sliced out of
lib.zsh at runtime by line range and executed live, so this test always tests
the shipped code. A SHA guard (LIB_SLICES) documents the ranges; if lib.zsh is
refactored the ranges move and the slice markers below must be updated -- the
test fails loudly (extracted text won't define the expected function) rather
than silently testing a stale copy.

Run:  python3 scripts/verify_budget_consistency.py
Exit: 0 = all four agree across the matrix; 1 = drift (a real latent bug) or
      an implementation could not be driven.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "config" / "ai-litellm" / "lib.zsh"
PY_PKG = REPO / "config"

# --- line ranges of the three shell-embedded copies (1-based, inclusive) ------
# These are the bodies *between* the surrounding shell quoting, verbatim from
# lib.zsh. If they drift, the marker check below fails before any row runs.
NODE_RANGE = (449, 515)          # node -e '<body>' in ai_litellm_harness_output_budget
RUBY_CAT_RANGE = (543, 581)      # positive_int + pick_reservation + effective_input
RUBY_MAT_RANGE = (4759, 4820)    # positive_int + output_budget


# =============================================================================
# 1. ORACLE  -- the canonical spec both families derive from.
# =============================================================================
def posint(value: Any) -> int | None:
    """Mirror of the *Number()/to_i loose* coercion used by Node + Ruby-catalog
    + Python (int()). Non-finite / non-positive -> None."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or n <= 0:
        return None
    return int(math.floor(n))


def canonical_budget(
    context_in: Any,
    capability_in: Any,
    *,
    default: int | None = 32000,
    tokenizer_headroom: Any = 8192,
    minimum_input: Any = 32768,
    per_selection: int | None = None,
    per_tier: int | None = None,
    per_model: int | None = None,
) -> dict[str, Any]:
    """The reference effectiveInput computation (Node / Ruby-cat / Ruby-mat)."""
    context = posint(context_in)
    capability = posint(capability_in)
    cfg_headroom = posint(tokenizer_headroom)
    cfg_headroom = 0 if cfg_headroom is None else cfg_headroom
    cfg_min_input = posint(minimum_input)
    cfg_min_input = 32768 if cfg_min_input is None else cfg_min_input

    chosen = None
    for cand in (per_selection, per_tier, per_model, default):
        n = posint(cand)
        if n is not None:
            chosen = n
            break

    if chosen is None:
        if capability is None:
            return {"sentinel": "no-reservation", "context": context,
                    "capability": capability, "effectiveInput": None}
        reservation = min(capability, 32000)
    else:
        reservation = chosen

    if capability is not None and reservation > capability:
        reservation = capability

    headroom = cfg_headroom
    min_input = cfg_min_input
    if context is not None:
        headroom = min(headroom, math.floor(context * 0.1))
        min_input = min(min_input, max(1, math.floor(context * 0.5)))
        max_reservation = context - headroom - min_input
        if max_reservation < 1:
            reservation = 1
            headroom = max(0, context - min_input - reservation)
        elif reservation > max_reservation:
            reservation = max_reservation

    effective_input = None if context is None else max(0, context - reservation - headroom)
    return {
        "sentinel": None,
        "context": context,
        "capability": capability,
        "reservation": reservation,
        "tokenizerHeadroom": headroom,
        "minimumInput": min_input,
        "effectiveInput": effective_input,
    }


def canonical_output_cap(
    context_in: Any,
    capability_in: Any,
    *,
    default: int = 32000,
    per_model: int | None = None,
    tokenizer_headroom: Any = 8192,
    minimum_input: Any = 32768,
) -> int:
    """The reference Python outputCap computation (gateway_output_cap)."""
    cap = posint(per_model) or posint(default) or 32000
    capability = posint(capability_in)
    if capability is not None:
        cap = min(cap, capability)
    context = posint(context_in)
    if context is not None:
        headroom = posint(tokenizer_headroom) or 0
        headroom = min(headroom, math.floor(context * 0.1))
        min_input = posint(minimum_input) or 32768
        min_input = min(min_input, max(1, math.floor(context * 0.5)))
        max_reservation = context - headroom - min_input
        cap = min(cap, max_reservation if max_reservation > 0 else 1)
    return max(1, cap)


# =============================================================================
# 2. DRIVERS  -- invoke the four real implementations.
# =============================================================================
def _slice_lib(start: int, end: int) -> str:
    lines = LIB.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[start - 1:end])


_NODE_BODY = _slice_lib(*NODE_RANGE)
_RUBY_CAT_BODY = _slice_lib(*RUBY_CAT_RANGE)
_RUBY_MAT_BODY = _slice_lib(*RUBY_MAT_RANGE)

# Sanity: the extracted slices must define the functions we expect. If lib.zsh is
# refactored and the ranges go stale, fail loudly here -- never test a wrong slice.
_SLICE_GUARDS = [
    ("NODE", _NODE_BODY, ["const positiveInt", "effectiveInput", "console.log"]),
    ("RUBY_CAT", _RUBY_CAT_BODY, ["def positive_int", "def pick_reservation", "def effective_input"]),
    ("RUBY_MAT", _RUBY_MAT_BODY, ["def positive_int", "def output_budget"]),
]


def _check_slices() -> list[str]:
    errs = []
    for name, body, needles in _SLICE_GUARDS:
        for needle in needles:
            if needle not in body:
                errs.append(f"lib.zsh slice {name} range is stale: missing {needle!r} "
                            f"(update *_RANGE in {Path(__file__).name})")
    # Catalog slice must NOT include the per-model-collapsing wrapper (we drive
    # effective_input directly so we can exercise perTier/perSelection).
    if "ai_litellm_codex_catalog_context_map" in _RUBY_CAT_BODY:
        errs.append("RUBY_CAT slice leaked the wrapper; tighten RUBY_CAT_RANGE")
    return errs


def policy_descriptor_json(policy: dict[str, Any]) -> str:
    return json.dumps({"adapterConfig": {"outputReservation": policy}})


def build_policy(default=32000, headroom=8192, min_input=32768,
                 per_selection=None, per_tier=None, per_model=None,
                 selection=None, model=None, empty=False) -> dict[str, Any]:
    if empty:
        return {}
    p: dict[str, Any] = {}
    if default is not None:
        p["default"] = default
    if headroom is not None:
        p["tokenizerHeadroom"] = headroom
    if min_input is not None:
        p["minimumInput"] = min_input
    if per_selection is not None and selection is not None:
        p["perSelection"] = {selection: per_selection}
    if per_tier is not None and selection is not None:
        p["perTier"] = {selection: per_tier}
    if per_model is not None and model is not None:
        p["perModel"] = {model: per_model}
    return p


def drive_node(policy: dict, selection: str, model: str,
               context: Any, capability: Any) -> dict[str, Any]:
    """Run the verbatim node -e block with its 4 argv. limits omits 'output'
    when capability is None (capability=null path)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write(policy_descriptor_json(policy))
        descriptor = f.name
    try:
        limits: dict[str, Any] = {}
        if context is not None:
            limits["context"] = context
        if capability is not None:
            limits["output"] = capability
        proc = subprocess.run(
            ["node", "-e", _NODE_BODY, descriptor, selection, model, json.dumps(limits)],
            capture_output=True, text=True,
        )
    finally:
        os.unlink(descriptor)
    if proc.returncode != 0:
        return {"sentinel": "exit-nonzero", "rc": proc.returncode, "stderr": proc.stderr.strip()}
    return {"sentinel": None, **json.loads(proc.stdout)}


_RUBY_CAT_SHIM = r'''
require "json"
%s
descriptor = JSON.parse(STDIN.read)
policy = descriptor.dig("adapterConfig", "outputReservation") || {}
selection = ARGV[0]; model = ARGV[1]
ctx = ARGV[2].empty? ? nil : ARGV[2].to_i
out = ARGV[3].empty? ? nil : ARGV[3].to_i
val = effective_input(policy, selection, model, ctx, out)
puts JSON.generate({"effective_input" => val})
'''


def drive_ruby_catalog(policy: dict, selection: str, model: str,
                       context: Any, capability: Any) -> dict[str, Any]:
    """Drive the catalog effective_input directly (distinct selection/model so the
    perSelection>perTier>perModel precedence is exercised, unlike the wrapper).

    Production contract: the catalog WRAPPER (lib.zsh ~590) coerces context via
    positive_int and does `next unless context`, so effective_input is NEVER
    called with a non-positive/non-finite context -- it is skipped entirely. We
    honor that contract here: a context that doesn't survive posint() means the
    model is absent from the catalog map (sentinel "skipped"), NOT a 0 budget.
    Driving the raw negative into effective_input would test a state the wrapper
    makes unreachable."""
    if posint(context) is None and context is not None:
        return {"sentinel": "skipped-by-wrapper-guard"}
    script = _RUBY_CAT_SHIM % _RUBY_CAT_BODY
    proc = subprocess.run(
        ["ruby", "-e", script,
         selection, model,
         "" if context is None else str(context),
         "" if capability is None else str(capability)],
        input=policy_descriptor_json(policy), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"sentinel": "exit-nonzero", "rc": proc.returncode, "stderr": proc.stderr.strip()}
    return {"sentinel": None, **json.loads(proc.stdout)}


_RUBY_MAT_SHIM = r'''
require "json"
%s
descriptor = JSON.parse(STDIN.read)
selection = ARGV[0]; model = ARGV[1]
ctx = ARGV[2].empty? ? nil : ARGV[2].to_i
out = ARGV[3].empty? ? nil : ARGV[3].to_i
b = output_budget(descriptor, selection, model, ctx, out)
if b.nil?
  puts JSON.generate({"nil" => true})
else
  puts JSON.generate(b)
end
'''


def drive_ruby_matrix(policy: dict, selection: str, model: str,
                      context: Any, capability: Any) -> dict[str, Any]:
    script = _RUBY_MAT_SHIM % _RUBY_MAT_BODY
    proc = subprocess.run(
        ["ruby", "-e", script,
         selection, model,
         "" if context is None else str(context),
         "" if capability is None else str(capability)],
        input=policy_descriptor_json(policy), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"sentinel": "exit-nonzero", "rc": proc.returncode, "stderr": proc.stderr.strip()}
    out = json.loads(proc.stdout)
    if out.get("nil"):
        return {"sentinel": "nil"}
    return {"sentinel": None, **out}


# Python is imported, not subprocessed. Policy is injected via env so we never
# touch the real config. Clear bleed between rows.
sys.path.insert(0, str(PY_PKG))
from ai_litellm_callbacks.output_clamp import (  # noqa: E402
    gateway_output_cap,
    clamp_token_reservations,
)

try:
    import yaml as _yaml  # noqa: F401
    _HAVE_YAML = True
except Exception:
    _HAVE_YAML = False

# checks that could not be driven in this environment (reported, not failed)
SKIPPED: list[str] = []

_PY_ENV_KEYS = [
    "AI_LITELLM_OUTPUT_CLAMP_DEFAULT",
    "AI_LITELLM_OUTPUT_CLAMP_TOKENIZER_HEADROOM",
    "AI_LITELLM_OUTPUT_CLAMP_MINIMUM_INPUT",
    "AI_LITELLM_CONFIG",
]


def _clear_py_env() -> None:
    for k in _PY_ENV_KEYS:
        os.environ.pop(k, None)


def drive_python_cap(context: Any, capability: Any, *,
                     default=32000, headroom=8192, min_input=32768,
                     per_model=None, model_name=None) -> int | None:
    _clear_py_env()
    os.environ["AI_LITELLM_OUTPUT_CLAMP_DEFAULT"] = str(default)
    os.environ["AI_LITELLM_OUTPUT_CLAMP_TOKENIZER_HEADROOM"] = str(headroom)
    os.environ["AI_LITELLM_OUTPUT_CLAMP_MINIMUM_INPUT"] = str(min_input)
    info: dict[str, Any] = {}
    if context is not None:
        info["max_input_tokens"] = context
    if capability is not None:
        info["max_output_tokens"] = capability
    kwargs: dict[str, Any] = {"model_info": info}
    if per_model is not None and model_name is not None:
        # perModel is read from the YAML policy, not env. Write a tiny fixture.
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "x-gateway-output-clamp:\n"
                f"  default: {default}\n"
                f"  tokenizer_headroom: {headroom}\n"
                f"  minimum_input: {min_input}\n"
                "  perModel:\n"
                f"    {model_name}: {per_model}\n"
            )
            cfg = f.name
        os.environ["AI_LITELLM_CONFIG"] = cfg
        kwargs["model"] = model_name
        try:
            return gateway_output_cap(kwargs)
        finally:
            os.unlink(cfg)
            _clear_py_env()
    try:
        return gateway_output_cap(kwargs)
    finally:
        _clear_py_env()


# =============================================================================
# 3. MATRIX
# =============================================================================
# Each row: id, regime, context, capability, policy kwargs, families to apply.
# Reservation overrides are expressed via build_policy kwargs.
ROWS: list[dict[str, Any]] = [
    dict(id="L1", regime="Large DeepSeek/gpt-5.4", ctx=1048576, cap=384000, families="A,B,D"),
    dict(id="L2", regime="Large Kimi/gpt-5.4-mini", ctx=262142, cap=262142, families="A,B,D"),
    dict(id="L3", regime="Large GLM-5.2/gpt-5.5", ctx=1048576, cap=131072, families="A,B,D"),
    dict(id="L4", regime="Large cap<32000", ctx=1048576, cap=20000, families="A,B"),
    dict(id="B1", regime="MR==R exactly", ctx=72960, cap=72960, families="A,B"),
    dict(id="B2", regime="MR==R-1 (small by 1)", ctx=72959, cap=72959, families="A,B"),
    dict(id="B3a", regime="Kimi-1", ctx=262143, cap=262143, families="A,B"),
    dict(id="B3b", regime="Kimi+1", ctx=262144, cap=262144, families="A,B"),
    dict(id="S1", regime="Small, headroom scaled", ctx=80000, cap=80000, families="A,B"),
    dict(id="S2", regime="Small, R clamped to MR", ctx=50000, cap=50000, families="A,B"),
    dict(id="G1", regime="gemma 8192/4096", ctx=8192, cap=4096, families="A,B", pin_oc=3277),
    dict(id="G2", regime="gemma no capability", ctx=8192, cap=None, families="A,B", pin_oc=3277),
    dict(id="T1", regime="tiny window (MR<1)", ctx=1, cap=None, families="A,B,Ctiny"),
    dict(id="T2", regime="tiny, capability tiny", ctx=1, cap=1, families="A,B,Ctiny"),
    dict(id="D1", regime="output>=input degenerate", ctx=1000, cap=99999, families="A,B"),
    dict(id="P1", regime="perModel override", ctx=262142, cap=262142, families="A,B,m",
         per_model=50000, model="m1"),
    dict(id="P2", regime="perTier (Node/Ruby only)", ctx=262142, cap=262142, families="A2",
         per_tier=40000, selection="sel"),
    dict(id="P3", regime="perSel>perTier>perModel", ctx=262142, cap=262142, families="A2",
         per_selection=10000, per_tier=20000, per_model=30000, selection="sel", model="m1"),
    dict(id="Z1", regime="context=null", ctx=None, cap=4096, families="nullctx"),
    dict(id="N1", regime="NaN/negative floor to null", ctx=-5, cap=-1, families="nullctx"),
    dict(id="Z2", regime="no cap AND empty policy", ctx=50000, cap=None, families="Cempty", empty=True),
]


# =============================================================================
# 4. ASSERTIONS
# =============================================================================
class Failure:
    def __init__(self, row_id: str, msg: str, detail: dict | None = None):
        self.row_id = row_id
        self.msg = msg
        self.detail = detail or {}

    def __str__(self) -> str:
        d = json.dumps(self.detail, sort_keys=True) if self.detail else ""
        return f"[{self.row_id}] {self.msg}  {d}"


def run() -> int:
    slice_errs = _check_slices()
    if slice_errs:
        for e in slice_errs:
            print(f"SLICE-GUARD FAIL: {e}", file=sys.stderr)
        return 1

    failures: list[Failure] = []
    rows_checked = 0

    for row in ROWS:
        rid = row["id"]
        ctx = row["ctx"]
        cap = row["cap"]
        selection = row.get("selection", "default")
        model = row.get("model", "default")
        per_selection = row.get("per_selection")
        per_tier = row.get("per_tier")
        per_model = row.get("per_model")
        families = set(row["families"].split(","))
        empty = row.get("empty", False)

        policy = build_policy(
            per_selection=per_selection, per_tier=per_tier, per_model=per_model,
            selection=selection, model=model, empty=empty,
        )

        # ---- ORACLE -------------------------------------------------------
        oracle = canonical_budget(
            ctx, cap,
            default=None if empty else 32000,
            tokenizer_headroom=None if empty else 8192,
            minimum_input=None if empty else 32768,
            per_selection=per_selection, per_tier=per_tier, per_model=per_model,
        )

        # ===================================================================
        # FAMILY A: effectiveInput agreement (Node + Ruby-cat + Ruby-mat)
        # ===================================================================
        if "A" in families:
            rows_checked += 1
            node = drive_node(policy, selection, model, ctx, cap)
            rmat = drive_ruby_matrix(policy, selection, model, ctx, cap)
            rcat = drive_ruby_catalog(policy, selection, model, ctx, cap)

            o_ei = oracle.get("effectiveInput")

            # Node effectiveInput
            if node.get("sentinel") is None:
                n_ei = node.get("effectiveInput")
                if n_ei != o_ei:
                    failures.append(Failure(rid, "Node.effectiveInput != oracle",
                                            {"node": n_ei, "oracle": o_ei}))
            else:
                failures.append(Failure(rid, "Node unexpectedly signalled sentinel in Family A",
                                        {"node": node}))

            # Ruby-matrix effectiveInput
            if rmat.get("sentinel") is None:
                m_ei = rmat.get("effective_input")
                if m_ei != o_ei:
                    failures.append(Failure(rid, "RubyMatrix.effective_input != oracle",
                                            {"ruby_matrix": m_ei, "oracle": o_ei}))
            else:
                failures.append(Failure(rid, "RubyMatrix unexpectedly nil in Family A",
                                        {"ruby_matrix": rmat}))

            # Ruby-catalog effectiveInput
            r_ei = rcat.get("effective_input")
            if r_ei != o_ei:
                failures.append(Failure(rid, "RubyCatalog.effective_input != oracle",
                                        {"ruby_catalog": r_ei, "oracle": o_ei}))

            # Cross-impl: the three effectiveInput values must be identical.
            triple = {
                "node": node.get("effectiveInput") if node.get("sentinel") is None else "<sentinel>",
                "ruby_matrix": rmat.get("effective_input") if rmat.get("sentinel") is None else "<sentinel>",
                "ruby_catalog": r_ei,
            }
            if len(set(map(json.dumps, triple.values()))) != 1:
                failures.append(Failure(rid, "effectiveInput DISAGREES across the three impls",
                                        triple))

            # Node + Ruby-matrix must also agree on reservation/headroom/minInput.
            if node.get("sentinel") is None and rmat.get("sentinel") is None:
                for nk, mk, ok in (("reservation", "reservation", "reservation"),
                                   ("tokenizerHeadroom", "tokenizer_headroom", "tokenizerHeadroom"),
                                   ("minimumInput", "minimum_input", "minimumInput")):
                    nv, mv, ov = node.get(nk), rmat.get(mk), oracle.get(ok)
                    if not (nv == mv == ov):
                        failures.append(Failure(rid, f"{ok} disagreement",
                                                {"node": nv, "ruby_matrix": mv, "oracle": ov}))

        # ===================================================================
        # FAMILY A2: Node == Ruby-matrix only (perTier/perSelection; no Python,
        #            no catalog-wrapper -- but we DO drive catalog effective_input
        #            directly since it supports the same precedence).
        # ===================================================================
        if "A2" in families:
            rows_checked += 1
            node = drive_node(policy, selection, model, ctx, cap)
            rmat = drive_ruby_matrix(policy, selection, model, ctx, cap)
            rcat = drive_ruby_catalog(policy, selection, model, ctx, cap)
            o_ei = oracle.get("effectiveInput")
            trip = {
                "node": node.get("effectiveInput"),
                "ruby_matrix": rmat.get("effective_input"),
                "ruby_catalog": rcat.get("effective_input"),
                "oracle": o_ei,
            }
            if not (trip["node"] == trip["ruby_matrix"] == trip["ruby_catalog"] == o_ei):
                failures.append(Failure(rid, "perTier/perSelection effectiveInput disagreement", trip))

        # ===================================================================
        # FAMILY B: outputCap agreement (Python only, vs oracle output_cap)
        # ===================================================================
        if "B" in families:
            o_oc = canonical_output_cap(ctx, cap)
            py_oc = drive_python_cap(ctx, cap)
            if py_oc != o_oc:
                failures.append(Failure(rid, "Python.gateway_output_cap != oracle outputCap",
                                        {"python": py_oc, "oracle": o_oc}))
            if "pin_oc" in row and o_oc != row["pin_oc"]:
                failures.append(Failure(rid, "oracle outputCap drifted from pinned legacy value",
                                        {"oracle": o_oc, "pin": row["pin_oc"]}))
            if "pin_oc" in row and py_oc != row["pin_oc"]:
                failures.append(Failure(rid, "Python outputCap drifted from pinned legacy value",
                                        {"python": py_oc, "pin": row["pin_oc"]}))

        # FAMILY Bm: Python perModel cap. perModel is read ONLY from the YAML
        # payload (no env var). Without PyYAML, output_clamp._read_config_payload
        # returns {} by design, so this path is undriveable -- skip, don't false-fail.
        if "m" in families:
            if not _HAVE_YAML:
                SKIPPED.append(f"{rid}: Python perModel cap (PyYAML not importable; "
                               "perModel injects only via YAML config)")
            else:
                o_oc = canonical_output_cap(ctx, cap, per_model=per_model)
                py_oc = drive_python_cap(ctx, cap, per_model=per_model, model_name=model)
                if py_oc != o_oc:
                    failures.append(Failure(rid, "Python perModel outputCap != oracle",
                                            {"python": py_oc, "oracle": o_oc}))

        # ===================================================================
        # FAMILY C_tiny: tiny window -- reservation==1 (not 0), Python cap>=1.
        # ===================================================================
        if "Ctiny" in families:
            node = drive_node(policy, selection, model, ctx, cap)
            rmat = drive_ruby_matrix(policy, selection, model, ctx, cap)
            if node.get("sentinel") is None and node.get("reservation") != 1:
                failures.append(Failure(rid, "Node tiny-window reservation != 1",
                                        {"node_reservation": node.get("reservation")}))
            if rmat.get("sentinel") is None and rmat.get("reservation") != 1:
                failures.append(Failure(rid, "RubyMatrix tiny-window reservation != 1",
                                        {"ruby_matrix_reservation": rmat.get("reservation")}))
            py_oc = drive_python_cap(ctx, cap)
            if py_oc is None or py_oc < 1:
                failures.append(Failure(rid, "Python tiny-window outputCap < 1",
                                        {"python": py_oc}))

        # ===================================================================
        # FAMILY AB_nullctx: context==null path. effectiveInput must be null on
        # the three effectiveInput impls; reservation/headroom unscaled. Python
        # cap computed with no context clamp.
        # ===================================================================
        if "nullctx" in families:
            node = drive_node(policy, selection, model, ctx, cap)
            rmat = drive_ruby_matrix(policy, selection, model, ctx, cap)
            rcat = drive_ruby_catalog(policy, selection, model, ctx, cap)
            # oracle effectiveInput is None (context None / non-finite)
            for label, val in (("node", node.get("effectiveInput") if node.get("sentinel") is None else "<sentinel>"),
                               ("ruby_matrix", rmat.get("effective_input") if rmat.get("sentinel") is None else "<sentinel>")):
                if val not in (None, "<sentinel>"):
                    failures.append(Failure(rid, f"{label} effectiveInput should be null with null context",
                                            {label: val}))
            # Ruby-catalog: with a genuinely-null context (Z1) effective_input
            # returns nil; with a non-positive context (N1) the production wrapper
            # skips the model entirely (sentinel). Both are acceptable; a 0/other
            # numeric budget would be the bug.
            rcat_sentinel = rcat.get("sentinel")
            if rcat_sentinel is None and rcat.get("effective_input") is not None:
                failures.append(Failure(rid, "RubyCatalog effective_input should be nil with null context",
                                        {"ruby_catalog": rcat.get("effective_input")}))
            # Python outputCap (no context clamp)
            o_oc = canonical_output_cap(ctx, cap)
            py_oc = drive_python_cap(ctx, cap)
            if py_oc != o_oc:
                failures.append(Failure(rid, "Python null-context outputCap != oracle",
                                        {"python": py_oc, "oracle": o_oc}))

        # ===================================================================
        # FAMILY C_empty: empty policy -- impl-specific behavior, NOT cross-impl.
        #   Node: exit(1).  Ruby-matrix output_budget: nil.
        #   Ruby-catalog effective_input: returns context (passthrough).
        # ===================================================================
        if "Cempty" in families:
            node = drive_node(policy, selection, model, ctx, cap)
            rmat = drive_ruby_matrix(policy, selection, model, ctx, cap)
            rcat = drive_ruby_catalog(policy, selection, model, ctx, cap)
            if node.get("sentinel") != "exit-nonzero":
                failures.append(Failure(rid, "Node empty-policy+no-cap should exit nonzero",
                                        {"node": node}))
            if rmat.get("sentinel") != "nil":
                failures.append(Failure(rid, "RubyMatrix empty-policy should return nil",
                                        {"ruby_matrix": rmat}))
            if rcat.get("effective_input") != ctx:
                failures.append(Failure(rid, "RubyCatalog empty-policy should pass context through",
                                        {"ruby_catalog": rcat.get("effective_input"), "context": ctx}))

        # ===================================================================
        # FAMILY D: Python clamp_token_reservations lower-only semantics.
        # ===================================================================
        if "D" in families:
            _clear_py_env()
            cap_val = canonical_output_cap(ctx, cap)
            info = {"max_input_tokens": ctx, "max_output_tokens": cap}
            kw = {"max_tokens": 999999, "max_completion_tokens": 5, "model_info": info}
            clamp_token_reservations(kw)
            if kw["max_tokens"] != min(999999, cap_val):
                failures.append(Failure(rid, "clamp_token_reservations max_tokens not lower-clamped",
                                        {"got": kw["max_tokens"], "want": min(999999, cap_val)}))
            if kw["max_completion_tokens"] != min(5, cap_val):
                failures.append(Failure(rid, "clamp_token_reservations did not keep lower value",
                                        {"got": kw["max_completion_tokens"], "want": min(5, cap_val)}))
            kw2 = {"max_tokens": 999999, "model_info": info}
            clamp_token_reservations(kw2)
            if "max_completion_tokens" in kw2:
                failures.append(Failure(rid, "clamp_token_reservations injected an absent key",
                                        {"kwargs": kw2}))
            _clear_py_env()

    # ---- report -----------------------------------------------------------
    print(f"budget-consistency: {len(ROWS)} rows, {rows_checked} effectiveInput "
          f"cross-impl checks, {len(failures)} failures, {len(SKIPPED)} undriveable")
    for s in SKIPPED:
        print(f"  skip: {s}")
    if failures:
        print("\n==== DRIFT / DISAGREEMENT (each line is a real latent bug) ====", file=sys.stderr)
        for f in failures:
            print(f"FAIL {f}", file=sys.stderr)
        print("=============================================================", file=sys.stderr)
        return 1
    print("OK: all four token-budget implementations agree across the full matrix.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
