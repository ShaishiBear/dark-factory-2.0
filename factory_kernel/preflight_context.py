"""Bounded committed facts for reasoning Preflight; never execute candidate code."""
from pathlib import Path
import subprocess

from .canonical import sha256_bytes, sha256_value
from .frontdoor_intent import IntentRefused
from .manifest import GIT_OID
from .preflight import bounded


def repository_context(root):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(Path(root)), *args], timeout=15).decode("utf-8")
    commit = git("rev-parse", "HEAD").strip()
    if not GIT_OID.fullmatch(commit):
        raise IntentRefused("Preflight repository needs an exact commit")
    names = git("ls-tree", "-r", "--name-only", commit).splitlines()
    if len(names) > 10000:
        raise IntentRefused("Preflight repository inventory exceeds bound")
    policies = {name: git("show", f"{commit}:{name}") for name in (".factory/architecture.json", "FACTORY_RULES.md")}
    return validate_context({"commit": commit, "tracked_files": names, "policy_files": policies})


def validate_context(raw):
    context = bounded(raw, 250000)
    if (set(context) != {"commit", "tracked_files", "policy_files"}
            or not isinstance(context["commit"], str) or not GIT_OID.fullmatch(context["commit"])
            or not isinstance(context["tracked_files"], list) or len(context["tracked_files"]) > 10000
            or any(not isinstance(name, str) or len(name) > 500 for name in context["tracked_files"])
            or not isinstance(context["policy_files"], dict)
            or set(context["policy_files"]) != {".factory/architecture.json", "FACTORY_RULES.md"}
            or any(not isinstance(value, str) or not value.strip() or len(value) > 100000
                   for value in context["policy_files"].values())):
        raise IntentRefused("invalid committed Preflight context")
    return context


def constraints(spec, context, acceptance_ids):
    return ([{"id": f"spec-constraint-{ordinal}", "source": "approved-spec", "text": text,
              "source_sha256": sha256_value(spec)} for ordinal, text in enumerate(spec["constraints"], 1)]
            + [{"id": "acceptance-" + criterion["id"], "source": "approved-item", "text": criterion["text"],
                "source_sha256": sha256_value(spec)} for requirement in spec["requirements"]
               for criterion in requirement["acceptance"] if criterion["id"] in acceptance_ids]
            + [{"id": f"non-goal-{ordinal}", "source": "approved-spec",
                "text": "Do not add excluded scope: " + text, "source_sha256": sha256_value(spec)}
               for ordinal, text in enumerate(spec["non_goals"], 1)]
            + [{"id": key, "source": name, "text": "Respect the supplied protected policy in full.",
                "source_sha256": sha256_bytes(context["policy_files"][name].encode())}
               for key, name in (("architecture-policy", ".factory/architecture.json"),
                                  ("factory-rules", "FACTORY_RULES.md"))])
