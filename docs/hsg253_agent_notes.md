# HSG253 Notes for the Equipment Isolation Agent

Source reviewed: `/home/swarnim/Downloads/Safe-Isilation-of-Equipment-and-Plant.pdf`

Actual document: HSE HSG253, *The safe isolation of plant and equipment*, second edition, 2006.

## Verdict

This is useful for the agent. It should not replace OSHA 1910.147 in the LOTO phase sequence, but it is a strong process-isolation engineering reference for:

- selecting the required integrity level of a process isolation;
- deciding when positive isolation should be expected;
- improving missing-evidence messages;
- making validation stricter around proving, monitoring, and DVPF;
- generating better field-verification actions.

The current agent already covers some HSG253 themes: positive isolation evidence, verification evidence, boundary coverage, bypass checks, instrument context, and deterministic validation. The document mainly helps define richer rules around risk level, isolation method hierarchy, proving requirements, temporary variations, and reinstatement controls.

## High-Value Concepts to Reuse

### Isolation Method Hierarchy

HSG253 groups final isolation methods into three practical categories:

- Category I, positive isolation: complete physical separation, such as spool removal, spade, spectacle blind, blank flange, or equivalent.
- Category II, proved isolation: valved isolation whose effectiveness can be confirmed before breaking containment, such as double block and bleed. HSG253 treats DBB as mechanically stronger than single block and bleed.
- Category III, non-proved isolation: valved isolation without a way to prove closure before intrusive work. This is lower assurance and should only be accepted where risk assessment supports it.

Useful agent implication: candidate classification should distinguish `positive`, `proved`, and `non_proved`, not just `barrier`, `positive`, and `verification`. A valve plus an adjacent bleed/test point should be recognized as potentially proved; a standalone valve should not silently become proven.

### Baseline Isolation Standard Selection

Appendix 6 provides a useful baseline-selection model:

- classify the substance hazard;
- combine line size and pressure into a release factor;
- combine release factor with location consequences into an outcome factor;
- combine substance category and outcome factor into a baseline standard: further review, positive isolation, proved isolation, or non-proved isolation.

The tool excludes high-risk cases such as confined-space entry, pipeline isolation, extended isolation, and catastrophic failure scenarios. Those need separate conservative handling.

Useful agent implication: add deterministic risk metadata inputs before validation, for example:

- `substance_category`;
- `pressure_barg`;
- `line_size_cm`;
- `location_factor`;
- `confined_space_entry`;
- `hot_work`;
- `pipeline_work`;
- `extended_isolation`;
- `catastrophic_failure_possible`;
- `live_plant_intrusive_work`.

Then compute a baseline standard and compare selected candidates against it.

### Positive Isolation Expectations

HSG253 is especially conservative for confined-space entry, toxic fluids, and extended isolations. It also favors physical disconnection where practicable because it is easier to confirm visually than an inserted plate.

Useful agent implication: the current `WorkScope` default of intrusive and high-risk service requiring positive isolation is directionally correct. It could be made more explicit with scope flags for confined space, toxic service, extended isolation, and hot work.

### Proving and Verification

HSG253 emphasizes that isolation points should be proved before intrusive work unless risk assessment justifies non-proved isolation. Important details:

- prove each element separately, such as each valve in a DBB arrangement;
- prove in the expected pressure direction where practicable;
- prove against the highest credible pressure during the work;
- for positive isolation, prove both the initial valved isolation and the final physical isolation;
- pressure gauges can show pressure exists, but should not be treated as sole proof of zero pressure;
- vents, bleeds, drains, or approved test points are needed to confirm no trapped pressure or leakage.

Useful agent implication: `verification_candidate_ids` should remain necessary, but the validator could become stricter about the kind and position of verification. A pressure indicator alone should produce a weaker recommendation than a bleed/vent/test point that can verify the isolation envelope.

### DVPF: Draining, Venting, Purging, Flushing

The document gives strong guidance for stored-energy relief and safe cleaning before breaking containment. Useful risk prompts include:

- blocked drains or vents;
- valve cavities and dead legs;
- trapped pressure between valves or behind blanks;
- hydrates, freezing, viscous fluids, solids, or debris causing false readings;
- flare or closed-drain back pressure;
- representative gas testing, including high/medium/low sampling for large equipment;
- purge sequencing, especially for flammable gas and nitrogen;
- water flushing side effects, such as corrosion, freezing, structural load, or steam generation during recommissioning.

Useful agent implication: `find_bleeds_vents_drains` should not only find devices. Missing-evidence text should ask the field engineer to verify safe routing, blockage risk, representative sampling, and suitability for the process fluid.

### Boundary Completeness and P&ID Walkdown

HSG253 repeatedly stresses that every isolation point must be identified, marked, documented, and checked against current P&IDs and field reality. It specifically calls out missed vent lines, temporary hoses, unauthorized modifications, and shared systems as failure modes.

Useful agent implication: the existing HILT topology and unselected-source investigation are highly relevant. The final summary should keep recommending a field walkdown when:

- any source boundary is unresolved;
- a line has graph/STLM disagreement;
- there are possible bypasses or companion lines;
- P&ID evidence is missing or visually unresolved.

### Securing Isolation Devices

The document treats isolation security as part of isolation quality, not an administrative afterthought:

- valves should be locked, immobilized, or otherwise secured in proportion to risk;
- 90-degree valves are easy to move accidentally and need physical security;
- tags should be attached to every isolation component, including bleeds and spades;
- remotely actuated valves need special treatment: prevent remote operation, isolate motive power, and record the status for reinstatement.

Useful agent implication: final LOTO procedure steps should include locking/immobilizing each selected isolation point and explicit field checks for remote actuators or motive power if such metadata is present.

### Variations From Baseline

HSG253 allows lower-integrity variations only through task-specific risk assessment and authorization. Short-duration variations have tight expectations: single shift, attended worksite, proved valves, leak monitoring, contingency action, and available mitigation equipment.

Useful agent implication: if the selected candidate set falls below the computed baseline standard, the validator should not mark the plan complete. It should produce a provisional status and say that a formal variation is required.

### Reinstatement

HSG253 gives useful controls for restoration:

- cross-check all permits and dependent isolations before removing common isolation points;
- restore control and protection overrides;
- control disturbed joints and blank/blind registers;
- visually check valve positions against the plan before startup;
- leak test and monitor after recommissioning;
- check behind blanks or spades for leakage before removal.

Useful agent implication: `loto.py` could add richer restoration/re-energization steps beyond the OSHA skeleton, clearly marked as HSG253 process-isolation controls.

### Instrument Isolation

Appendix 9 maps well to the existing instrument context work:

- instrument removal may use primary process isolation plus local instrument valves;
- instrument loss can affect control or safety functionality;
- local single-valve instrument isolation on older plant should be treated as a variation requiring risk assessment;
- direct-mounted instruments may require isolation of the primary vessel itself.

Useful agent implication: instrument context should remain advisory unless a deterministic tool classifies the instrument isolation path. For instrument work, a local single valve should not be treated as robust isolation without a variation warning.

## Suggested Agent Improvements

1. Add HSG253 as a separate process-isolation reference, distinct from the OSHA RAG source. OSHA should still control the six LOTO phases; HSG253 should inform process-isolation quality, risk assessment, and field controls.

2. Add a deterministic baseline-standard assessor using Appendix 6 concepts. Inputs can come from config first, then later from graph/API metadata.

3. Extend evidence classification from boolean flags into an integrity level:

- `positive_isolation`;
- `proved_isolation`;
- `non_proved_isolation`;
- `verification_only`;
- `conditional_manual_review`.

4. Add validation logic that compares required baseline standard against selected candidate integrity. If selected integrity is lower, return provisional/unproven with a formal variation requirement.

5. Improve missing-evidence recommendations for DVPF. The current message asks for bleeds, vents, drains, and pressure indicators; it should also ask for blockage risk, safe routing, representative gas testing, and trapped-pressure checks where relevant.

6. Add procedure details for securing devices: lock/immobilize/tag each isolation component, include bleed/spade tags, and call out remote-actuated valve motive-power isolation.

7. Add restoration checks inspired by HSG253: blank/blind register, disturbed joints, overridden safety functions, valve-position walkdown, pressure/leak testing, and post-recommissioning monitoring.

8. Preserve conservative status behavior. HSG253 supports the existing rule that absence of proof or unresolved boundaries should not be papered over by the LLM.

## Best Initial Implementation Path

The lowest-risk path is documentation/RAG first, then deterministic logic:

1. Add HSG253 excerpts or notes to an internal reference source for the agent to retrieve.
2. Add config-only risk metadata and a small baseline-standard function.
3. Update validator missing-evidence messages and status rationale.
4. Only then expand graph/API extraction for pressure, line size, and substance metadata.

This avoids overfitting to incomplete plant data while still improving the agent's reasoning and field-action output.
