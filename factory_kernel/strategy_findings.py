"""Independent, data-only findings about exact protected architectural permissions.

This authority checks policy facts, never candidate output, prose or failure reason strings.
It establishes a registered dependency permission, not the root cause of a failed build.
"""
from pathlib import Path

from .canonical import sha256_bytes, sha256_value
from .exploration_repository import observe_protected_files
from .frontdoor_intent import IntentRefused
from .programme import parse_json
from .strategy_rules import KIND, validate_rules

AUTHORITY = "protected-architecture-permission-v1"
POLICY = ".factory/architecture.json"
PROGRAMS = ("factory_kernel/strategy_findings.py", "factory_kernel/strategy_rules.py",
            "factory_kernel/exploration_repository.py", "factory_kernel/canonical.py",
            "factory_kernel/programme.py", "factory_kernel/manifest.py", "factory_kernel/frontdoor_intent.py")


def establish(github, rules, claims, *, expected_revision, check_stop):
    rules = validate_rules(rules, claims)

    def analyse(revision, paths, read):
        if revision != expected_revision:
            raise IntentRefused("strategy finding and authenticated factory outcome have different revisions")
        programs = {}
        for name in PROGRAMS:
            raw = read(name, 50000)
            actual = Path(__file__).with_name(Path(name).name).read_bytes()
            # A stale host cannot evaluate new protected policy with a different authority.
            if raw.replace(b"\r\n", b"\n") != actual.replace(b"\r\n", b"\n"):
                raise IntentRefused("strategy authority program differs from protected source")
            programs[name] = sha256_bytes(raw)
        raw = read(POLICY, 100000)
        policy = parse_json(raw.decode("utf-8"))
        if (policy.get("version") != "1.0" or not isinstance(policy.get("layers"), list)
                or policy.get("graph", {}).get("enforce_new_forbidden_edges") is not True):
            raise IntentRefused("unsupported architecture policy")
        layers = {}
        for layer in policy["layers"]:
            key, allowed = layer["id"], layer["allowed_imports"]
            if (not isinstance(key, str) or key in layers or not isinstance(allowed, list)
                    or any(not isinstance(value, str) for value in allowed) or len(set(allowed)) != len(allowed)):
                raise IntentRefused("ambiguous architecture layer policy")
            layers[key] = allowed
        if any(not set(allowed) <= layers.keys() for allowed in layers.values()):
            raise IntentRefused("architecture policy references an unknown layer")
        findings = []
        for rule in rules:
            known = rule["from_layer"] in layers and rule["to_layer"] in layers
            permitted = rule["to_layer"] in layers.get(rule["from_layer"], []) if known else None
            findings.append({"rule_id": rule["id"], "rule_sha256": sha256_value(rule),
                "claim_id": rule["claim_id"], "kind": KIND, "expected": True, "observed": permitted,
                "status": "unresolved" if permitted is None else "supported" if permitted else "contradicted",
                "explanation": "Unknown layer is missing evidence, not a prohibited dependency." if not known else
                    "The registered new dependency is permitted by protected policy." if permitted else
                    "The selected strategy requires a new dependency that protected policy does not permit."})
        return {"authority": AUTHORITY, "authority_programs": programs, "revision": revision,
                "policy_sha256": sha256_bytes(raw), "findings": findings,
                "scope": "registered-layer-permissions-only", "failure_cause": "unresolved",
                "qualification_status": "UNPROVEN", "proof_reuse_allowed": False}

    return observe_protected_files(github, list(PROGRAMS), (POLICY,), analyse, check_stop=check_stop)
