"""Compile approved scope into bounded work; never infer approval from a model field.

The operational input is read from the protected default branch, not a worker checkout.
The proposal can only partition the specification's acceptance criteria and order tasks.
Structural coverage is not semantic qualification: generated issues still undergo triage.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from .canonical import sha256_value

ACTIVE_PATH = ".factory/programmes/active.json"
MARKER = "<!-- dark-factory-programme:"
MAX_ITEMS = 50


class ProgrammeRefused(ValueError):
    pass


def _object(value: Any, fields: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ProgrammeRefused(f"{name} must have exactly {sorted(fields)}")
    return value


def _text(value: Any, name: str, limit: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ProgrammeRefused(f"{name} must be nonempty text of at most {limit} characters")
    # Rendered issue structure and kernel markers belong to the compiler.
    if "<!--" in value or re.search(r"(?im)^\s*(?:Blocked by|Part of|Fixes|Closes)\b", value):
        raise ProgrammeRefused(f"{name} contains reserved issue control syntax")
    return value


def _id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ProgrammeRefused("invalid programme, requirement, acceptance or item ID")
    return value


def _list(value: Any, name: str, *, empty: bool = False) -> list:
    if not isinstance(value, list) or len(value) > MAX_ITEMS or (not empty and not value):
        raise ProgrammeRefused(f"{name} must be a bounded list (maximum {MAX_ITEMS})")
    return value


def _ids(value: Any, name: str, *, empty: bool = False) -> list[str]:
    result = [_id(x) for x in _list(value, name, empty=empty)]
    if len(set(result)) != len(result):
        raise ProgrammeRefused(f"{name} contains duplicate IDs")
    return sorted(result)


@dataclass(frozen=True)
class Programme:
    spec: dict
    items: tuple[dict, ...]
    app_login: str
    sha256: str

    def marker(self, item: Mapping) -> str:
        return f"{MARKER}{self.sha256}:{item['id']} -->"

    def render(self, item: Mapping, numbers: Mapping[str, int]) -> tuple[str, str]:
        acceptance = {
            ac["id"]: (req["id"], ac["text"])
            for req in self.spec["requirements"] for ac in req["acceptance"]
        }
        lines = [self.marker(item), "", self.spec["outcome"], "",
                 f"Specification: {self.spec['id']} v{self.spec['revision']}",
                 f"Specification SHA256: {sha256_value(self.spec)}", "", "## Acceptance criteria"]
        for ac in item["acceptance"]:
            requirement, text = acceptance[ac]
            lines.append(f"- {ac} ({requirement}): {text}")
        for heading, key in (("Hard constraints", "constraints"), ("Out of scope", "non_goals")):
            lines.extend(["", f"## {heading}", *[f"- {x}" for x in self.spec[key]]])
        if item["blocked_by"]:
            lines.extend(["", *[f"Blocked by: #{numbers[x]}" for x in item["blocked_by"]]])
        lines.extend(["", "This is a compiled programme candidate. Normal triage and all existing "
                      "factory proof and permission rules apply."])
        body = "\n".join(lines) + "\n"
        if len(body) > 11000:
            raise ProgrammeRefused("compiled issue exceeds the triage input window")
        return f"{self.spec['title']} [{item['id']}]", body


def compile_programme(raw: Any, *, repository: str) -> Programme:
    root = _object(raw, {"version", "spec", "proposal", "app_login"}, "programme input")
    if root["version"] != "1.0":
        raise ProgrammeRefused("programme version must be 1.0")
    spec = _object(root["spec"], {"id", "revision", "repository", "title", "outcome",
                                      "requirements", "constraints", "non_goals"}, "spec")
    _id(spec["id"])
    if type(spec["revision"]) is not int or spec["revision"] < 1:
        raise ProgrammeRefused("spec revision must be a positive integer")
    if spec["repository"] != repository:
        raise ProgrammeRefused("spec repository differs from the configured repository")
    _text(spec["title"], "title", 100)
    _text(spec["outcome"], "outcome")
    for key in ("constraints", "non_goals"):
        for text in _list(spec[key], key):
            _text(text, key)
    requirements: set[str] = set()
    acceptance: set[str] = set()
    for req in _list(spec["requirements"], "requirements"):
        _object(req, {"id", "acceptance"}, "requirement")
        if _id(req["id"]) in requirements:
            raise ProgrammeRefused("duplicate requirement ID")
        requirements.add(req["id"])
        for ac in _list(req["acceptance"], "acceptance"):
            _object(ac, {"id", "text"}, "acceptance criterion")
            if _id(ac["id"]) in acceptance:
                raise ProgrammeRefused("duplicate acceptance ID")
            acceptance.add(ac["id"])
            _text(ac["text"], "acceptance text")
    if len(acceptance) > MAX_ITEMS:
        raise ProgrammeRefused("spec has too many acceptance criteria")
    proposal = _object(root["proposal"], {"spec_sha256", "items"}, "proposal")
    if proposal["spec_sha256"] != sha256_value(spec):
        raise ProgrammeRefused("proposal is bound to a different spec hash")
    items: dict[str, dict] = {}
    owners: set[str] = set()
    for raw_item in _list(proposal["items"], "items"):
        _object(raw_item, {"id", "acceptance", "blocked_by"}, "item")
        key = _id(raw_item["id"])
        if key in items:
            raise ProgrammeRefused("duplicate item ID")
        acs = _ids(raw_item["acceptance"], "item acceptance")
        if not set(acs) <= acceptance or owners.intersection(acs):
            raise ProgrammeRefused("unknown or multiply owned acceptance criteria")
        owners.update(acs)
        items[key] = {"id": key, "acceptance": acs,
                      "blocked_by": _ids(raw_item["blocked_by"], "blockers", empty=True)}
    if owners != acceptance:
        raise ProgrammeRefused(f"uncovered acceptance criteria: {sorted(acceptance - owners)}")
    for item in items.values():
        if not set(item["blocked_by"]) <= items.keys():
            raise ProgrammeRefused("unknown blocker")
    ordered: list[dict] = []
    done: set[str] = set()
    while len(done) < len(items):
        ready = sorted(k for k, v in items.items()
                       if k not in done and set(v["blocked_by"]) <= done)
        if not ready:
            raise ProgrammeRefused("programme dependency cycle")
        for key in ready:
            ordered.append(items[key])
            done.add(key)
    login = root["app_login"]
    if not isinstance(login, str) or not re.fullmatch(r"[a-zA-Z0-9-]+\[bot\]", login):
        raise ProgrammeRefused("app_login must name the installation's GitHub bot")
    identity = {"spec": spec, "items": ordered, "app_login": login, "version": "1.0"}
    result = Programme(spec, tuple(ordered), login, sha256_value(identity))
    for item in ordered:
        result.render(item, {k: 999999999 for k in items})
    return result


def parse_json(text: str) -> Any:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProgrammeRefused(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    if len(text) > 250000:
        raise ProgrammeRefused("programme input exceeds 250000 characters")
    try:
        return json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise ProgrammeRefused("invalid programme JSON") from exc
