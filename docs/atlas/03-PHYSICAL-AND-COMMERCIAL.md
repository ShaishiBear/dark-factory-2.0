# Dark Factory Physical, and the Business

*The second domain and the company. Separated from `02-ARCHITECTURE-AND-METHODS.md` because it sits at a different evidentiary standard — see the caution below.*

---

## A caution about this document

The physical architecture emerged from a 49-question grill on the night of 5–6 Sep in which the user answered "agree" or "agreed" to the great majority of proposals. These are **[AGREED]** positions with high assent and correspondingly low adversarial pressure. Nothing here has been through the audit treatment that produced Part B of the architecture document, and nothing here has an implementation to falsify it.

Treat the software material as tested and this as designed.

The one load-bearing assumption underneath all of it: **the same generic control plane generalises across domains, with domain packs supplying expertise.** Software uses tests and production telemetry; physical engineering uses solvers and measurements. That claim appears once in the record, asserted rather than argued, and the target-architecture directive separately warns against premature genericisation. It is a bet, and this document is what is staked on it.

---

# PART A — THE ENGINEERING SYSTEM

## A.1 The benchmark ladder

**Hestia** is benchmark #1 and is never special-cased — the platform is built generic and Hestia is used as its test. Then a phone. Then over-ear high-fidelity headphones, chosen because they force acoustics, ergonomics, electronics, firmware, materials and genuinely subjective preference into one model.

The loop, as the user corrected it: **virtual design → simulation → improve design → loop → production/manufacturing package.** Virtual testing is accepted as inferior to physical testing, on the reasoning that the iteration rate compensates.

## A.2 The requirements model

```
HARD CONSTRAINTS        → must always pass
OPTIMISATION OBJECTIVES → improve as much as practical
RESIDUAL UNCERTAINTIES  → exposed explicitly, never disguised as passes
```

Users state priorities in natural language; the system compiles them into hard constraints, required margins and ranked objectives. Multi-objective and Pareto optimisation, never one arbitrary score. The optimisation policy is visible **before** the autonomous loop begins.

## A.3 The canonical model

Source of truth is a **structured parametric engineering model**, not exported CAD files. It carries components, assemblies, geometry parameters, materials, interfaces, tolerances, loads, constraints, electronics modules and firmware.

Scope is **complete mechatronics**: mechanical structure, actuators, sensors, electronics, power, firmware and control, thermal and environmental.

## A.4 The engineering loop

Autonomous concept selection · virtual design → simulation → redesign as the core capability · cost optimisation as a first-class loop · manufacturing redesign · lowest-cost-fidelity analysis first, escalating only on uncertainty · parallel design branches with dynamic compute allocation and dominated-branch pruning · surrogate and reduced-order models for **search acceleration only**, never to close a qualification requirement · **robust, uncertainty-aware engineering** against tolerances and distributions rather than nominal values · automatic FMEA-style failure-mode generation and fault injection · **time-to-first-prototype as an explicit optimisation dimension**.

The fidelity-ladder logic is identical to Preflight's: escalate cost only while the answer is still uncertain.

## A.5 Evidence and qualification

An **engineering evidence graph** in which every material claim traces:

```
requirement → decision → calculation / simulation / datasheet / standard / supplier evidence
→ result → margin
```

A versioned, machine-executable **qualification suite** per product, because *a design cannot call itself virtually qualified because an AI says it looks good*. Only the affected requirement subset reruns during optimisation — the physical analogue of the attestation dependency model.

**Immutable revisions** give hardware the equivalent of Git plus CI plus reproducible builds. Explicit evidence invalidation whenever a change touches a load path, power system, control model, material or component.

## A.6 The real-world boundary

The platform may design, simulate, optimise, source, compare suppliers and prepare BOMs, RFQs and manufacturing files. **The user approves external spend.**

Live component and supplier intelligence with exact MPNs, price, availability, lead time and lifecycle risk — and **a component substitution is a design change, not an invisible refresh.**

Prototype quantity is first-class: default 1–10 units, on the user's reasoning that people want a handful they can physically test or show investors before committing £50k to manufacturing. With the sharp observation attached: **three different prototypes may be more informative than ten identical ones.**

## A.7 Scope and policy

Vendor-neutral agent with an open-source core and commercial CAD/CAE as optional higher-fidelity adapters · hybrid cloud, local and private execution · greenfield **and** existing-design import · multimodal intake (sketches, images, video, CAD, PDFs, BOMs, PCB files, firmware, test data) with provenance preserved and gaps **marked rather than invented** · industrial design, ergonomics and CMF inside the canonical model, with **engineering forbidden from silently sacrificing appearance or feel to save cost** · sustainability as a native optimisation dimension that does not automatically outrank commercial priorities · cybersecurity engineering auto-activated for connected products only · lifecycle and serviceability reasoning that may legitimately conclude a cheap product is not economically serviceable.

## A.8 Governance

Hard constraints are immutable unless the user changes them. **The optimiser may never "solve" a problem by quietly weakening the brief.**

Infeasibility is surfaced as the smallest useful trade-off, distinguishing genuine infeasibility from insufficient search from model uncertainty.

Independent adversarial review at **milestones only**, not in the inner loop. Project-local learning automatic; cross-project learning opt-in only.

**No lock-in**: full export, documented schema. The stated reasoning — the moat is engineering quality, not trapped customer data — and the user's own version: *surely we'd make more money from it being private than by taking people's ideas.*

Life-critical domains excluded pending domain-specific qualification, but **not architecturally excluded forever**. Prior-art and patent-proximity analysis as engineering input, explicitly **not** a freedom-to-operate opinion.

## A.9 Reuse and novelty

Versioned, evidence-backed **qualified subsystems** with private, org and public scopes, carrying forward only evidence valid inside the original operating envelope. Stronger than a component library: over time the system assembles products from already-qualified engineering primitives.

Novelty policy:

```
standard component → known mechanism → combination/adaptation → genuinely novel mechanism
```

Escalate only when necessary. **Novel approaches require higher evidence.**

## A.10 What was actually produced

Three successive handover packages, each superseding the last:

- **Genesis** — the first brief.
- **v2** — `00-READ-ME-FIRST`, `MISSION`, `V1_SPEC`, `ONBOARDING_AND_PROGRAMME_SYNTHESIS`, `PROGRAMME`, `BENCHMARKS`, `LEARNING_AND_MODEL_ROADMAP`, `GENESIS_ISSUE`, `DECISION_LEDGER.json`.
- **v3** — adds `COMPETITIVE_STRATEGY`, a dated `MARKET_AND_COMPETITIVE_RESEARCH_2026-09` carrying sources, confidence levels and limitations, `COMPETITOR_WATCH`, and `LONG_TERM_VISION`, which preserves the go-to-market and company-OS ambitions while explicitly keeping them **out of V1**.

## A.11 Parallelisation

Dark Factory Physical need not wait six months. Once the Front Door, programme synthesis, Preflight, decision graph and reduced kernel work, enough of the generic control plane is proven to start the physical repository alongside.

---

# PART B — THE BUSINESS

## B.1 Positioning

**Rejected:** "autonomous CAD" (crowded) · "AI for engineering" (Synera, PhysicsX and Neural Concept own it) · "the Lovable of CAD" (anchors price to CAD software).

**Adopted:** **the autonomous product engineer** — *from product intent to qualified manufacturable product*. Economically: **AWS-like on-demand engineering capacity for physical products.**

## B.2 The pricing insight

CAD tooling is priced for humans to operate: Fusion around $57/month, Onshape $1,500/year individual, $2,500 professional. That pricing is irrelevant.

Price against **what the customer would otherwise pay to have this engineered**. If Hestia represents £30k–£100k of conventional engineering effort and marginal compute is hundreds to low thousands, the gap is the business.

## B.3 Five revenue layers

Core engineering subscription · metered engineering compute · prototype and manufacturing transaction revenue · enterprise and private deployment · the engineering-intelligence layer (Engineering API, models licensed to manufacturers, a qualified-subsystem marketplace).

## B.4 Two-stage go-to-market

**Stage 1 — an AI engineering service**, project-priced:

| | |
|---|---|
| Feasibility | £250–£500 |
| Prototype development | £2k–£10k |
| Production engineering | £10k+ |

**Stage 2 — the self-service platform:**

| | |
|---|---|
| Founders | £300–£1,500/month |
| Professional teams | £5k–£15k/month |
| Enterprise | £100k–£1m+ ARR |

## B.5 Customer selection

Enterprise is crowded. The uncontested position is *"I'm a founder with a product idea and no engineering department — build it for me."* Start there and move upward. **Do not attempt to rip Siemens out of Airbus on day one.**

## B.6 Data contribution credits

Customers trade contribution to the shared engineering dataset for reduced pricing, making the moat and the pricing mechanism the same object. The most original commercial idea in the record.

## B.7 The moat, in six layers

1. **Cross-discipline engineering OS** — canonical product state across all disciplines.
2. **The evidence and qualification layer** — the value is trust.
3. **The engineering trajectory dataset** — intent → thousands of decisions → failures → simulations → redesigns → physical outcome. Unbuyable and unscrapeable. **Failed designs may be worth more than successful ones.**
4. **The physical-world feedback loop** — and specifically learning **calibration**: where the system is systematically overconfident, not merely where it is wrong.
5. **Proprietary engineering intelligence** trained on 1–4.
6. **Integration depth** as legitimate workflow stickiness, explicitly distinguished from artificial lock-in.

Stated with unusual discipline: any one layer can be copied; the integrated flywheel is much harder.

**Explicitly not the moat:** "we use GPT better" · "we generate CAD from text" · "we have an AI physics model" · customer lock-in.

## B.8 External validation

*Closed-loop AI achieves certifiable engineering design* (arXiv 2608.21976, August 2026) describes substantially this loop for an offshore structure. The design passed an external **Approval in Principle** review by the China Classification Society, and the authors report **8.1% reductions in steel mass and capital cost** against their human-optimised baseline. They explicitly differentiate their approach from open-ended generation by requiring deterministic physics and engineering limit-state checks.

Read as validation of **"AI proposes, engineering evidence decides"**, and as a signal that the category window is now.

Handle with the source's own discipline: the market research was recorded with confidence levels and limitations attached, and this is a single external datapoint from one domain with one classification society.

## B.9 Competitive map

| Layer | Players |
|---|---|
| Requirements | Trace.Space |
| Mechanical | Leo, Zoo, Backflip, Autodesk, PTC |
| Physics | Neural Concept, PhysicsX, Ansys, nTop |
| Electronics | Circuit Mind, CELUS |
| Workflow | Synera |
| Review | CoLab |
| Manufacturing | Xometry, Fictiv |

**Nobody publicly combines the whole chain.**

Watchlist by threat: **Synera → Neural Concept → PhysicsX → Dulo → Zoo → Leo**, with Dulo (Sebastian Thrun, foundation models for hardware) singled out as the most direct potential overlap. An automated **competitor watch system** is proposed as a follow-on and exists as a document in the v3 package.
