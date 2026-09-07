# Dark Factory 2.0 — Canonical Data Contracts

**Status:** owner-approved target design; implement incrementally, not as a flag-day rewrite.  
**Purpose:** prevent future features from inventing incompatible representations of specifications, graph state, candidates, evidence, programme items, factory handoffs, trajectories, leases, capabilities and attestations.

This document supplements `DARK_FACTORY_2_TARGET_ARCHITECTURE_AND_OVERSEER.md`.

---

## 1. Compatibility rule

The current factory already has valuable exact-hash and cross-artifact bindings for contract/context/design/RED/GREEN/provenance.

Do not replace proven current formats merely to make the new system aesthetically uniform.

Target layering:

```text
NEW PROJECT / SPEC / PREFLIGHT LAYER
              ↓
         factory handoff
              ↓
CURRENT TRUSTED FACTORY ARTIFACTS
```

Migrate existing trusted artifacts only where a concrete simplification or trust benefit is demonstrated.

---

## 2. Canonical JSON rules

Durable canonical objects should use:

1. explicit schema identifier;
2. explicit schema version;
3. UTF-8;
4. deterministic canonical serialization;
5. stable opaque object identity;
6. UTC ISO-8601 timestamps;
7. explicit enums rather than free-form trust-sensitive strings;
8. repository-relative paths where semantic identity involves code;
9. canonical SHA-256 bindings where identity/integrity matters.

Do not infer missing trust-critical fields.

Do not make a top-level `sha256` self-referential. Compute over the canonical object without that self-hash, then store the digest in envelope/adjacent metadata.

---

## 3. ID convention

Use opaque globally unique IDs, recognisable by type for debugging, e.g.:

```text
proj_  spec_  req_  q_  asm_  cand_  rec_
ev_    evid_ prog_ item_ run_ lease_ att_
cap_   inc_   lesson_ traj_
```

Do not make semantics depend on sequential database integers.

A superseding semantic object receives a new ID.

---

## 4. Common provenance

Where origin matters:

```json
{
  "actor_type": "user",
  "actor_id": "user",
  "source": "front-door",
  "created_at": "2026-09-07T12:00:00Z"
}
```

Initial actor types:

`user`, `system`, `agent`, `authority`, `import`.

Agent/model metadata may include model, method version and prompt version. Deterministic events do not require fabricated model metadata.

---

## 5. Reference object

Use references rather than copying whole objects across domains.

Minimum:

```json
{"id":"q_123","type":"question"}
```

Version/hash-bound reference:

```json
{
  "id":"spec_123",
  "type":"approved-spec",
  "version":3,
  "sha256":"..."
}
```

Repository subject:

```json
{
  "repo":"ShaishiBear/dark-factory-2.0",
  "revision":"full-git-object-id"
}
```

---

# PRODUCT INTENT CONTRACTS

## 6. Approved Specification

An approved specification is immutable once approved.

Canonical shape should support:

```json
{
  "schema":"dark-factory/approved-spec",
  "schema_version":"1.0",
  "spec_id":"spec_...",
  "project_id":"proj_...",
  "version":3,
  "supersedes":{"spec_id":"spec_...","version":2,"sha256":"..."},
  "mission":"...",
  "users":[{"id":"userclass_1","description":"..."}],
  "outcomes":[{"id":"outcome_1","statement":"..."}],
  "requirements":[{
    "id":"req_...",
    "statement":"...",
    "kind":"functional",
    "priority":"required",
    "acceptance":["observable condition"],
    "provenance":{}
  }],
  "hard_constraints":[{"id":"constraint_...","statement":"...","provenance":{}}],
  "preferences":[{"id":"pref_...","statement":"...","rank":1,"provenance":{}}],
  "non_goals":[{"id":"nongoal_...","statement":"..."}],
  "acceptance_criteria":[{
    "id":"PAC-1",
    "statement":"...",
    "requirement_ids":["req_..."]
  }],
  "open_exploration":[{"question_id":"q_..."}],
  "approved_by":{"actor_type":"user","actor_id":"user"},
  "approved_at":"..."
}
```

Promotion of a question/idea into `requirements` or `hard_constraints` requires explicit user approval.

A change creates `SPEC vN+1`; do not mutate the old approved meaning.

---

## 7. Interview Ledger Entry

The interview ledger is historical process, not approved intent.

```json
{
  "schema":"dark-factory/interview-entry",
  "schema_version":"1.0",
  "entry_id":"int_...",
  "project_id":"proj_...",
  "sequence":17,
  "question":{"text":"...","asked_by":"system"},
  "recommendation":{"text":"...","present":true},
  "answer":{"text":"...","actor":"user"},
  "proposed_classification":"possible-requirement",
  "classification_status":"exploration-only",
  "resulting_refs":[{"id":"q_...","type":"question"}],
  "created_at":"..."
}
```

Proposed classification is advisory until spec approval changes the authoritative spec.

Preserve original user wording separately from normalised interpretation when useful.

---

# PROJECT DECISION GRAPH CONTRACTS

## 8. Graph Command

All user/agent graph mutations should enter through commands:

```json
{
  "schema":"dark-factory/graph-command",
  "schema_version":"1.0",
  "command_id":"cmd_...",
  "project_id":"proj_...",
  "actor":{"actor_type":"user","actor_id":"user"},
  "idempotency_key":"...",
  "expected_project_version":184,
  "operation":"add-question",
  "payload":{},
  "created_at":"..."
}
```

Rules:

- idempotency key mandatory;
- expected project version mandatory for mutation;
- stale conflicting writes fail;
- replay of same idempotency key returns original outcome;
- accepted commands generate canonical events.

---

## 9. Project Event

Canonical project history is append-only.

```json
{
  "schema":"dark-factory/project-event",
  "schema_version":"1.0",
  "event_id":"ev_...",
  "project_id":"proj_...",
  "project_version":185,
  "event_type":"question-added",
  "subject":{"id":"q_...","type":"question"},
  "caused_by":{"command_id":"cmd_..."},
  "spec_ref":{"spec_id":"spec_...","version":3,"sha256":"..."},
  "repo_ref":{"repo":"ShaishiBear/dark-factory-2.0","revision":"..."},
  "payload":{},
  "provenance":{},
  "created_at":"..."
}
```

`project_version` is the canonical mutation order for a project. Do not use wall-clock timestamps as concurrency order.

---

## 10. Materialised Node / Edge

Materialised nodes and edges are rebuildable projections, not canonical history.

Node envelope:

```json
{
  "node_id":"q_...",
  "kind":"question",
  "project_id":"proj_...",
  "status":"active",
  "created_event_id":"ev_...",
  "last_event_id":"ev_...",
  "created_project_version":112,
  "updated_project_version":185,
  "origin":{"actor_type":"user"},
  "spec_ref":{"spec_id":"spec_...","version":3,"sha256":"..."}
}
```

Edge:

```json
{
  "edge_id":"edge_...",
  "project_id":"proj_...",
  "type":"ASSUMES",
  "from":{"id":"rec_...","type":"recommendation"},
  "to":{"id":"asm_...","type":"assumption"},
  "created_event_id":"ev_...",
  "status":"active"
}
```

Initial edge registry:

`PART_OF`, `DEPENDS_ON`, `ADDRESSES`, `CONSTRAINED_BY`, `ASSUMES`, `SUPPORTED_BY`, `CONTRADICTED_BY`, `CANDIDATE_FOR`, `RECOMMENDED_FOR`, `REJECTED_FOR`, `IMPLEMENTED_AS`, `INVALIDATES`, `SUPERSEDES`, `RECONSIDERED_DUE_TO`.

Do not allow arbitrary trust-relevant edge strings.

---

## 11. Question

```json
{
  "schema":"dark-factory/question",
  "schema_version":"1.0",
  "question_id":"q_...",
  "project_id":"proj_...",
  "parent_question_id":null,
  "feature_id":"feature_...",
  "text":"How should session state be stored?",
  "origin":{"actor_type":"system"},
  "classification":"exploration",
  "status":"open",
  "importance":"consequential",
  "candidate_ids":["cand_..."],
  "created_at":"..."
}
```

Graph classification does not silently mutate the approved specification.

---

## 12. Assumption

Assumptions are first-class because later evidence must be able to invalidate only dependent decisions.

```json
{
  "schema":"dark-factory/assumption",
  "schema_version":"1.0",
  "assumption_id":"asm_...",
  "project_id":"proj_...",
  "statement":"Expected peak concurrent users remain below 5,000.",
  "scope":{"feature_ids":["feature_..."],"question_ids":["q_..."]},
  "status":"active",
  "basis":[{"evidence_id":"evid_..."}],
  "confidence":{"type":"judgement","level":"medium"},
  "revisit_condition":"Observed peak concurrency exceeds 4,000.",
  "origin":{},
  "created_at":"...",
  "invalidated_by":null
}
```

Statuses: `active`, `challenged`, `invalidated`, `superseded`.

Only `invalidated` deterministically stales dependent recommendations.

---

# PREFLIGHT CONTRACTS

## 13. Candidate

```json
{
  "schema":"dark-factory/candidate",
  "schema_version":"1.0",
  "candidate_id":"cand_...",
  "project_id":"proj_...",
  "question_id":"q_...",
  "name":"Event-sourced session state",
  "description":"...",
  "origin":{"actor_type":"system"},
  "status":"evaluating",
  "assumption_ids":["asm_..."],
  "constraint_ids":["constraint_..."],
  "evidence_ids":[],
  "prediction_ids":[],
  "probe_ids":[],
  "created_at":"..."
}
```

Statuses: `generated`, `evaluating`, `promising`, `pareto-finalist`, `recommended`, `pruned`, `rejected-by-reality`, `superseded`.

Do not use `PASS` for exploration candidates.

A user-added direction is the same object with user provenance and receives identical evaluation rules.

---

## 14. Exploration Evidence

```json
{
  "schema":"dark-factory/exploration-evidence",
  "schema_version":"1.0",
  "evidence_id":"evid_...",
  "project_id":"proj_...",
  "candidate_id":"cand_...",
  "class":"measurement",
  "kind":"dependency-count",
  "claim":"Candidate introduces zero runtime dependencies.",
  "value":{"number":0,"unit":"dependencies"},
  "method":{"authority":"repo-static-analysis-v1"},
  "repo_ref":{"revision":"..."},
  "artifact_sha256":"...",
  "created_at":"..."
}
```

Evidence class registry initially:

`measurement`, `prediction`, `judgement`, `probe-result`, `external-source`.

Do not collapse these semantic classes.

---

## 15. Prediction

```json
{
  "schema":"dark-factory/prediction",
  "schema_version":"1.0",
  "prediction_id":"pred_...",
  "project_id":"proj_...",
  "candidate_id":"cand_...",
  "target":"first-pass-qualification",
  "prediction":{"type":"probability","value":0.72},
  "uncertainty":{"method":"uncalibrated","level":"high"},
  "model":{"id":"...","version":"..."},
  "features_sha256":"...",
  "created_at":"...",
  "resolved":false,
  "actual_outcome_ref":null
}
```

Do not fabricate confidence intervals before calibration exists.

---

## 16. Disposable Probe

```json
{
  "schema":"dark-factory/probe",
  "schema_version":"1.0",
  "probe_id":"probe_...",
  "project_id":"proj_...",
  "candidate_id":"cand_...",
  "question":"Does library X support atomic operation Y?",
  "scope":"disposable",
  "budget":{"estimated_cost":0.12,"currency":"USD","max_wall_seconds":60},
  "execution":{"environment_id":"...","base_revision":"..."},
  "result":{"status":"completed","summary":"...","evidence_id":"evid_..."},
  "production_authority":false
}
```

A probe never qualifies production work.

---

## 17. Candidate Evaluation

Maintain dimensions instead of one fake universal score:

```json
{
  "schema":"dark-factory/candidate-evaluation",
  "schema_version":"1.0",
  "evaluation_id":"eval_...",
  "candidate_id":"cand_...",
  "hard_constraints":[{"constraint_id":"constraint_...","status":"satisfied","evidence_ids":["evid_..."]}],
  "measurements":[{"metric":"dependency-additions","value":0,"evidence_id":"evid_..."}],
  "predictions":[{"prediction_id":"pred_..."}],
  "judgements":[{"criterion":"conceptual-simplicity","assessment":"strong","reason":"..."}],
  "dominance":{"dominated":false,"dominated_by":[]},
  "evaluated_at":"..."
}
```

---

## 18. Decision Policy and Recommendation

Pre-register comparison policy where practical:

```json
{
  "schema":"dark-factory/decision-policy",
  "schema_version":"1.0",
  "policy_id":"policy_...",
  "question_id":"q_...",
  "hard_constraints":["constraint_..."],
  "priority_order":["reliability","minimal-complexity","maintenance-cost","latency"],
  "tie_break":"prefer fewer new dependencies",
  "created_before_final_evaluation":true
}
```

Recommendation:

```json
{
  "schema":"dark-factory/recommendation",
  "schema_version":"1.0",
  "recommendation_id":"rec_...",
  "project_id":"proj_...",
  "question_id":"q_...",
  "candidate_id":"cand_...",
  "status":"current",
  "reason":"...",
  "assumption_ids":["asm_..."],
  "constraint_ids":["constraint_..."],
  "evidence_ids":["evid_..."],
  "prediction_ids":["pred_..."],
  "alternatives_retained":["cand_..."],
  "revisit_conditions":["asm_..."],
  "decision_policy_ref":"policy_...",
  "created_at":"..."
}
```

A recommendation cannot remain `current` after a required assumption is invalidated.

---

# PROGRAMME CONTRACTS

## 19. Programme Proposal

```json
{
  "schema":"dark-factory/programme",
  "schema_version":"1.0",
  "programme_id":"prog_...",
  "project_id":"proj_...",
  "spec_ref":{"spec_id":"spec_...","version":3,"sha256":"..."},
  "version":4,
  "supersedes":{"programme_id":"prog_...","version":3},
  "items":[{
    "item_id":"item_...",
    "title":"...",
    "kind":"feature",
    "requirement_ids":["req_..."],
    "acceptance_ids":["PAC-1"],
    "depends_on":[],
    "status":"planned"
  }],
  "coverage":{"requirements":{"req_...":["item_..."]},"acceptance":{"PAC-1":["item_..."]}},
  "created_at":"..."
}
```

This is an untrusted proposed programme until compiled.

---

## 20. Compiled Programme DAG

```json
{
  "schema":"dark-factory/compiled-programme",
  "schema_version":"1.0",
  "programme_id":"prog_...",
  "programme_version":4,
  "spec_sha256":"...",
  "nodes":[{
    "item_id":"item_...",
    "depends_on":[],
    "requirement_ids":["req_..."],
    "acceptance_ids":["PAC-1"]
  }],
  "topological_order":["item_..."],
  "coverage_complete":true,
  "compiler_version":"..."
}
```

Compiler fails closed on cycles, missing targets, incomplete coverage, malformed references, duplicate ownership where prohibited and hidden spec mutation.

---

## 21. Factory Handoff

Narrow bridge from project/preflight domain into the existing trusted factory:

```json
{
  "schema":"dark-factory/factory-handoff",
  "schema_version":"1.0",
  "handoff_id":"handoff_...",
  "project_id":"proj_...",
  "programme_item_id":"item_...",
  "spec_ref":{"spec_id":"spec_...","version":3,"sha256":"..."},
  "question_id":"q_...",
  "candidate_id":"cand_...",
  "recommendation_id":"rec_...",
  "technical_direction":"...",
  "hard_constraint_ids":["constraint_..."],
  "assumption_ids":["asm_..."],
  "exploration_evidence_refs":["evid_..."],
  "qualification_status":"UNPROVEN",
  "created_at":"..."
}
```

Absolute rule: Preflight never creates a handoff claiming PASS.

The real factory independently compiles its current contract/design/RED/GREEN/review evidence.

---

# TRUSTED EXECUTION CONTRACTS

## 22. Factory Run

```json
{
  "schema":"dark-factory/factory-run",
  "schema_version":"1.0",
  "run_id":"run_...",
  "issue":184,
  "project_id":"proj_...",
  "programme_item_id":"item_...",
  "handoff_id":"handoff_...",
  "spec_sha256":"...",
  "base_revision":"...",
  "state":"RED_PROVEN",
  "current_stage":"implement",
  "started_at":"...",
  "finished_at":null
}
```

This is orchestration state; trusted evidence remains in exact authority artifacts/attestations.

---

## 23. Transition Request / Decision

Conceptual future kernel interface:

```json
{
  "schema":"dark-factory/transition-request",
  "schema_version":"1.0",
  "request_id":"tr_...",
  "run_id":"run_...",
  "from_state":"RED_PROVEN",
  "to_state":"IMPLEMENTED",
  "subject":{"issue":184,"revision":"...","spec_sha256":"..."},
  "evidence_refs":["att_..."],
  "capability_ref":"cap_...",
  "policy_sha256":"..."
}
```

Decision:

```json
{
  "schema":"dark-factory/transition-decision",
  "schema_version":"1.0",
  "request_id":"tr_...",
  "allowed":true,
  "reason_code":"requirements-satisfied",
  "new_state":"IMPLEMENTED",
  "decision_sha256":"..."
}
```

The orchestrator cannot manufacture `allowed=true`.

---

## 24. Authority Attestation

Build on current command/certification evidence rather than replacing it abruptly.

```json
{
  "schema":"dark-factory/authority-attestation",
  "schema_version":"1.0",
  "attestation_id":"att_...",
  "authority":{"id":"green-replay","version":"3"},
  "subject":{"issue":184,"run_id":"run_...","revision":"...","spec_sha256":"..."},
  "policy_sha256":"...",
  "result":"PASS",
  "command_evidence":{
    "authority_id":"...",
    "argv_sha256":"...",
    "stdout_sha256":"...",
    "stderr_sha256":"...",
    "exit_code":0
  },
  "evidence_sha256":"...",
  "created_at":"..."
}
```

Result enum: `PASS`, `FAIL`, `INDETERMINATE`.

`INDETERMINATE` never satisfies a required PASS.

Where independence matters, metadata may describe candidate write access, prior-verdict visibility and learning-store access, but the declaration is not itself enforcement.

---

## 25. Capability Grant

```json
{
  "schema":"dark-factory/capability-grant",
  "schema_version":"1.0",
  "capability_id":"cap_...",
  "run_id":"run_...",
  "actor":{"role":"test-author","instance_id":"worker_..."},
  "scope":{"read":["app/**"],"write":["app/**/__tests__/**"]},
  "tools":["read","search","edit"],
  "network":{"allowed":false,"destinations":[]},
  "git":{"read":false,"write":false,"commit":false,"merge":false},
  "trust_root_write":false,
  "issued_at":"...",
  "expires_at":"...",
  "policy_sha256":"..."
}
```

The worker/provider must not be trusted to enforce its own capability.

---

## 26. Lease v2

Evolve the current useful lease identity/heartbeat/stage/state semantics rather than replacing them blindly.

```json
{
  "schema":"dark-factory/lease",
  "schema_version":"2.0",
  "lease_id":"lease_...",
  "resource":{"type":"programme-item","id":"item_..."},
  "owner":{"type":"factory-run","id":"run_..."},
  "purpose":"implementation",
  "state":"active",
  "acquired_at":"...",
  "heartbeat_at":"...",
  "expires_at":"...",
  "generation":4,
  "handoff_ref":null
}
```

States: `active`, `released`, `expired`, `reaped`, `handed-off`.

`generation` is fencing. A stale owner from generation N must be unable to mutate after generation N+1 acquires the resource.

---

# TELEMETRY / LEARNING CONTRACTS

## 27. Trajectory

Every meaningful attempt gets a trajectory even if no PR is created.

```json
{
  "schema":"dark-factory/trajectory",
  "schema_version":"1.0",
  "trajectory_id":"traj_...",
  "run_id":"run_...",
  "project_id":"proj_...",
  "issue":184,
  "spec_sha256":"...",
  "base_revision":"...",
  "started_at":"...",
  "ended_at":"...",
  "outcome":"failure",
  "terminal_reason":"test-author-cap",
  "stages":[{
    "stage":"contract",
    "attempt":1,
    "model":"...",
    "method_version":"...",
    "prompt_version":"...",
    "turns":8,
    "wall_seconds":94.2,
    "cost":{"currency":"USD","amount":0.82},
    "result":"success",
    "artifact_refs":[]
  }],
  "final_revision":null,
  "merged":false
}
```

Keep repeated stage attempts; do not overwrite failed attempts.

Trajectory evidence is observability/learning, not merge authority.

---

## 28. Prediction Outcome

Keep prediction immutable and resolve it with a separate outcome record:

```json
{
  "schema":"dark-factory/prediction-outcome",
  "schema_version":"1.0",
  "prediction_id":"pred_...",
  "candidate_id":"cand_...",
  "factory_run_id":"run_...",
  "predicted":{"first_pass_probability":0.72,"estimated_cost":8.50,"estimated_repairs":1},
  "actual":{"qualified":true,"cost":11.20,"repairs":2},
  "errors":{"cost":2.70,"repairs":1},
  "resolved_at":"..."
}
```

---

## 29. Strategy Outcome

Strategy-level terminal failure is distinct from ordinary repairable implementation failure:

```json
{
  "schema":"dark-factory/strategy-outcome",
  "schema_version":"1.0",
  "outcome_id":"stratout_...",
  "candidate_id":"cand_...",
  "factory_run_id":"run_...",
  "result":"STRATEGY_REJECTED_BY_REALITY",
  "authority_refs":["att_..."],
  "reason_code":"architecture-incompatible",
  "reason":"...",
  "created_at":"..."
}
```

This returns control to Preflight.

---

## 30. Engineering Lesson

Derived lesson, never raw history:

```json
{
  "schema":"dark-factory/engineering-lesson",
  "schema_version":"1.0",
  "lesson_id":"lesson_...",
  "domain":"factory",
  "scope":{"repo":"ShaishiBear/dark-factory-2.0","task_classes":["frontend-lifecycle"]},
  "statement":"...",
  "supporting_trajectory_ids":["traj_..."],
  "contradicting_trajectory_ids":[],
  "confidence":{"method":"empirical","level":"medium"},
  "falsifier":"...",
  "status":"active",
  "created_at":"...",
  "last_applicable_at":"...",
  "dormancy_after_applicable_runs":100
}
```

Domains initially: `factory`, `software`.

Learning retrieval is structurally forbidden for blinded judges/independent certifiers where independence requires blindness.

---

# FEEDBACK / IMPLEMENTATION GRAPH CONTRACTS

## 31. Implementation Component

```json
{
  "schema":"dark-factory/implementation-component",
  "schema_version":"1.0",
  "component_id":"comp_...",
  "project_id":"proj_...",
  "kind":"service",
  "name":"SessionStore",
  "repo_locations":["app/backend/session_store.py"],
  "introduced_by":{"factory_run_id":"run_...","revision":"..."},
  "implements":[{"id":"rec_...","type":"recommendation"}],
  "status":"active"
}
```

Initial kinds: `module`, `service`, `interface`, `api`, `datastore`, `queue`, `external-integration`, `library`, `infrastructure`.

Inferred mappings require provenance/confidence.

---

## 32. Observation / Incident / Reconsideration

Observation:

```json
{
  "schema":"dark-factory/observation",
  "schema_version":"1.0",
  "observation_id":"obs_...",
  "project_id":"proj_...",
  "kind":"performance",
  "subject_refs":[{"id":"comp_...","type":"implementation-component"}],
  "statement":"Peak latency exceeded 400 ms.",
  "measurement":{"value":438,"unit":"ms"},
  "source":"production-telemetry",
  "evidence_sha256":"...",
  "created_at":"..."
}
```

Incident:

```json
{
  "schema":"dark-factory/incident",
  "schema_version":"1.0",
  "incident_id":"inc_...",
  "project_id":"proj_...",
  "summary":"...",
  "severity":"high",
  "component_ids":["comp_..."],
  "observation_ids":["obs_..."],
  "status":"investigating",
  "created_at":"..."
}
```

Reconsideration:

```json
{
  "schema":"dark-factory/reconsideration",
  "schema_version":"1.0",
  "reconsideration_id":"recon_...",
  "project_id":"proj_...",
  "trigger":{"type":"assumption-invalidated","id":"asm_..."},
  "affected_recommendations":["rec_..."],
  "reopened_questions":["q_..."],
  "retained_candidate_ids":["cand_..."],
  "status":"exploring",
  "created_at":"..."
}
```

Affected recommendations are computed from graph edges before model reasoning starts.

---

## 33. Project Snapshot and Live Event

Snapshot is a rebuildable UI/bootstrap projection:

```json
{
  "schema":"dark-factory/project-snapshot",
  "schema_version":"1.0",
  "project_id":"proj_...",
  "project_version":185,
  "active_spec_ref":{},
  "features":[],
  "questions":[],
  "assumptions":[],
  "candidates":[],
  "recommendations":[],
  "programme_items":[],
  "factory_runs":[],
  "components":[],
  "incidents":[],
  "generated_at":"..."
}
```

Transport-neutral live event:

```json
{
  "schema":"dark-factory/live-event",
  "schema_version":"1.0",
  "project_id":"proj_...",
  "project_version":185,
  "event_id":"ev_...",
  "event_type":"candidate-evaluated",
  "subject":{"id":"cand_...","type":"candidate"}
}
```

UI reconnect should supply last project version and receive replay or fresh snapshot.

UI messages are not canonical authority.

---

# SCHEMA GOVERNANCE

## 34. Schema evolution

Never silently reinterpret an existing version.

Compatible additions may use a minor version only if old readers can safely ignore them.

Trust-sensitive semantic changes require a new schema version.

Migrations must be explicit, deterministic and tested; reversible where practical.

Trusted kernel/authority readers should fail closed on unknown trust-sensitive fields or explicitly defined extension surfaces.

Untrusted exploration/UI objects may be more forward-compatible where safe.

---

## 35. Deletion semantics

Canonical semantic history is not physically deleted merely because the current UI no longer displays it.

Use statuses/events such as:

`superseded`, `withdrawn`, `invalidated`, `pruned`.

Operational cold-storage policy may archive raw data without rewriting logical history.

---

## 36. Privacy / learning scope

Future project/trajectory objects should support an explicit learning scope, e.g.:

`none`, `project`, `organisation`, `global-opt-in`, `public`.

Private customer data must not become global training material by default.

---

## 37. Status vocabulary

Avoid `PASS` outside trusted qualification authorities.

Exploration: `recommended`, `promising`, `pruned`, `dominated`, `rejected`.

Authority: `PASS`, `FAIL`, `INDETERMINATE`.

Run lifecycle: `queued`, `running`, `blocked`, `repairing`, `failed`, `completed`.

This prevents UI trust confusion.

---

## 38. Schema implementation order

### Foundation

- trajectory;
- authority attestation;
- capability grant;
- lease evolution.

### Front Door / programme

- project;
- interview entry;
- approved spec;
- programme;
- compiled programme;
- factory handoff.

### Preflight

- question;
- assumption;
- candidate;
- evidence;
- prediction;
- probe;
- evaluation;
- recommendation;
- rejection;
- budget.

### Graph

- graph command;
- project event;
- node/edge projections;
- snapshot.

### Feedback / learning

- observation;
- incident;
- reconsideration;
- prediction outcome;
- engineering lesson;
- lesson packet.

Do not create a giant unused schema framework before dependent features need it.

---

## 39. Required schema tests

For every canonical schema:

- minimum-valid fixture;
- fully populated fixture;
- missing-required-field failure;
- invalid enum failure;
- wrong identity/hash binding failure where relevant;
- unsafe path failure where relevant;
- duplicate identity failure;
- unknown trust-sensitive-field failure where applicable;
- parse/serialize round trip with stable semantic digest.

Cross-schema invariants should include:

- recommendation assumptions exist;
- current recommendation cannot require invalidated assumption;
- handoff spec hash matches the approved spec at creation;
- compiled programme covers all required acceptance criteria;
- candidate question exists;
- user-origin candidate rejection has human-readable reason;
- prediction outcome references an existing prediction;
- authority revision equals transition subject revision where required;
- leased mutation presents current lease generation.

---

## 40. Security principle

References and declarations are not authority.

A JSON field saying:

```text
authority = green-replay
network = false
spec_sha256 = X
```

does not prove the claim.

Always distinguish declared metadata from independently enforced fact.

The target is one coherent semantic contract layer surrounding the existing evidence-driven factory, not a second source of truth.