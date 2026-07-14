# Equipment Isolation Agent PRD

## 1. Purpose

The Equipment Isolation Agent is a decision-support system that helps engineers create, inspect, and document an equipment isolation procedure from P&ID-derived graph data.

The product must make the isolation basis transparent. It must show:

- what equipment was selected,
- which process/electrical/other energy paths were found,
- which isolation points were selected,
- which boundaries remain unresolved,
- which stored-energy relief and verification points were found or missing,
- which downstream items may be affected,
- which instruments can support the procedure,
- what assumptions were made,
- what the authorized employee must verify in the field.

The agent must not certify that equipment is safe. It may generate an isolation procedure only as a planning aid. The final decision must remain with a qualified authorized employee under the site energy control program.

## 2. Regulatory Grounding

This PRD is grounded primarily in OSHA 29 CFR 1910.147, "The control of hazardous energy (lockout/tagout)," for general industry service and maintenance.

Key OSHA requirements used as product requirements:

- OSHA 1910.147 covers service and maintenance where unexpected energization, startup, or release of stored energy could injure employees.
- OSHA requires an energy control program with procedures, training, and periodic inspections.
- Energy control procedures must clearly cover scope, purpose, authorization, rules, and techniques.
- Procedures must include specific steps for shutdown, isolation, blocking, securing, placement/removal/transfer of lockout or tagout devices, and testing/verification.
- Application of control must follow the OSHA sequence: preparation, shutdown, isolation, lockout/tagout device application, stored-energy control, and verification.
- Stored or residual energy must be relieved, disconnected, restrained, or otherwise rendered safe.
- If stored energy can reaccumulate, verification must continue until servicing is complete or reaccumulation is no longer possible.
- Before work starts, the authorized employee must verify that isolation and de-energization have been accomplished.
- Before locks/tags are removed and energy restored, the work area and employees must be checked, controls must be in a safe state, devices must be removed according to procedure, and affected employees must be notified.

Primary sources:

- OSHA 1910.147 standard: https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147
- OSHA 1910.147 Appendix A, typical minimal lockout procedure: https://www.osha.gov/laws-regs/regulations/standardnumber/1910/1910.147AppA
- OSHA Control of Hazardous Energy overview: https://www.osha.gov/control-hazardous-energy
- OSHA Lockout/Tagout training program: https://www.osha.gov/dts/osta/lototraining/

## 3. Product Scope

### 3.1 In Scope

The agent must support isolation planning for selected equipment represented in the available Plant360/CNVRT/Unigraph data sources.

The product must:

- identify equipment boundary paths,
- select likely isolation points,
- classify isolation evidence,
- check coverage for all process boundary paths,
- identify unresolved or manual-review paths,
- identify stored-energy relief opportunities such as bleed, vent, and drain points,
- identify verification opportunities such as pressure gauges, pressure indicators, test points, and local indicators,
- identify relevant instruments and explain how they can support decisions,
- identify downstream impact from selected isolation barriers,
- generate an ordered isolation procedure,
- generate warnings and field holds,
- return structured JSON that CNVRT UI can render,
- provide a local HTML viewer only as a development and review harness,
- support deterministic and agentic execution modes.

### 3.2 Out of Scope

The agent must not:

- certify an isolation as safe for work,
- replace a site-approved energy control procedure,
- replace field verification by an authorized employee,
- infer numeric safe operating limits unless those limits are explicitly configured,
- infer operating state from live plant data unless live data integration is explicitly added,
- issue work permits,
- manage lock ownership,
- manage personnel accountability,
- perform legal compliance certification,
- handle construction, agriculture, maritime, oil and gas drilling/servicing, or electric utility generation/transmission cases unless a separate jurisdiction-specific requirements review is completed.

### 3.3 Safety Position

The agent must always present itself as decision support. If required evidence is missing, the correct behavior is to show the gap, not to hide it or infer it.

The agent must never convert weak evidence into certainty. Examples:

- A P&ID valve symbol does not prove the valve exists, is accessible, works, or is lockable.
- A pressure indicator reading does not prove all energy sources are isolated.
- A level indicator reading does not prove zero pressure.
- A remote transmitter does not prove local zero energy unless site policy explicitly accepts it.
- A control loop or controller is not an energy-isolating device.

## 4. Users and Reviewers

Primary users:

- process engineers,
- maintenance planners,
- safety engineers,
- authorized employees preparing isolation work,
- reviewers validating isolation scope before field execution.

Reviewers:

- site safety authority,
- operations authority,
- maintenance authority,
- process safety authority,
- product owner,
- data/API owner.

The product must be understandable to a reviewer who has authority over isolation safety. The UI and generated text must use plain professional language.

## 5. Definitions

Equipment of interest: The equipment selected by the user for isolation.

Boundary path: A process, utility, electrical, instrument, or other connection path leaving or entering the selected equipment.

Process boundary path: A boundary path that can transmit process energy, material, pressure, flow, or inventory and therefore requires isolation or explicit disposition.

Context path: A boundary path that is not a process isolation boundary, such as an instrument impulse line or signal line. It may still require procedural consideration.

Isolation point: A device intended to physically isolate an energy source, such as a line valve, disconnect switch, breaker, blind, spade, blank flange, or block.

Positive isolation: A stronger physical separation method such as a blind, spade, blank flange, spool removal, disconnection, breaker, or equivalent site-approved means.

Stored-energy relief point: A means to relieve, drain, vent, bleed, restrain, block, ground, or otherwise render stored energy safe.

Verification point: A means to verify isolation and de-energization, such as a test point, local gauge, pressure indicator, or approved try/test method.

Instrument context: Instrument information that can support the procedure, such as PI, PG, PT, LI, LT, LIC, FIC, alarms, and other tagged instruments. Instrument context is advisory unless a site-specific policy explicitly marks it as accepted evidence.

Assurance status: The deterministic validation result assigned by the system. It is not a safety certification.

Authorized employee: The person who locks or tags equipment to perform service or maintenance, as defined by OSHA 1910.147.

Affected employee: A person whose work requires operating or using equipment being serviced or whose work area is affected by lockout/tagout.

## 6. Core Product Principles

1. Deterministic facts first.
   The system must derive isolation points, boundaries, instrument context, downstream impacts, and evidence status from structured data and deterministic algorithms wherever possible.

2. LLM cannot invent safety facts.
   The agentic runner may summarize and organize deterministic facts. It must not create isolation points, affected equipment, instrument readings, acceptance criteria, or assurance status that deterministic tools did not provide.

3. Product isolation status is assigned by deterministic validation rules.
   The software's reported `assurance_status` must come only from the deterministic validation module. Other components, including the LLM, viewer, downstream impact analysis, and instrument analysis, may explain or add context to the result, but must not override it. This status reflects the completeness of the isolation plan based on available data. It is not a field certification that the equipment has been physically isolated or is safe for work.

4. Missing evidence must be visible.
   Missing bboxes, unresolved boundary paths, missing bleed/vent/drain points, missing verification points, uncertain flow direction, unavailable API data, and downstream unknowns must be shown as warnings or field holds.

5. Procedure must be linear and usable.
   The generated procedure must read as a single ordered sequence. Supporting explanation may appear as sub-points under the relevant step.

6. Site limits must be configurable.
   Numeric values such as safe pressure, empty/low level, safe temperature, stabilization hold time, and acceptable transmitter use cannot be inferred from a P&ID. They must be supplied by site configuration or left as field-required acceptance criteria.

7. Visual overlays must match the procedure.
   Every blue isolation point, yellow target, orange manual candidate, red downstream impact, and instrument overlay must correspond to structured JSON entries.

## 7. Data Sources

### 7.1 Required Data

The minimum data required to produce a useful procedure:

- selected equipment tag,
- Unigraph/JanusGraph equipment graph,
- HILT process graph,
- STLM symbol/text/bbox data,
- project profile identifying the Unigraph project and CNVRT project/collection,
- P&ID image for visual review,
- isolation policy configuration,
- TODO: Review and define work scope configuration in detail. The current implementation has only a basic work scope model, and the final required inputs for work type, risk level, line break, vessel entry, hot work, confined-space entry, electrical work, contractor work, group lockout, shift handover, and temporary re-energization need authority review.

### 7.2 Optional Data

Optional data is useful only when it is available as structured input with clear source and scope. Free-form text documents may be stored as references, but they must not directly change validation or procedure logic.

V1 optional data that is feasible and should be supported:

- site policy JSON: site-level rules for instrument use, positive isolation requirements, conditional device handling, and default field holds,
- equipment override JSON: equipment-specific or line-specific thresholds and metadata, such as safe pressure, safe level, hazardous service, lockable-device notes, or valve overrides,
- manual correction JSON: user-approved corrections from the UI or CLI, such as accepted/rejected manual isolation candidates, corrected labels, manually added isolation points, and confirmed bypass valves,
- run arguments: selected equipment tag, project profile, work scope flags currently supported by the runner, output directory, and optional image URL.

V2 optional data that can be added after V1 is stable:

- work order or permit API data,
- line list API or exported structured line list,
- asset registry API for lockability and device metadata,
- historian API for timestamped readings and trends,
- structured extraction from operating procedures, datasheets, site templates, and PSSR/startup documents.

Not supported as direct logic input:

- unreviewed PDF/DOCX/text documents,
- copied free-form notes,
- LLM-extracted thresholds that have not been reviewed and stored as structured data,
- live historian values without timestamp, units, and tag mapping,
- design limits used as safe-work limits without site approval.

### 7.3 Optional Data Ingestion Contract

The V1 ingestion contract must be file-based and explicit. This keeps the first implementation feasible and auditable.

Supported V1 files:

- `project_config.json`: selects project/profile, Unigraph project, CNVRT project/collection, graph host/port, and job lookup behavior.
- `site_policy.json`: defines site-level isolation rules and instrument interpretation policy.
- `equipment_overrides.json`: defines equipment-specific or line-specific metadata.
- `manual_corrections.json`: records user-approved corrections for a run or equipment tag.

These files may be passed by CLI flags or referenced from the active project profile. The final JSON must list which files were loaded.

Example `site_policy.json` shape:

```json
{
  "version": "site_policy_v1",
  "positive_isolation": {
    "required_for_intrusive_work": true,
    "required_for_high_risk_service": true,
    "accepted_device_classes": ["blind", "spade", "blank_flange", "disconnection", "breaker"]
  },
  "conditional_devices": {
    "check_valve_counts_as_isolation": false,
    "control_valve_counts_as_isolation": false
  },
  "instrument_policy": {
    "remote_transmitters_can_verify_zero_energy": false,
    "local_pressure_indicators_can_support_verification": true,
    "level_indicators_can_verify_depressurization": false,
    "controllers_are_isolation_devices": false
  }
}
```

Example `equipment_overrides.json` shape:

```json
{
  "version": "equipment_overrides_v1",
  "equipment": {
    "NEW": {
      "service": "DM water",
      "safe_level": {
        "value": "empty_or_low",
        "unit": "site_defined",
        "source": "site_review_required",
        "scope": "drain-down support"
      },
      "safe_pressure": {
        "value": 0,
        "unit": "barg",
        "source": "site_review_required",
        "scope": "depressurization support"
      }
    }
  }
}
```

Example `manual_corrections.json` shape:

```json
{
  "version": "manual_corrections_v1",
  "equipment_tag": "NEW",
  "corrections": [
    {
      "type": "accept_manual_isolation_candidate",
      "candidate_id": "AA002",
      "reason": "Reviewer confirmed this is a required bypass valve for the right-side branch.",
      "author": "reviewer@example.com",
      "timestamp": "2026-07-07T00:00:00Z"
    }
  ]
}
```

Required handling rules:

- Optional data must be parsed into structured fields before it affects validation, procedure text, or UI warnings.
- Each safety-relevant optional value must include source and scope.
- Numeric values must include units.
- User corrections must include author, timestamp, reason, and affected object ID.
- If optional data conflicts with graph/P&ID data, the conflict must be shown as a warning or review item.
- If optional data is missing, the procedure must fall back to field-required acceptance criteria rather than guessing.
- The LLM may summarize loaded optional data, but it must not invent optional data.

Precedence for V1:

1. User-approved manual correction for the current run.
2. Equipment-specific override.
3. Site policy.
4. Project profile default.
5. Product default.

Examples:

- If `site_policy.json` says remote transmitters cannot verify zero energy, `PT` and `LT` readings may support trends but must not satisfy verification.
- If `equipment_overrides.json` gives a safe pressure threshold with units and source, the pressure instrument explanation may use that threshold.
- If only a line design pressure is available, it may be shown as context but must not be treated as the safe depressurization limit.
- If a manual correction accepts an orange bypass valve, the procedure may include it as a manual-basis isolation step and must record that basis.

### 7.4 Data Source Reliability Order

For topology:

1. HILT process-line topology for nozzle-to-valve connectivity.
2. STLM bboxes for drawing coordinates.
3. Unigraph/JanusGraph for equipment and graph relationships.
4. Visual proximity only as fallback or manual-review signal.

For instrument bboxes:

1. STLM instrument symbol bbox.
2. HILT bbox only when calibrated and STLM is unavailable.
3. No overlay when current-page bbox is unavailable.

For job/project resolution:

1. Explicit configured project and collection.
2. Project-scoped job lookup from selected equipment/P&ID metadata.
3. Fail with a clear message if the configured project/collection cannot be resolved.

The product must not silently fall back to a global job scan that can cross project or collection boundaries.

## 8. Functional Requirements

### FR-1: Project and Equipment Selection

The product must allow analysis for one selected equipment tag within one configured project profile.

In production, CNVRT UI is the primary entry point. A user selects equipment in CNVRT UI and clicks the isolation action. CNVRT UI sends the selected equipment and project context to the isolation agent. The agent returns structured JSON to CNVRT UI. CNVRT UI is responsible for rendering the procedure, overlays, warnings, and review controls.

The local CLI, HTML file, and JSON file are development artifacts used for rapid iteration, regression testing, and reviewer demos before CNVRT UI integration is done.

Requirements:

- The active profile must define Unigraph project ID and CNVRT project/collection.
- Listing equipment must list equipment from the selected Unigraph project.
- Running equipment not present in the selected project must fail with a clear message.
- The production response must be structured JSON suitable for direct CNVRT UI rendering.
- Development output file paths must be stable and tag-based: `/tmp/eia/<TAG>.html` and `/tmp/eia/<TAG>.json` by default.
- The development HTML/JSON output must not define the product contract. The product contract is the structured response returned to CNVRT UI.

Acceptance criteria:

- Running `python -m run --list-equipment` under a profile shows only that profile's equipment.
- Running an invalid tag reports no match or job resolution failure without using another project.
- Re-running the same tag overwrites the same HTML/JSON so the browser can be refreshed.
- Given a CNVRT UI request for a valid equipment in the active project, the agent returns JSON containing project context, selected equipment, assurance status, warnings, isolation points, procedure steps, overlays, and audit/debug metadata.
- Given a CNVRT UI request for equipment outside the active project, the agent fails clearly and does not search another Unigraph project or CNVRT collection.

### FR-2: Boundary Detection

The product must identify all boundary paths connected to the selected equipment.

Boundary types:

- process inlet,
- process outlet,
- bidirectional process path,
- vent,
- drain,
- utility,
- electrical,
- hydraulic,
- pneumatic,
- instrument impulse line,
- instrument signal line,
- control/companion line,
- off-page connector,
- unknown.

Requirements:

- Process paths must become isolation obligations unless classified as non-process context with documented basis.
- Instrument and signal context must not be counted as process isolation obligations, but must appear as context.
- Unknown paths must be treated conservatively as requiring review.
- Duplicate paths must be deduplicated without hiding independent branches.

Acceptance criteria:

- Every process nozzle/source path appears in the isolation coverage table as isolated, unresolved, or manual review.
- Non-process context paths appear separately and do not create false missing-boundary warnings.
- Unknown path types are visible and require manual review.

### FR-3: Isolation Candidate Selection

The product must select candidate isolation points for each process boundary path.

Supported isolation candidates:

- gate valve,
- ball valve,
- globe valve,
- generic/undefined valve,
- control valve where policy permits conditional candidate handling,
- check valve as conditional or warning-only unless site policy permits,
- blind,
- spade,
- spectacle blind,
- blank flange,
- removable spool,
- disconnect,
- breaker,
- block or equivalent physical energy-isolating device.

Requirements:

- HILT topology must be used to find the nearest isolation valve on each branch.
- Valves must be treated as branch endpoints for first-isolation selection unless policy says otherwise.
- Bypass and parallel paths must be detected and highlighted for manual review.
- A single physical valve may satisfy multiple source paths only if topology supports that relationship.
- The system must retain source path provenance for every selected candidate.

Acceptance criteria:

- Closing one valve on a line does not hide a parallel bypass branch.
- Manual bypass candidates are shown in orange overlays and warnings.
- Each isolation point has a source path reason in JSON and viewer table.

### FR-4: Isolation Coverage

The product must report coverage for every process isolation obligation.

Statuses:

- isolated: a selected candidate covers the process path,
- unresolved: no selected candidate covers the process path,
- context: path is non-process context,
- manual_review: candidate exists but needs field/UI decision,
- unavailable: analysis could not run due to missing HILT/STLM/API data.

Requirements:

- Coverage must be shown in the procedure card.
- Unresolved process paths must create a warning/field hold.
- Manual candidates must not be silently promoted to selected isolation points.

Acceptance criteria:

- If any process path is unresolved, `assurance_status` cannot be `isolated`.
- The viewer shows the unresolved source label or a clear graph-only identifier.
- Manual-review candidates are visible on the P&ID if they have bboxes.

### FR-5: Evidence Classification

The product must classify selected candidates and nearby devices into evidence categories.

Evidence categories:

- barrier evidence,
- positive isolation evidence,
- stored-energy relief evidence,
- verification evidence,
- instrument support,
- downstream impact evidence.

Requirements:

- Barrier evidence includes physical isolation devices such as valves and disconnects.
- Positive isolation evidence includes blinds, spades, blank flanges, disconnections, breakers, and equivalent site-approved physical separations.
- Stored-energy relief evidence includes bleed, vent, drain, grounding, blocking, restraining, or equivalent means.
- Verification evidence includes approved test points, local gauges/indicators, try/test methods, or other site-approved methods.
- Instrument support must be advisory by default.

Acceptance criteria:

- A valve alone does not satisfy stored-energy relief or verification.
- A level indicator does not satisfy depressurization.
- A pressure transmitter does not satisfy zero-energy verification unless policy explicitly allows it.

### FR-6: Assurance Status

The product must compute an assurance status deterministically.

Initial statuses:

- `not_isolated`: no selected deterministic barrier or critical boundary missing.
- `provisional_unproven_isolation`: selected barriers exist but safety-critical evidence remains unresolved.
- `isolated_with_gaps`: every known process path has selected barriers but positive isolation or verification requirements are incomplete for the configured work scope.
- `isolated`: every known process path is isolated and required positive isolation/verification evidence is present.
- `unavailable`: required data source is unavailable.

Requirements:

- Status must be derived by deterministic validation only.
- The agentic runner must not modify or override status.
- Status must include rationale and unresolved evidence checks.
- Work scope must affect required evidence. Intrusive work, confined-space entry, hot work, or high-risk service must require positive isolation unless site policy says otherwise.

Acceptance criteria:

- Missing bleed/vent/drain creates a stored-energy field hold.
- Missing verification point creates a verification field hold.
- Missing positive isolation for high-risk intrusive work prevents full isolated status.

### FR-7: Downstream Impact Analysis

The product must analyze downstream reachability from selected isolation barriers.

Requirements:

- Use HILT process-line graph.
- Treat selected isolation valves/barriers as closed.
- Report reachable equipment, instruments/control loops, endpoints, open vents, and off-page connectors.
- Use `likely` for directed one-way/arrow-grounded paths.
- Use `possible` for unknown-flow or weakly directed paths.
- Do not draw off-page/current-page-missing endpoints without valid current-page bbox.

Acceptance criteria:

- Downstream warnings appear in the procedure warning section.
- Red overlays appear only for downstream items with valid current-page bboxes.
- The LLM cannot add downstream impacts not returned by deterministic analysis.

### FR-8: Instrument Context and Interpretation

The product must identify relevant instruments and explain how they support the procedure.

Supported instrument examples:

- `PI`, `PG`: pressure indicator/gauge.
- `PT`: pressure transmitter.
- `PIC`: pressure indicating controller.
- `LI`, `LG`: level indicator/gauge.
- `LT`: level transmitter.
- `LIC`: level indicating controller.
- `FI`: flow indicator.
- `FT`: flow transmitter.
- `FIC`: flow indicating controller.
- `TI`: temperature indicator.
- `TT`: temperature transmitter.
- `TIC`: temperature indicating controller.
- `PAH`, `PAL`, `LAH`, `LAL`: alarms.

Requirements:

- Instrument classification must be config-driven.
- Relevant instruments must be selected by HILT connection or target-adjacent STLM fallback.
- STLM bboxes must be preferred for instrument overlays.
- Controllers must produce control-state steps, not reading/verification steps.
- Local indicators may support field interpretation but remain advisory by default.
- Remote transmitters may support trends but remain advisory by default.
- Each instrument step must include:
  - action,
  - purpose,
  - meaning,
  - acceptance criteria,
  - limitation.

Interpretation rules:

- Pressure: zero gauge pressure or site-defined safe threshold supports depressurization only when stable and confirmed by approved field verification.
- Level: low/empty level supports drain-down or inventory removal, but does not prove zero pressure.
- Flow: no-flow supports absence of flow in the measured line, but does not prove isolation of all sources.
- Temperature: safe temperature supports thermal-energy control, but does not prove process isolation.
- Controller: controller state prevents automatic action but is not physical isolation.
- Alarm: alarm state is awareness/context, not isolation evidence.

Acceptance criteria:

- Instrument overlays visually match the P&ID labels.
- Instrument details appear as sub-points under the relevant ordered procedure step.
- The procedure does not have a separate instrument checklist that duplicates the ordered steps.

### FR-9: Ordered Isolation Procedure

The product must generate one straightforward procedure, not disconnected panels.

Required OSHA phase order:

1. Preparation for shutdown.
2. Equipment shutdown.
3. Equipment isolation.
4. Lockout/tagout device application.
5. Stored/residual energy relief.
6. Verification of isolation.
7. Release/restoration/re-energization after work.

Requirements:

- The phase order must not be changed.
- Within-phase device order may use engineering logic, such as inlet/upstream before outlet/downstream, but the UI must clearly state OSHA does not prescribe valve closure order within a phase.
- Each step must include OSHA reference where applicable.
- Supporting details must be sub-points under the relevant step.
- Field gaps must be visibly marked.
- Restoration must be included in the same procedure card.

Acceptance criteria:

- A field user can read the procedure top-to-bottom.
- Instrument meaning appears under the step where the instrument is used.
- Warnings and holds appear before the ordered steps.
- The release/restoration section includes area clear, employees clear, controls neutral, lock removal, notification, and instrument/restoration monitoring when available.

### FR-10: Stored Energy Handling

The product must explicitly handle stored and residual energy.

Energy types:

- pressure,
- liquid inventory,
- gas/vapor inventory,
- electrical charge,
- hydraulic pressure,
- pneumatic pressure,
- mechanical motion,
- springs/tension,
- gravity/elevated load,
- thermal energy,
- chemical/reactive energy.

Requirements:

- If no stored-energy relief point is found, create a field hold.
- If reaccumulation is possible, require continued monitoring.
- The procedure must not imply a system is depressurized only because isolation valves were closed.
- Drains/vents/bleeds must be distinguished from indicators.

Acceptance criteria:

- A vessel with no drain/vent/bleed shows a stored-energy relief field gap.
- A pressure indicator may explain depressurization logic but does not remove the need for approved field verification.

### FR-11: Verification Handling

The product must explicitly handle verification before work.

Verification methods:

- local pressure gauge/indicator,
- test point,
- bleed/vent confirmation,
- try/start test for applicable equipment,
- electrical absence-of-voltage test where applicable,
- site-approved zero-energy method.

Requirements:

- Verification must happen after lockout/tagout and stored-energy relief.
- Verification must include a check that personnel are not exposed before trying/testing.
- Operating controls must be returned to neutral/off after try/test.
- Instruments can support but not replace verification unless site policy explicitly allows.

Acceptance criteria:

- If no verification method exists in the data, the procedure includes a field gap.
- The procedure does not state "verified" solely because an instrument exists.

### FR-12: Restoration and Re-Energization

The product must include restoration steps after work completion.

Requirements:

- Inspect the machine/equipment and immediate area.
- Remove nonessential items.
- Confirm equipment components are operationally intact.
- Ensure employees are safely positioned or removed.
- Verify controls are neutral/off.
- Remove lockout/tagout devices according to responsibility rules.
- Re-energize according to site procedure.
- Notify affected employees.
- Monitor relevant instruments and alarms after re-energization.

Acceptance criteria:

- Restoration appears in the same procedure card.
- Restoration does not imply startup is safe if site acceptance limits are missing.
- Instrument restoration checks reference site-defined safe operating ranges.

### FR-13: Temporary Re-Energization for Testing or Positioning

The product must support a special procedure path for temporary energization when needed.

Requirements:

- Clear tools and materials.
- Remove employees from the machine/equipment area.
- Remove lockout/tagout devices according to procedure.
- Energize and proceed with testing or positioning.
- De-energize and reapply energy control measures before continuing work.
- Show this as a special case, not part of normal isolation.

Acceptance criteria:

- Temporary re-energization is opt-in.
- The procedure warns that energy control must be reapplied before work resumes.

### FR-14: Group Lockout

The product must support group lockout planning as a future or configurable workflow.

Requirements:

- Identify when multiple authorized employees or crews are involved.
- Require a primary authorized employee or equivalent responsible person.
- Require each authorized employee to apply their own lock or equivalent personal control.
- Preserve individual accountability.

Acceptance criteria:

- Group lockout metadata appears in JSON when configured.
- The UI does not collapse multiple workers into a single anonymous lock action.

### FR-15: Shift or Personnel Change

The product must support shift/personnel transfer requirements.

Requirements:

- Procedure must include transfer continuity when work crosses shift changes.
- New authorized employees must verify isolation status before accepting responsibility.
- Lock transfer/removal responsibility must be explicit.

Acceptance criteria:

- If shift handover is configured, the procedure includes handover steps.
- Missing handover owner creates a field hold.

### FR-16: Outside Personnel / Contractors

The product must support contractor coordination requirements.

Requirements:

- On-site employer and outside employer procedures must be identified.
- The procedure must require mutual communication of lockout/tagout procedures.
- The on-site employer must ensure affected employees understand restrictions.

Acceptance criteria:

- If contractor work is configured, the procedure includes contractor coordination steps.
- Missing contractor procedure creates a field hold.

### FR-17: Tagout-Only Cases

The product must distinguish lockout from tagout-only cases.

Requirements:

- If a device is capable of being locked out, lockout is preferred unless site policy demonstrates equivalent tagout protection.
- If a device cannot be locked, tagout must be shown with equivalent protection requirements.
- Additional safety measures must be considered for tagout-only cases.

Acceptance criteria:

- Tagout-only output is clearly marked as lower-preference and policy-dependent.
- Tagout-only cannot be treated as equivalent unless configured and justified.

### FR-18: Electrical Isolation Cases

The product must support electrical energy sources when represented in the data.

Requirements:

- Identify disconnects, breakers, MCC feeds, and electrical energy paths.
- Require lockout/tagout of electrical isolating devices.
- Require appropriate verification, such as absence-of-voltage testing, when applicable.
- Distinguish 1910.147 process/equipment isolation from electrical work covered by other standards, such as OSHA Subpart S, where applicable.

Acceptance criteria:

- Electrical isolation points appear separately from process valves.
- Electrical verification gaps are distinct from process pressure verification gaps.

### FR-19: Development HTML Viewer

The repository must provide a clear local HTML viewer for development, debugging, and reviewer demos.

This viewer is not the production UI. In production, CNVRT UI consumes the agent JSON and renders the equivalent information using CNVRT UI components.

Required overlays:

- yellow: selected equipment target,
- blue: selected isolation points,
- orange: manual-review isolation candidates,
- red: downstream impact,
- teal/cyan: instruments,
- optional: context paths.

Requirements:

- Labels must be short and readable.
- The viewer must scroll to the useful area using all drawable overlays, not only isolation points.
- Invisible or off-page endpoints must not be drawn on the current P&ID.
- The procedure card must precede the image so the user sees warnings first.
- The viewer must be generated from the same structured JSON contract that CNVRT UI consumes.
- Viewer-specific formatting must not hide, rename, or reinterpret safety-critical JSON fields.

Acceptance criteria:

- The image and procedure can be inspected without needing to copy a new path after each run.
- Refreshing `/tmp/eia/<TAG>.html` shows the latest run.
- A reviewer can compare the HTML viewer against the JSON and trace every visible overlay or warning to a structured JSON entry.

### FR-20: CNVRT UI JSON Response

The product must return structured JSON as the primary integration contract with CNVRT UI.

Required top-level data:

- project context,
- selected equipment,
- assurance status,
- isolation validation,
- isolation points,
- isolation obligations,
- downstream impact,
- instrument context,
- procedure,
- debug data.

Requirements:

- The same JSON shape must be used by the CLI development output and by the CNVRT UI integration response.
- Raw HILT/STLM payloads must not be written to final JSON unless explicitly requested for debug export.
- Every visual overlay must trace to a JSON object.
- Every warning must have a structured basis.
- The JSON must include enough information for CNVRT UI to render the procedure without parsing text blobs.
- Human-readable text may be included, but safety-critical meaning must also be represented as structured fields.

Acceptance criteria:

- CNVRT UI can consume the JSON without parsing HTML.
- Safety-critical warnings are machine-readable.
- The local `/tmp/eia/<TAG>.json` file is a saved copy of the same response for development and audit review.

## 9. Agentic Runner Requirements

The agentic runner may use Gemini or another LLM as an orchestrator. The LLM must call deterministic tools and summarize their results.

Requirements:

- The LLM must call validation before reporting status.
- The LLM must not override `assurance_status`.
- The LLM must call downstream impact analysis after validation.
- The LLM must call instrument context analysis before building the procedure.
- The LLM may propose within-phase ordering, but must not claim OSHA mandates valve order.
- The final answer must distinguish deterministic findings from field-required checks.
- The trace must record every tool call.

Acceptance criteria:

- Agent trace shows required tool sequence or explicit forced fallback.
- If the LLM skips a required tool, the runner forces it or fails closed.
- Agent output and deterministic output agree on assurance status and isolation UUIDs.

## 10. Error Handling and Fallbacks

The product must fail closed for safety-critical uncertainty.

Cases:

- Graph unavailable.
- API unavailable.
- Missing job ID.
- Missing P&ID image.
- Missing STLM bboxes.
- Missing HILT graph.
- Equipment not found.
- Multiple equipment matches.
- Unknown flow direction.
- Unknown source type.
- Missing instrument catalog entry.
- Off-page connector with no current-page bbox.

Required behavior:

- Do not crash for optional analyses such as downstream impact or instrument context.
- Mark optional analysis as `unavailable` with error reason.
- Do fail with a clear message for project/job resolution errors that could attach the wrong drawing.
- Never substitute data from another project or collection.
- Show user-facing warnings for missing visual evidence.

## 11. Work Scope Policy

The product must allow work scope to drive required evidence.

Work scope inputs:

- intrusive work,
- non-intrusive work,
- confined-space entry,
- hot work,
- high-risk service,
- line break,
- vessel entry,
- electrical work,
- contractor involvement,
- temporary re-energization required,
- group lockout required,
- shift handover expected.

Default policy:

- Intrusive work and high-risk service require positive isolation evidence.
- Missing positive isolation prevents full isolated status.
- Missing relief or verification evidence creates field holds.
- Confined-space entry or hot work should require stricter site policy before final approval.

## 12. Configuration Requirements

The product must use configuration for site-specific decisions.

Configurable items:

- project profile,
- instrument catalog,
- accepted instrument prefixes,
- safe pressure threshold,
- safe level threshold,
- safe temperature threshold,
- required stabilization hold time,
- whether remote transmitters can be accepted,
- whether local indicators can satisfy any verification step,
- positive isolation requirements by work type,
- lockout/tagout terminology,
- output directory,
- model selection for agentic runner.

Default safety posture:

- Instrument context is advisory only.
- Remote transmitter readings do not satisfy verification.
- Local indicators support but do not prove verification.
- Controllers are not energy-isolating devices.
- Unknown flow direction is possible, not likely.
- Missing data creates a warning or field hold.

## 13. Non-Functional Requirements

### 13.1 Traceability

Every output must be traceable to:

- graph vertex or HILT/STLM UUID,
- selected equipment tag,
- source component/nozzle,
- P&ID job ID,
- project profile,
- deterministic module,
- confidence/basis.

### 13.2 Reproducibility

Given the same graph/API data and configuration, deterministic output must be repeatable.

### 13.3 Performance

Target performance:

- single equipment deterministic run: less than 90 seconds under normal API conditions,
- list equipment: less than 60 seconds,
- development HTML viewer generation: less than 5 seconds after data collection,
- parallel runs must not corrupt output for different equipment tags.

### 13.4 Security

Requirements:

- Do not write auth tokens to output JSON, HTML, logs, or trace.
- Do not expose raw API payloads by default.
- Use project-scoped API queries.
- Treat local `.env` as secret.

### 13.5 Auditability

The agentic runner must write a trace file with:

- model,
- equipment tag,
- config context,
- tool call sequence,
- compact tool results,
- forced calls,
- final payload path.

## 14. Rendering Requirements

CNVRT UI must render the agent response. The local HTML viewer must render the same information during development.

The rendered view must show:

1. Header with equipment and assurance status.
2. Warning and field holds.
3. Isolation coverage.
4. Ordered isolation procedure.
5. Release/restoration section.
6. P&ID image with overlays.
7. Overlay summary table.

The ordered procedure must:

- be readable in one pass,
- show phase/reference,
- keep action text concise,
- place explanations as sub-points,
- avoid duplicating instrument information in a separate panel,
- keep field gaps visually distinct.

## 15. Output Procedure Requirements

A generated procedure must include:

- preparation: affected employee notification and energy identification,
- baseline instrument readings where available,
- orderly shutdown,
- controller safe/manual/neutral state where applicable,
- physical isolation of every selected isolation point,
- lock/tag application to each energy-isolating device,
- stored-energy relief,
- continued monitoring if reaccumulation is possible,
- verification before work,
- missing evidence field holds,
- restoration/re-energization checks.

The procedure must not say:

- "safe to work" unless site policy explicitly permits that wording,
- "verified" when verification evidence is missing,
- "depressurized" solely from level indication,
- "isolated" solely from an instrument reading,
- "OSHA requires inlet valve first."

## 16. Acceptance Test Matrix

Minimum deterministic tests:

| Case | Expected Result |
|---|---|
| Single inlet and outlet with valves | Both source paths isolated; blue overlays; procedure includes both valves |
| Parallel bypass valve present | Manual-review orange overlay and warning |
| Missing process boundary isolation | `not_isolated` or provisional status; unresolved coverage warning |
| Missing bleed/vent/drain | Stored-energy field hold |
| Missing verification point | Verification field hold |
| Pressure indicator present | Pressure interpretation under verification/support step; does not auto-certify |
| Level indicator present | Drain-down/inventory interpretation; not depressurization |
| Controller present | Control-state step only |
| Downstream pump reachable through directed line | Likely downstream warning and red overlay if bbox exists |
| Downstream endpoint off-page | Warning in procedure; no current-page overlay without bbox |
| HILT unavailable | Optional analyses unavailable; core run fails or degrades according to stage |
| STLM unavailable | No bboxes; warnings; no false overlays |
| Wrong project config | Clear failure; no global fallback |
| Agent skips validation | Runner forces validation or fails |

Minimum regression commands:

```bash
uv run python -m unittest discover -s tests
uv run python -m compileall -q agent bbox.py evidence.py impact.py instrument_context.py loto.py output.py run.py viewer.py
uv run python -m run --equipment <TAG> --quiet
```

## 17. Open Questions for Authority Review

These require site safety or operations authority:

1. Which work scopes require positive isolation by site policy?
2. Can any local pressure gauge or indicator satisfy a verification requirement, or is a bleed/test point always required?
3. Can remote transmitters be accepted for any verification purpose?
4. What pressure threshold defines depressurized for each service?
5. What hold time is required to show no pressure reaccumulation?
6. What level threshold defines drained or empty for each vessel type?
7. Which services require double block and bleed?
8. Which services require blinds/spades rather than valves?
9. How should control valves and check valves be treated?
10. What terminology should be used: Isolation Procedure, Energy Control Procedure, Lockout/Tagout Procedure, or site-specific naming?
11. What approval workflow is required before a generated procedure can be used in the field?
12. What audit retention is required for JSON, trace, and HTML outputs?

## 18. Rollout Plan

Phase 1: Decision-support only

- Deterministic runner.
- Agentic runner with deterministic guardrails.
- Structured JSON response contract.
- Development HTML viewer generated from the same JSON.
- No live plant data.
- No approval workflow.

Phase 2: Site policy integration

- Site-specific thresholds.
- Site-specific accepted verification methods.
- Site-specific positive isolation rules.
- Work scope templates.
- Review/approval metadata.

Phase 3: Operational integration

- Work order/permitting integration.
- Live historian readings as optional context.
- Group lockout and shift handover workflows.
- User corrections saved as structured overrides.
- Procedure versioning and audit retention.

## 19. Success Criteria

The product is successful when:

- reviewers can see exactly why each isolation point was selected,
- unresolved paths and missing evidence are not hidden,
- procedure text follows the OSHA sequence,
- instrument guidance is useful but does not overstate certainty,
- downstream effects are visible,
- visual overlays match structured data,
- deterministic and agentic outputs agree on safety-critical facts,
- project switching cannot silently use the wrong job/P&ID,
- a safety authority can audit the output without relying on model reasoning.

## 20. Explicit Non-Claims

The product does not claim:

- OSHA compliance certification,
- field readiness,
- safe-to-work authorization,
- correct valve operability,
- current plant state,
- lock ownership,
- personnel clearance,
- permit approval,
- site policy approval.

The product provides structured isolation planning evidence and a draft procedure for qualified human review.
