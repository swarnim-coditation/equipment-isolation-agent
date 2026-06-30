# Equipment Isolation Agent Blueprint

## 1. Purpose

This document defines the proposed CNVRT Equipment Isolation Agent flow, request and response contracts, data dependencies, validation rules, and implementation considerations.

The agent supports the following user workflow:

1. A user opens a P&ID, plant graph, equipment view, or instrument view in CNVRT.
2. The user selects an equipment item, instrument, or line.
3. The user chooses **Isolate** from the CNVRT UI.
4. CNVRT sends selected entity context to the Equipment Isolation Agent.
5. The agent uses Unigraph, JanusGraph, CNVRT APIs, and HILT graph data to identify isolation actions.
6. CNVRT displays the resulting isolation report and highlights resolvable isolation points on the P&ID image.

The production architecture should not be tied to Langflow. Langflow can be used as the current prototype runtime, but the agent contract should be stable enough to support a standalone service later.

---

## 2. Core Use Case

CNVRT users inspect P&IDs, equipment, instruments, and plant graph data. The target interaction is a context-menu action where the user selects an equipment item, instrument, or line and chooses **Isolate**.

CNVRT invokes the Equipment Isolation Agent with the selected entity context. The agent identifies isolation actions using Unigraph connectivity and CNVRT visual data. CNVRT consumes the output to display an isolation report and highlight valid isolation points on the P&ID image.

The selected entity, Unigraph unit context, and P&ID job context must refer to the same drawing or unit. The agent must warn or reject the request when this cannot be verified.

---

## 3. System Context

### `cnvrt-ui-standard`

Main CNVRT UI. Provides P&ID and HILT interaction surfaces, including context-menu support for selected visual entities.

### `cnvrt-backend-api-training`

CNVRT backend API. Provides projects, collections, jobs, uploads, HILT graphs, job graphs, symbol metadata, and related P&ID data.

### `graph-convert`

Graph conversion backend. Relevant for producing or transforming graph structures used by Unigraph.

### `janusgraph-cassandra-elasticsearch`

Unigraph persistence stack. Stores graph relationships used to traverse from selected equipment to connected isolation devices.

### `demo-plantgraph-ui`

Reference UI for plant graph interactions, context menus, and graph visualization behavior.

### `plant360-ai-langflow-clone`

Current prototype runtime and report UI reference. Langflow is an implementation option, not a required production dependency.

---

## 4. Key Design Update: `cnvrt_id` Bridge

Each node in JanusGraph / Unigraph stores a `cnvrt_id`.

This should become the primary bridge between graph-discovered isolation candidates and HILT/P&ID visual nodes.

### Why This Matters

Previous bbox matching relied on less stable strategies:

- matching graph IDs against HILT node IDs,
- matching tags or names,
- matching approximate graph coordinates,
- applying manual coordinate transforms.

These approaches are fragile because Unigraph IDs, HILT node IDs, graph coordinates, and image coordinates may not share the same coordinate system or identifier scheme.

The `cnvrt_id` gives us a stronger deterministic bridge:

```text
Unigraph candidate node -> cnvrt_id -> HILT graph node / CNVRT visual entity -> bbox -> P&ID highlight
```

### Required Matching Strategy

For every candidate isolation point selected from Unigraph:

1. Read the candidate's `cnvrt_id` from JanusGraph node properties.
2. Fetch the HILT graph for the correct `job_id`.
3. Search HILT nodes and related visual records for the same `cnvrt_id`.
4. If a match is found, use the HILT node's bounding box as the final `bbox`.
5. If multiple HILT nodes match, rank by entity class, selected job context, tag, and path trace.
6. If no HILT match exists, leave `bbox: []` and return a warning.

The agent must not fall back to guessed or manually transformed coordinates unless the transform is formally validated and marked as reliable.

### Data Contract Implication

The agent response should preserve the bridge fields for traceability:

```json
{
  "uuid": "graph-candidate-id",
  "cnvrt_id": "source-cnvrt-id",
  "hilt_node_id": "matched-hilt-node-id-or-null",
  "bbox_match_method": "cnvrt_id"
}
```

The current UI may ignore these additional fields, but they should exist in the agent contract or debug/trace payload for validation.

---

## 5. End-To-End Flow

### Step 1: User Selects Isolate

The user selects an equipment item, instrument, or line in CNVRT and chooses the isolation action from the context menu.

Output: selected entity context.

### Step 2: CNVRT Calls The Agent

CNVRT sends project, collection, job, selected entity, and authentication context to the agent runtime.

Runtime options:

- current prototype: Langflow webhook;
- production target: standalone isolation agent API.

### Step 3: Agent Validates Context

The agent confirms that the selected entity belongs to the supplied project, collection, and P&ID job.

Data used:

- project metadata,
- collection metadata,
- job metadata,
- selected entity metadata,
- Unigraph unit context.

### Step 4: Agent Traverses Unigraph

The agent uses graph relationships to identify connected nozzles, lines, valves, instruments, branches, and boundary candidates.

Data used:

- JanusGraph / Unigraph traversal,
- `graph{project_id}_traversal`,
- selected entity vertex,
- connected physical paths.

### Step 5: Agent Selects Isolation Candidates

The agent chooses valid isolation devices by graph path, entity class, service type, policy configuration, and proximity to the selected entity boundary.

Output: ranked deterministic candidates.

### Step 6: Agent Resolves Visual Coordinates

The agent uses `cnvrt_id` as the primary bridge from Unigraph candidates to HILT/P&ID visual nodes.

Primary matching path:

```text
candidate.cnvrt_id -> HILT node with same cnvrt_id -> HILT bbox -> UI bbox
```

Fallback matching paths, in order:

1. direct HILT node ID if CNVRT provided one;
2. exact tag or source ID match;
3. validated job graph mapping;
4. no bbox if no safe match exists.

The agent must not return a bbox unless it is safely resolved.

### Step 7: CNVRT Displays The Result

CNVRT renders isolation report rows and highlights P&ID locations for points with valid bboxes.

---

## 6. Happy Path

The happy path is the expected flow when all required context and source data are available.

1. The user selects an equipment item, instrument, or line in CNVRT.
2. CNVRT sends the agent a request containing project context, drawing context, selected entity context, and authentication context.
3. The request includes a stable selected entity identifier, preferably `selected_cnvrt_id`.
4. The agent validates that the selected entity belongs to the supplied project, collection, and P&ID job.
5. The agent resolves the selected entity to a Unigraph vertex.
6. The agent traverses connected graph paths outward from the selected entity boundary.
7. The agent applies the isolation policy to select valid isolation candidates.
8. Each selected candidate includes `cnvrt_id` from Unigraph node properties.
9. The agent fetches the HILT graph for the correct `job_id`.
10. The agent matches each candidate to a HILT or CNVRT visual node using `cnvrt_id`.
11. The agent uses the matched HILT node bbox as the final P&ID image bbox.
12. The agent returns a deterministic response with isolation points, bboxes, traceability fields, and no warnings.
13. CNVRT displays the report and highlights the resolved isolation points on the P&ID image.

Expected happy-path result:

- Isolation candidates are selected from Unigraph connectivity.
- Visual placement is resolved through `cnvrt_id` matching.
- The UI can render both report rows and image markers.
- Traceability is available from report row back to Unigraph candidate and HILT visual node.

---

## 7. Minimum Required Inputs

These inputs are required to carry out the isolation workflow reliably.

- `project_id`
  - Required to select the correct CNVRT project and Unigraph traversal source.

- `job_id`
  - Required to fetch the correct P&ID image and HILT graph.

- Selected entity identifier
  - At least one of the following is required:
    - `selected_cnvrt_id`
    - `selected_unigraph_node_id`
    - `selected_entity_id`
    - `selected_tag`
  - Preferred identifier: `selected_cnvrt_id`.

- `selected_entity_type`
  - Required to determine whether the selected item is equipment, instrument, or line.

- Authentication/session context
  - Required to call CNVRT APIs and access protected job/HILT data.

- Unigraph access
  - Required to traverse physical connectivity and identify isolation candidates.

- HILT graph access for the selected `job_id`
  - Required to resolve P&ID image bboxes.

Minimum viable output with only required data:

- Isolation report rows can be generated if Unigraph traversal succeeds.
- P&ID image markers can be generated only if candidate `cnvrt_id` matches HILT visual data.
- If visual matching fails, the agent should still return report rows with `bbox: []` and warnings.

---

## 8. Inputs That Enrich The Result

These inputs are not always required, but they improve accuracy, traceability, UI quality, or operational usefulness.

- `collection_id`
  - Helps validate project/collection/job consistency.

- `job_name`
  - Helps cross-check Unigraph `unit_name` against the selected P&ID drawing.

- `collection_name`
  - Improves report display and user confirmation.

- `selected_hilt_node_id`
  - Provides a direct visual reference for bbox matching.

- `selected_bbox`
  - Provides visual context for fallback nearest-symbol matching.

- `selected_unigraph_node_id`
  - Avoids ambiguity when multiple graph vertices share similar tags.

- Line, service, and process metadata
  - Improves energy type classification and isolation reason quality.

- Valve metadata
  - Examples: `valve_type`, `fluid_service`, `actuator_type`, `drawing_number`.
  - Improves candidate filtering and method selection.

- Isolation policy configuration
  - Allows plant-specific rules for valid isolation devices, control valves, check valves, line breaks, blinds, and positive isolation preference.

- Historical or approved isolation templates
  - Can enrich recommendations if CNVRT has previous approved isolation plans.

- Maintenance scope context
  - Can support future multi-equipment or unit-level isolation planning.

---

## 9. Request Contract

The request must provide enough context for the agent to identify the selected entity, choose the correct graph, fetch the correct drawing, and return traceable results.

Required and recommended fields:

- `project_id`
  - Example: `274`
  - Requirement: Required
  - Purpose: Selects CNVRT project and Unigraph traversal source.

- `collection_id`
  - Example: `196`
  - Requirement: Required
  - Purpose: Identifies the collection or unit context for the drawing.

- `job_id`
  - Example: `2100`
  - Requirement: Required for visual output
  - Purpose: Identifies the P&ID image and HILT graph.

- `job_name`
  - Example: `pnid_2_bio_final`
  - Requirement: Recommended
  - Purpose: Cross-checks Unigraph `unit_name` against drawing context.

- `collection_name`
  - Example: `Unit`
  - Requirement: Recommended
  - Purpose: Used for report display.

- `selected_entity_type`
  - Example: `equipment`
  - Requirement: Required
  - Purpose: Defines whether the selected object is equipment, instrument, or line.

- `selected_tag`
  - Example: `BT-11`
  - Requirement: Required if ID is absent
  - Purpose: Human-readable selected object tag.

- `selected_entity_id`
  - Example: `5383bc1f-d13b...`
  - Requirement: Recommended
  - Purpose: Stable backend, HILT, or graph ID for exact lookup.

- `selected_unigraph_node_id`
  - Example: `82645008`
  - Requirement: Recommended
  - Purpose: Direct graph vertex reference when available.

- `selected_hilt_node_id`
  - Example: `b144cf53-003c...`
  - Requirement: Recommended
  - Purpose: Direct visual node reference for bbox matching.

- `selected_cnvrt_id`
  - Example: `cnvrt-entity-id`
  - Requirement: Strongly recommended
  - Purpose: Primary bridge between Unigraph and HILT/P&ID visual data.

- `selected_bbox`
  - Example: `[1200, 800, 40, 30]`
  - Requirement: Optional
  - Purpose: Visual context for nearest-symbol matching.

- `auth_context`
  - Example: Bearer token/session
  - Requirement: Required
  - Purpose: Allows authorized CNVRT API access.

- `flow_id` or `request_id`
  - Example: `ab6a68fb...`
  - Requirement: Runtime-dependent
  - Purpose: Used to store or retrieve asynchronous results.

Example request:

```json
{
  "action": "isolate",
  "project_id": 274,
  "collection_id": 196,
  "collection_name": "Unit",
  "job_id": 2100,
  "job_name": "pnid_2_bio_final",
  "selected_entity_type": "equipment",
  "selected_entity_id": "5383bc1f-d13b-45f6-8f06-74c00eab0005",
  "selected_unigraph_node_id": "optional-if-known",
  "selected_hilt_node_id": "optional-if-known",
  "selected_cnvrt_id": "optional-but-preferred",
  "selected_tag": "BT-11",
  "selected_bbox": [1200, 800, 40, 30],
  "request_id": "optional-client-request-id"
}
```

---

## 10. Isolation Policy Configuration

The configuration should define business policy, not encode every graph scenario. Complex graph behavior such as branch handling, loop detection, path search, and fallback traversal belongs in the agent algorithm.

### Configurable Policy

- Maximum traversal depth.
- Eligible isolation classes.
- Classes excluded from final isolation output.
- Conditional classes such as check valves and control valves.
- Whether positive isolation is preferred.
- Whether line break or blind/spade options should be included.
- Maximum candidates per equipment boundary or path.

### Algorithmic Behavior

- Traverse all relevant connected nodes from each equipment boundary.
- Detect branches and evaluate each branch path separately.
- Continue beyond non-final nodes such as pipes, nozzles, tees, reducers, check valves, and control valves.
- Stop or warn when another equipment boundary is reached before an isolation point.
- Detect loops and prevent repeated traversal.
- Rank valid candidates by path role, distance, depth, and isolation quality.

Example policy config:

```json
{
  "max_traversal_depth": 6,
  "eligible_isolation_classes": [
    "gate_valve",
    "ball_valve",
    "globe_valve",
    "butterfly_valve",
    "plug_valve",
    "needle_valve",
    "blind_flange",
    "spectacle_blind",
    "spade",
    "breaker",
    "disconnect"
  ],
  "excluded_output_classes": [
    "equipment",
    "equipment_nozzle",
    "pipe",
    "tee",
    "elbow",
    "reducer",
    "text",
    "label",
    "pns",
    "pnsg"
  ],
  "conditional_classes": {
    "check_valve": "traverse_through",
    "control_valve": "traverse_through"
  },
  "prefer_positive_isolation": true,
  "include_line_break_options": true,
  "max_candidates_per_boundary": 3
}
```

---

## 11. Agent Processing Model

The processing model separates entity resolution, context validation, graph traversal, candidate selection, visual placement, and response construction.

### Phase 1: Resolve Selected Entity

Inputs:

- `selected_entity_type`
- `selected_tag`
- `selected_entity_id`
- `selected_unigraph_node_id`
- `selected_hilt_node_id`
- `selected_cnvrt_id`

Logic:

- Prefer stable IDs over text tags.
- Resolve the selected object to a Unigraph vertex where possible.
- Preserve `cnvrt_id` as a required traceability field when available.
- Record all identifiers for traceability.

### Phase 2: Validate Drawing Context

The agent confirms that the selected entity, Unigraph unit, CNVRT collection, and P&ID job refer to the same drawing context.

APIs and failure behavior:

- `GET /projects/{project_id}`
  - Data fetched: Project metadata.
  - Purpose: Validate project and label output.
  - Failure behavior: Return project validation error.

- `GET /projects/{project_id}/collections`
  - Data fetched: Project collections.
  - Purpose: Confirm collection ownership.
  - Failure behavior: Fallback to job details if available.

- `GET /collections/{collection_id}`
  - Data fetched: Collection metadata.
  - Purpose: Confirm collection name and project link.
  - Failure behavior: Warn if unavailable.

- `GET /jobs/get_job_details/{job_id}`
  - Data fetched: Job context.
  - Purpose: Confirm job, project, and collection.
  - Failure behavior: Fallback to `GET /jobs/{job_id}`.

- `GET /jobs/{job_id}`
  - Data fetched: Full job details and image reference.
  - Purpose: Required by UI to render drawing.
  - Failure behavior: Return report without visual rendering or fail if visual output is required.

- `GET /jobs/list_complete?project_id={project_id}`
  - Data fetched: Project job list.
  - Purpose: Map Unigraph `unit_name` to a job if job context is missing.
  - Failure behavior: Require explicit job context from CNVRT.

### Phase 3: Traverse Connectivity Graph

Data sources and failure behavior:

- `ws://{host}:{port}/gremlin`
  - Data fetched: Unigraph connection.
  - Purpose: Access graph data.
  - Failure behavior: Return graph connection error.

- `graph{project_id}_traversal`
  - Data fetched: Project traversal source.
  - Purpose: Keep traversal scoped to the project.
  - Failure behavior: Return traversal source error.

- Equipment lookup
  - Data fetched: Selected equipment or entity vertex.
  - Purpose: Find traversal root.
  - Failure behavior: Return selected entity not found.

- Physical traversal
  - Data fetched: Nozzles, lines, valves, instruments, branches.
  - Purpose: Build isolation boundary candidates.
  - Failure behavior: Return partial result with warning if traversal is incomplete.

Traversal must include `cnvrt_id` in normalized node properties for all traversed nodes.

### Phase 4: Select Isolation Candidates

The traversal should be broad, but final candidate selection should be narrow. Pipes, nozzles, tees, reducers, containers, labels, and grouping nodes may be required for path context, but they should not become final isolation points unless policy explicitly allows the class or metadata indicates an isolation function.

Candidate signals:

- `entity_class`, such as `gate_valve`, `ball_valve`, `blind_flange`.
- `valve_type`, `fluid_service`, service tag, or line tag.
- Path from selected entity boundary.
- Traversal depth and source nozzle.
- `cnvrt_id` availability for visual traceability.

Selection rules:

- Evaluate each equipment boundary connection as a separate path search.
- Prefer the first valid isolation device on each path when policy allows.
- Continue traversal through conditional classes such as check valves or control valves when they are not acceptable final isolation points.
- Group candidates by source nozzle, connected line, or branch path.
- Keep candidate ID, `cnvrt_id`, source component, traversal depth, and path trace.
- Do not use an LLM to invent isolation points.

### Phase 5: Resolve Visual Coordinates

The graph determines what should be isolated. CNVRT visual data determines where each point appears on the P&ID image.

Primary strategy:

```text
Unigraph candidate cnvrt_id -> HILT/CNVRT visual node cnvrt_id -> bbox
```

APIs and failure behavior:

- `GET /jobs/get_job_hilt_graph/{job_id}`
  - Data fetched: HILT nodes, image size, symbol bboxes, candidate visual metadata.
  - Purpose: Primary source for final `bbox`. Search by `cnvrt_id` first.
  - Failure behavior: Return isolation points with empty bbox and warning.

- `GET /projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph`
  - Data fetched: Nested job graph if available.
  - Purpose: Potential bridge from graph candidate IDs or `cnvrt_id` to original image coordinates.
  - Failure behavior: Fallback to HILT `cnvrt_id`, token, and exact ID matching.

- `GET /bounding-box/...`
  - Data fetched: Stored bounding box records.
  - Purpose: Fallback for symbol/entity bbox lookup by `cnvrt_id` or entity ID.
  - Failure behavior: Do not output visual box if unresolved.

- `GET /hilt/...`
  - Data fetched: HILT records and annotations.
  - Purpose: Fallback for visual-symbol metadata and `cnvrt_id` lookup.
  - Failure behavior: Continue with available data.

- `GET /pid-entities/...`
  - Data fetched: PID entity classifications.
  - Purpose: Map visual symbols to semantic types and `cnvrt_id`.
  - Failure behavior: Use Unigraph/HILT classification instead.

- `GET /symbol_text_line_master/...`
  - Data fetched: Symbol, text, line records and original coordinates.
  - Purpose: Fallback for exact coordinates, text-line links, and `cnvrt_id` mapping.
  - Failure behavior: Leave `bbox` empty if no safe match exists.

### Phase 6: Build Response Payload

Included data:

- Project, collection, and job context.
- Isolation point rows.
- Traceability: candidate ID, `cnvrt_id`, HILT node ID, source component, path information, and matching method.
- Warnings for partial or unresolved visual matches.

Output rules:

- Return `bbox` only when confidently resolved.
- Return `tag_number` only when present in source data.
- Reject off-image boxes.
- Do not silently mix drawing contexts.

---

## 12. Agentic LLM Planning Layer

The agent may include an LLM planning loop, but the LLM must not be the source of truth for plant topology, device existence, or final isolation assurance.

The purpose of the LLM layer is to reason over supplied evidence and decide what additional evidence is needed before a usable isolation procedure can be written. The LLM may request graph, P&ID, HILT, STLM, or image-derived evidence through approved tools. It must not invent isolation devices, bboxes, tags, graph paths, blinds, bleeds, vents, drains, gauges, or pressure indicators.

### Observed Project 274 Graph Evidence

Gremlin exploration of `graph274_traversal` shows the current Unigraph has useful but incomplete safety evidence.

Available graph structures include:

- labels: `Equipment`, `Component`, `PNS`, `PNSG`, `Loop`, `Unit`, `TagNode`, `Section`, `PDFFile`, `Plant`;
- edge labels: `PHYSICALLY_HAS_A`, `PHYSICALLY_CONNECTED_TO`, `HAS_A`, `ASSOCIATED_WITH`, `STARTS_AT`, `ENDS_AT`, `has_connection`;
- equipment nozzles as `Component` nodes with `entity_class: equipment_nozzle`;
- isolation valves as `Component` nodes with `entity_class` such as `gate_valve` and `ball_valve`;
- piping structure as `PNS` and `PNSG` nodes with service tags, flow medium, nominal diameter, material class, and some valve-service metadata;
- pressure indication candidates as instrument components with function names such as `PI`, `PI-11`, or similar variants;
- tees and off-page connectors that can represent branches or unresolved continuation paths.

Current graph evidence did not expose obvious `entity_class` values for:

- blind flange;
- blank flange;
- spade;
- spectacle blind;
- bleed;
- vent;
- drain;
- pressure gauge as a normalized gauge class;
- electrical breakers or disconnects.

This means the LLM planner must be given access to the P&ID visual JSON and image-derived evidence, not just Unigraph. Missing blinds, bleeds, vents, drains, and gauges must be treated as missing evidence unless confirmed by HILT, STLM, OCR, symbol classification, or another deterministic source.

### Agentic Loop Contract

The LLM loop should follow this structure:

```text
Evidence state -> LLM evidence planner -> approved tool request -> deterministic tool result -> updated evidence state -> validator
```

Allowed LLM actions:

- request more evidence through named tools;
- classify path evidence as positive, proven, unproven, not isolated, or insufficient;
- propose an ordered isolation procedure from validated evidence;
- explain missing evidence and assumptions;
- recommend human review when evidence is incomplete.

Forbidden LLM actions:

- write arbitrary Gremlin in normal production mode;
- invent graph nodes, tags, visual IDs, or bboxes;
- treat a check valve or control valve as a final isolation barrier unless plant instructions explicitly allow it;
- claim that equipment is isolated when the deterministic validator has not passed;
- override plant/site isolation instructions.

### Approved Tool Set

The LLM should call named tools. Each tool owns its Gremlin/API implementation, depth limits, label filters, result limits, and output schema.

Recommended tools:

- `find_equipment_boundaries(equipment_id)`
- `find_paths_from_nozzle(nozzle_id, max_depth)`
- `find_nearest_isolation_devices(path_id)`
- `find_bleeds_vents_drains(path_id)`
- `find_pressure_indicators(path_id)`
- `find_blinds_spades_flanges(path_id)`
- `find_bypass_paths(barrier_candidate_id)`
- `check_alternate_route_to_equipment(blocked_node_ids)`
- `classify_connected_energy_sources(equipment_id)`
- `fetch_pid_visual_json(job_id)`
- `fetch_hilt_graph(job_id)`
- `fetch_stlm_symbols(job_id)`
- `inspect_pid_image_region(job_id, bbox_or_path_context)`

The P&ID JSON and image tools are required because the graph may not model every field safety device. They should return structured symbol evidence, OCR/text evidence, and visual matches, not free-form observations.

### Plant Instructions Context

The LLM planner should receive plant or site instructions as natural language context plus structured work-scope flags.

Examples of instructions:

- require positive isolation for line breaking, confined-space entry, high pressure, toxic, flammable, corrosive, hot, steam, or high-consequence work;
- require bleed, vent, drain, pressure gauge, pressure indicator, or approved test point before classifying valve isolation as proven;
- prohibit check valves and control valves as final isolation barriers unless explicitly approved;
- require residual energy release and verification before authorizing work;
- require human review when the graph or P&ID evidence does not prove isolation.

These instructions guide the LLM, but they do not replace deterministic validation.

### Loop Break Conditions

The loop must stop when any of the following is true:

- validator returns `complete_positive_isolation`;
- validator returns `complete_proven_isolation`;
- validator returns `not_isolated` because at least one boundary path has no valid barrier or has an unresolved bypass;
- validator returns `insufficient_data` because required evidence cannot be found;
- the loop reaches the configured maximum iterations;
- a query signature repeats without producing new evidence;
- the LLM cannot name a useful next evidence request.

Recommended hard limits:

- `max_iterations`: 10;
- `max_depth_per_path`: 6 to 8;
- `max_nodes_per_query`: 500;
- `max_total_nodes_seen`: 3000;
- `max_tool_errors`: 2;
- `timeout_per_query_seconds`: 10.

### Deterministic Assurance Status

The final assurance status is computed by a validator, not by the LLM.

Allowed statuses:

- `complete_positive_isolation`: every path is isolated by physical separation or a physical barrier such as a blind, spade, spectacle blind, blank flange, removable spool, disconnection, or equivalent confirmed positive isolation.
- `complete_proven_isolation`: every path has valid block isolation plus a verified bleed, vent, drain, pressure gauge, pressure indicator, or approved test point on the isolated volume.
- `provisional_unproven_isolation`: every path has at least one valid block valve, but verification or positive isolation evidence is missing.
- `not_isolated`: at least one path lacks a valid barrier, has an unresolved bypass, or remains connected to an energy/material source.
- `insufficient_data`: graph, P&ID, or visual evidence is incomplete or inconsistent.

### Ordered Procedure Output

The LLM may write the ordered procedure after validation. The procedure should follow this order unless plant instructions require a stricter sequence:

1. Prepare the job, confirm work scope, identify hazards, and identify energy/material sources.
2. Notify affected personnel.
3. Shut down equipment using normal operating controls.
4. Stop or isolate feed and transfer sources.
5. Operate selected block valves or positive isolation devices.
6. Apply locks and tags to energy isolating devices.
7. Install blinds, spades, blank flanges, or disconnections where required and evidenced.
8. Open approved bleed, vent, or drain points in a controlled sequence.
9. Depressurize, drain, purge, flush, or otherwise release residual energy.
10. Verify zero or safe pressure using confirmed gauges, pressure indicators, or approved test points.
11. Confirm no continuing flow or pressure reaccumulation.
12. Authorize work only when the validator status and human review requirements allow it.
13. Maintain verification during work where reaccumulation is possible.
14. Restore service in controlled reverse sequence after work completion.

---

## 13. Edge Cases And Fallback Behavior

The agent should explicitly handle edge cases and return warnings rather than silently producing incorrect output.

### Selected Entity Issues

- Selected entity is not found in Unigraph.
  - Behavior: Return an error or warning with the identifiers searched.

- Selected tag matches multiple graph vertices.
  - Behavior: Prefer stable IDs such as `selected_cnvrt_id` or `selected_unigraph_node_id`; otherwise return an ambiguity warning.

- Selected entity type is unsupported.
  - Behavior: Return an unsupported entity warning and do not attempt isolation.

### Drawing And Context Issues

- Selected entity belongs to a different P&ID job than the supplied `job_id`.
  - Behavior: Warn or reject. Do not mix Unigraph candidates from one drawing with HILT bboxes from another drawing.

- `job_id` is missing.
  - Behavior: The agent may produce graph-only report rows, but cannot provide P&ID visual bboxes.

- `collection_id` is missing or inconsistent.
  - Behavior: Attempt to infer from job details; include warning if unresolved.

- HILT graph is missing for the job.
  - Behavior: Return report rows with `bbox: []` and warning.

### `cnvrt_id` Matching Issues

- Candidate has no `cnvrt_id`.
  - Behavior: Attempt conservative fallback matching by HILT node ID, exact tag, or validated job graph mapping; otherwise leave `bbox: []`.

- HILT graph has no matching `cnvrt_id`.
  - Behavior: Leave `bbox: []`; include warning and candidate trace.

- Multiple HILT nodes match the same `cnvrt_id`.
  - Behavior: Rank by job context, entity class, tag, candidate path, and visual metadata; include match method in trace.

- Matched bbox is outside image bounds.
  - Behavior: Reject bbox and return warning.

### Graph Traversal Issues

- No isolation device is found within traversal depth.
  - Behavior: Return unresolved path warning and include source boundary path.

- A check valve or control valve appears before a block valve.
  - Behavior: Apply policy. Default behavior is to traverse through unless explicitly allowed as final isolation.

- A branch or tee creates multiple possible isolation paths.
  - Behavior: Evaluate each branch path separately.

- Another equipment boundary is reached before an isolation device.
  - Behavior: Warn that isolation may affect another equipment item or require scope review.

- Graph loop is detected.
  - Behavior: Stop repeated traversal on that path and return loop warning in debug/trace.

- Duplicate candidates appear across multiple paths.
  - Behavior: Deduplicate final rows while preserving all affected source paths in trace.

### API And Runtime Issues

- CNVRT API call fails due to auth or permission error.
  - Behavior: Return API failure with endpoint context.

- JanusGraph connection fails.
  - Behavior: Return graph connection error.

- Langflow run stores stale or previous output during prototype testing.
  - Behavior: Use version markers and trace/debug fields during prototype validation.

### Partial Result Policy

Partial results are acceptable when:

- isolation candidates are valid but visual bboxes are unresolved;
- some paths resolve and others do not;
- non-critical enrichment metadata is missing.

Partial results are not acceptable when:

- selected entity context is ambiguous;
- project/job context does not match;
- graph traversal fails completely;
- candidate selection cannot be traced to source data.

---

## 14. API And Data Source Inventory

Each endpoint or data source is tied to a specific phase of the isolation workflow.

Endpoint and data source list:

- `POST /agents/equipment-isolation/run`
  - Workflow phase: Agent start.
  - Fetches or receives: CNVRT request contract.
  - Reason: Preferred production entry point.

- `/api/v1/flows/end_flow/{flow_id}`
  - Workflow phase: Prototype result delivery.
  - Fetches or receives: Final output stored in Langflow.
  - Reason: Current Langflow integration path.

- `ws://{host}:{port}/gremlin`
  - Workflow phase: Graph traversal.
  - Fetches or receives: Unigraph connection.
  - Reason: Access physical plant connectivity.

- `graph{project_id}_traversal`
  - Workflow phase: Graph traversal.
  - Fetches or receives: Project graph traversal source.
  - Reason: Scope traversal to the correct project.

- `GET /projects/{project_id}`
  - Workflow phase: Context validation.
  - Fetches or receives: Project metadata.
  - Reason: Validate and label project context.

- `GET /projects/{project_id}/collections`
  - Workflow phase: Context validation.
  - Fetches or receives: Project collections.
  - Reason: Confirm collection ownership.

- `GET /collections/{collection_id}`
  - Workflow phase: Context validation.
  - Fetches or receives: Collection metadata.
  - Reason: Confirm collection and display name.

- `GET /jobs/list_complete?project_id={project_id}`
  - Workflow phase: Drawing mapping.
  - Fetches or receives: All jobs for project.
  - Reason: Map Unigraph unit names to job IDs.

- `GET /jobs/get_job_details/{job_id}`
  - Workflow phase: Drawing validation.
  - Fetches or receives: Job context.
  - Reason: Confirm job, project, and collection.

- `GET /jobs/{job_id}`
  - Workflow phase: Drawing display.
  - Fetches or receives: Full job details and image reference.
  - Reason: Required by UI for image rendering.

- `GET /uploads/{image_id}`
  - Workflow phase: Drawing display.
  - Fetches or receives: P&ID image blob.
  - Reason: Draw image behind markers.

- `GET /jobs/get_job_hilt_graph/{job_id}`
  - Workflow phase: BBox matching.
  - Fetches or receives: HILT visual graph, symbol bboxes, visual metadata, and `cnvrt_id` where available.
  - Reason: Primary source for marker coordinates.

- `GET /projects/{project_id}/collections/{collection_id}/jobs/{job_id}/graph`
  - Workflow phase: BBox matching.
  - Fetches or receives: Nested job graph.
  - Reason: Potential graph-to-image bridge.

- `GET /bounding-box/...`
  - Workflow phase: BBox fallback.
  - Fetches or receives: Stored bbox records.
  - Reason: Fallback for exact visual placement by entity ID or `cnvrt_id`.

- `GET /hilt/...`
  - Workflow phase: Visual fallback.
  - Fetches or receives: HILT records and annotations.
  - Reason: Additional visual-symbol metadata and `cnvrt_id` lookup.

- `GET /pid-entities/...`
  - Workflow phase: Semantic fallback.
  - Fetches or receives: PID entity records.
  - Reason: Map visual entities to semantic classes and `cnvrt_id`.

- `GET /symbol_text_line_master/...`
  - Workflow phase: Coordinate fallback.
  - Fetches or receives: Symbol, text, and line master records.
  - Reason: Recover original coordinates, text-line links, and `cnvrt_id` mapping.

- `ai_results`, `final_results`, or dedicated agent-result endpoint
  - Workflow phase: Production result storage.
  - Fetches or receives: Stored agent result.
  - Reason: CNVRT-native replacement for Langflow result storage.

---

## 15. Response Contract

The response must be deterministic, traceable, and directly consumable by the CNVRT UI.

Response fields:

- `error`
  - Type: boolean
  - Purpose: Indicates whether the agent failed.

- `message`
  - Type: string
  - Purpose: Short status message.

- `warnings`
  - Type: array
  - Purpose: Non-fatal issues such as unresolved bboxes or context mismatch.

- `debug`
  - Type: object
  - Purpose: Optional engineering trace data.

- `data[].job_id`
  - Type: number
  - Purpose: P&ID job where points should be rendered.

- `data[].job_name`
  - Type: string
  - Purpose: Drawing name.

- `data[].project_id`
  - Type: number
  - Purpose: CNVRT project ID.

- `data[].collection_id`
  - Type: number
  - Purpose: CNVRT collection ID.

- `isolation_points[].equipment_id`
  - Type: string
  - Purpose: Selected equipment or target item.

- `isolation_points[].uuid`
  - Type: string
  - Purpose: Stable graph/HILT/candidate ID.

- `isolation_points[].cnvrt_id`
  - Type: string or null
  - Purpose: CNVRT entity ID used to bridge Unigraph and HILT/P&ID data.

- `isolation_points[].hilt_node_id`
  - Type: string or null
  - Purpose: HILT visual node matched for bbox placement.

- `isolation_points[].bbox_match_method`
  - Type: string or null
  - Purpose: Matching method, preferably `cnvrt_id`.

- `isolation_points[].bbox`
  - Type: number array
  - Purpose: `[x, y, width, height]`; empty if unresolved.

- `isolation_points[].entity_class`
  - Type: string
  - Purpose: Candidate class such as `gate_valve`.

- `isolation_points[].tag_number`
  - Type: string or null
  - Purpose: Real source tag only.

- `isolation_points[].energy_type`
  - Type: string
  - Purpose: Hazard category.

- `isolation_points[].isolation_method`
  - Type: string
  - Purpose: Required action.

- `isolation_points[].reason`
  - Type: string
  - Purpose: Traceable explanation.

Example response:

```json
{
  "error": false,
  "message": "Completed",
  "warnings": [],
  "total_jobs_processed": 1,
  "data": [
    {
      "job_id": 2100,
      "job_name": "pnid_2_bio_final",
      "project_id": 274,
      "project_name": "Project 274",
      "collection_id": 196,
      "collection_name": "Unit",
      "isolation_points": [
        {
          "equipment_id": "BT-11",
          "uuid": "graph-candidate-id",
          "cnvrt_id": "candidate-cnvrt-id",
          "hilt_node_id": "matched-hilt-node-id",
          "bbox_match_method": "cnvrt_id",
          "bbox": [1598, 1874, 22, 22],
          "entity_class": "gate_valve",
          "tag_number": "real-tag-or-null",
          "energy_type": "process",
          "isolation_method": "close and lock valve",
          "reason": "Valve found on a connected path from nozzle N8_BT11. Candidate id: graph-candidate-id."
        }
      ]
    }
  ]
}
```

---

## 16. CNVRT UI Consumption

### Report Table

The report table should:

- read `data[].isolation_points[]`,
- display job, project, collection, tag, class, method, energy type, and reason,
- show unresolved bbox warnings where applicable,
- optionally expose trace fields such as `cnvrt_id`, `hilt_node_id`, and `bbox_match_method` in debug mode.

### P&ID Visualizer

The visualizer should:

- fetch job details from `GET /jobs/{job_id}`,
- fetch image from `GET /uploads/{image_id}`,
- draw markers only when `bbox` has four valid numbers,
- avoid rendering labels as literal `null` when `tag_number` is absent,
- optionally use `cnvrt_id` to cross-highlight the underlying CNVRT/HILT visual entity.

---

## 17. Validation And Safety Rules

1. The agent must traverse broadly for context but apply the isolation policy before returning final isolation points.
2. Directly connected nodes define the equipment boundary; valid isolation points may appear at depth 1, 2, 3, or later depending on topology.
3. The algorithm must support path-based isolation from each boundary connection rather than only direct-neighbor selection.
4. The agent must preserve `cnvrt_id` for selected entities, traversed candidates, and matched visual nodes where available.
5. `cnvrt_id` matching should be the primary bbox resolution method when both Unigraph and HILT/CNVRT visual data expose it.
6. The agent must not fabricate `tag_number`.
7. The agent must not fabricate `bbox`.
8. The agent must reject off-image bboxes.
9. The agent must warn or reject when selected entity context does not match the P&ID job context.
10. Every isolation point must include traceability to source component, candidate ID, `cnvrt_id`, and matching method where available.
11. Deterministic graph traversal and approved evidence tools must supply all device and path facts. LLMs may plan, request evidence, classify evidence, and write ordered procedures, but they must not invent isolation points or override deterministic validation.
12. Partial results are acceptable when unresolved points are clearly marked and warnings are returned.

---

## 18. Current Prototype Notes

The current prototype is implemented as Langflow custom nodes.

The current full-agent prototype chain is:

```text
11_Equipment_Isolation_Selector_Config.py
-> 03_Equipment_Boundary_Fetcher.py
-> 05_Isolation_Candidate_Finder.py
-> 07A_BBox_Resolver.py
-> 13_Plant_Isolation_Instructions.py
-> 14_Isolation_Evidence_State.py
-> 15_LLM_Evidence_Request_Planner.py
-> 16_Isolation_Assurance_Validator.py
-> 17_Isolation_Procedure_Writer.py
-> 07B_Final_Isolation_UI_Output.py
-> 09_Plant360_AI_Output_Component.py
```

`13_Plant_Isolation_Instructions.py` supplies site/work-scope instructions. `14_Isolation_Evidence_State.py` packages candidates, boundary coverage, bbox status, positive isolation evidence, verification evidence, bypass evidence, and missing evidence. `15_LLM_Evidence_Request_Planner.py` produces constrained named tool requests rather than arbitrary Gremlin. `16_Isolation_Assurance_Validator.py` assigns the authoritative assurance status. `17_Isolation_Procedure_Writer.py` writes ordered isolation and return-to-service steps from validated evidence.

The Langflow canvas export can be updated by inserting nodes 13 through 17 between `07A_BBox_Resolver.py` and `07B_Final_Isolation_UI_Output.py`. The final UI component remains backward-compatible because `isolation_points` is unchanged and the new fields are additive.

Langflow is not required for the final architecture. The same contract can be implemented by a standalone agent service.

The current Langflow output component stores results through:

```text
/api/v1/flows/end_flow/{flow_id}
```

A production CNVRT integration should use a stable agent endpoint and a CNVRT-native result storage mechanism.

---

## 19. Implementation Priority

Recommended near-term implementation order:

1. Insert nodes 13 through 17 into the Langflow canvas between bbox resolution and final UI output.
2. Replace the prototype rule-based evidence planner with a real LLM call that can only emit approved tool requests.
3. Implement the approved Gremlin/API/image tools behind the planner request schema.
4. Feed deterministic tool results back into `14_Isolation_Evidence_State.py` or its production equivalent.
5. Expand `16_Isolation_Assurance_Validator.py` from candidate-level heuristics to path-level barrier coverage, bypass proof, and verification-device association.
6. Ensure CNVRT sends `selected_cnvrt_id` where available.
7. Ensure Unigraph candidate extraction includes `cnvrt_id` in every normalized node property set.
8. Update CNVRT UI to expose `assurance_status`, `isolation_plan`, and missing evidence while preserving the existing marker behavior.
