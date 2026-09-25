"""De-identification policy: which action applies to a detected span.

A policy is JSON kept **outside git** (by default ``docs/private/dafa_policy.json``), because the
standards it encodes are internal. This module only knows the shape, never the content::

    {"name": "...", "version": "...",
     "rules": {
       "<key>": {"action": "remove" | "pseudonymize" | "retain"},
       "<key>": {"action": "generalize", "method": "<transform name>"},
       "<key>": {"action": "flag", "options": ["remove", "pseudonymize", ...]},
       "<key>": {"by": "<context key>",
                 "cases": {"<value>": <rule>, ...},
                 "unknown": <rule>}}}

Keys are span ``type`` or ``identifier`` values; ``type`` is looked up first so a finer type
(``url`` under ``other_id``) can carry its own rule. ``by`` rules pick a case from context the
judge supplies (e.g. whether a date is a date of birth).

Fail closed: no matching rule, an unknown context value, or a ``flag`` action all set
``needs_review``. The policy only names an action; code elsewhere performs it, and the model never
generates replacement values.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

ACTIONS = frozenset({"remove", "pseudonymize", "retain", "generalize", "flag"})
ENV_VAR = "SLMJEV_POLICY"
DEFAULT_RELPATH = Path("docs") / "private" / "dafa_policy.json"


# --- validation ----------------------------------------------------------------------------


def _check_leaf(key: str, rule: object) -> None:
    if not isinstance(rule, Mapping):
        raise ValueError(f"{key}: rule must be an object")
    action = rule.get("action")
    if action not in ACTIONS:
        raise ValueError(f"{key}: unknown action {action!r}")
    if action == "generalize" and not rule.get("method"):
        raise ValueError(f"{key}: generalize needs a method")
    if action == "flag":
        opts = rule.get("options")
        if not opts or not isinstance(opts, list) or not set(opts) <= ACTIONS - {"flag"}:
            raise ValueError(f"{key}: flag needs options drawn from {sorted(ACTIONS - {'flag'})}")


def _check_rule(key: str, rule: object) -> None:
    if isinstance(rule, Mapping) and "by" in rule:
        cases = rule.get("cases")
        if not isinstance(cases, Mapping) or not cases:
            raise ValueError(f"{key}: 'by' rule needs non-empty cases")
        if "unknown" not in rule:
            raise ValueError(f"{key}: 'by' rule needs an 'unknown' fallback")
        for case, sub in cases.items():
            _check_leaf(f"{key}.{case}", sub)
        _check_leaf(f"{key}.unknown", rule["unknown"])
    else:
        _check_leaf(key, rule)


def validate(pol: object) -> dict:
    """Return ``pol`` if it is a well-formed policy, else raise ValueError."""
    if not isinstance(pol, Mapping) or not isinstance(pol.get("rules"), Mapping):
        raise ValueError("policy needs a 'rules' object")
    for key, rule in pol["rules"].items():
        _check_rule(key, rule)
    return dict(pol)


# --- loading -------------------------------------------------------------------------------


def _discover(start: Path) -> Path | None:
    for d in (start, *start.parents):
        cand = d / DEFAULT_RELPATH
        if cand.is_file():
            return cand
    return None


def load_policy(path: str | os.PathLike | None = None, *, search_from: Path | None = None) -> dict:
    """Load and validate a policy from ``path``, ``$SLMJEV_POLICY``, or the nearest
    ``docs/private/dafa_policy.json`` above ``search_from`` (default: the working directory)."""
    if path is None:
        path = os.environ.get(ENV_VAR) or _discover((search_from or Path.cwd()).resolve())
    if path is None or not Path(path).is_file():
        raise FileNotFoundError(f"no policy file (pass a path or set {ENV_VAR})")
    return validate(json.loads(Path(path).read_text(encoding="utf-8")))


# --- resolution ----------------------------------------------------------------------------


def resolve(span: Mapping, pol: Mapping, context: Mapping | None = None) -> dict:
    """The action for one span: ``policy_key``, ``action``, ``method``, ``options``,
    ``needs_review``."""
    rules = pol["rules"]
    key = next((k for k in (span.get("type"), span.get("identifier")) if k in rules), None)
    if key is None:
        return {"policy_key": None, "action": "flag", "method": None,
                "options": sorted(ACTIONS - {"flag"}), "needs_review": True}
    rule, review = rules[key], False
    if "by" in rule:
        value = (context or {}).get(rule["by"])
        if value in rule["cases"]:
            rule = rule["cases"][value]
        else:
            rule, review = rule["unknown"], True
    action = rule["action"]
    return {"policy_key": key, "action": action, "method": rule.get("method"),
            "options": list(rule.get("options", [])),
            "needs_review": review or action == "flag"}
