## **Equipment Isolation Planner (Smart LOTO)**

## **1\. Objective & Executive Summary**

In heavy industrial enterprises, isolating equipment for maintenance (Lockout/Tagout or LOTO) is an exceptionally high-risk operation. If an isolation boundary is miscalculated, field personnel risk exposure to high-pressure hydrocarbons, toxic gases, or superheated steam.  
The goal of this feature is to bridge high-level business value—slashing manual engineering drawing reviews by up to 50% —with a safe, concrete execution layer. By combining our existing P\&ID JSON engine and the JanusGraph (UniGraph) database, plant360.ai will automatically discover isolation boundaries, map out required documentation, generate a step-by-step physical checklist, and visually highlight the isolated engineering envelope directly on digital drawings.

## **2\. Data Asymmetry & Graceful Degradation Architecture**

If we design a system that requires perfect, fully ingested data across five different engineering disciplines to function, it will fail in 90% of real-world brownfield plants. Most facilities have an up-to-date P\&ID asset baseline, but their electrical diagrams are buried in physical binders and their line lists live in stale Excel sheets.  
Our product architecture handles this reality via **Graceful Degradation**. The P\&ID and JanusGraph serve as our Core Infrastructure; everything else is an optional, enhanced layer. If supplementary documents are missing, the system degrades to a structured, interactive manual workflow instead of blocking the user.

### **2.1 Tiered Data Classification**

To ensure high system availability regardless of a plant's digital maturity, data inputs are categorised into two tiers:

* **Tier 1 (Core \- Mandatory):** P\&ID JSON and JanusGraph. The system cannot generate an isolation plan without these components.  
* **Tier 2 (Enhanced \- Optional):** SLDs, Piping Isometrics, C\&E Matrices, Line Lists, and Legacy SOPs. These enhance automation but do not act as blockers.  
* 

### **2.2 Tier 2 Missing Data Fallback Matrix**

When a Tier 2 document type is missing from the database, the system applies the following fallback logic:

| Missing Document Type | Operational Impact | Automated Alternative / Guessing Logic | Human-in-the-Loop (HITL) Fallback Action |
| :---- | :---- | :---- | :---- |
| **Electrical Single Line Diagrams (SLDs)** | Cannot automatically pinpoint the exact breaker ID or MCC location to cut power. | The system skips automated step creation for electrical isolation. | **Mandatory UI Infill:** The UI halts at Step 1 (Command & Control) and prompts the engineer: *"Electrical data missing. Please manually input Breaker ID and Substation/MCC Location to proceed"*. |
| **Line Lists / Piping Class Specs** | Cannot automatically verify line pressures/temperatures to dictate if a Double Block & Bleed (DBB) is mandatory. | **Conservative Default:** The algorithm reads the fluid\_code from the P\&ID JSON. If the code indicates a hazardous fluid (e.g., H2S, Hydrocarbon), the system defaults to the highest safety standard (DBB). | **User Validation:** The UI flags the boundary with a banner: *"Using maximum safety default (DBB) due to missing Class Specs. Click to downgrade to Single Block if pressure permits"*. |
| **Piping Isometrics (Isos)** | Cannot provide exact physical 3D coordinates or precise flange pair locations for spectacle blinds. | The system identifies the closest boundary valve on the 2D P\&ID and notes that positive isolation is required nearby. | **Field Verification Step:** The generated checklist adds a mandatory operational check: *"Field walkdown required: Visually locate closest physical flange pair to Valve \[ID\] for blind insertion"*. |
| **Cause & Effect (C\&E) Matrices** | Cannot programmatically evaluate if closing an isolation valve will trip an automated process loop upstream or downstream. | The system runs a purely topological downstream reachability check on the JanusGraph to look for active control loops. | **Engineering Review:** The system displays a generalized warning: *"Topological flow cut detected on Line \[ID\]. Verify manually that this does not trip interlocks on active control system"*. |

### 

### **2.3 Data Readiness Indicator**

When an engineer selects an asset for isolation, the platform side-panel displays a **Data Completeness Score** before running the isolation algorithm:

* **100% Score (Full Tier 1 \+ Tier 2 Ingestion):** The system generates a completely hands-free, multi-disciplinary isolation checklist spanning electrical, process, and physical blinding.  
* **40% \- 60% Score (Tier 1 Only):** The system displays a warning badge: **"Core Process Isolation Mode Only"**. The UI alerts the user that they must manually supplement the checklist with electrical and spatial verification steps.

## 

## 

## **3\. Multi-Disciplinary Data Ingestion Pipeline (Tier 2 Detail)**

To build a comprehensive isolation plan when data is available, the UniGraph schema ingests and parses the following legacy document types alongside the core P\&ID JSON:

### **A. Electrical Isolation Data**

* **Single Line Diagrams (SLDs) / Electrical Schematics:** Traces the electrical distribution from the main substation down to the Motor Control Centre (MCC) switches. Used to map the target equipment vertex to its exact breaker ID and switchgear location.  
* **Panel Schedules & Electrical Load Lists:** Details auxiliary loads, like localised heat-tracing cables or lube oil pumps, linked to the primary asset.


### **B. Physical & Spatial Layout Data**

* **Piping Isometrics (Isos):** Detailed 3D-angled technical drawings of individual pipe segments. They provide exact physical coordinates of flange pairs to identify where to insert physical spectacle blinds or skillets.  
* **Plot Plans & General Arrangement (GA) Drawings:** Maps the physical layout of the facility to provide real-world routing instructions, such as floor levels or unit quadrants.


### **C. Automation, Control, & Safety System Data**

* **Cause & Effect (C\&E) Matrices:** Spreadsheets mapping safety system logic (e.g., if a sensor drops below a certain pressure, a valve slams shut). Vital for downstream impact analysis to ensure isolation does not accidentally trip adjacent operational units.  
* **Instrument Loop Diagrams (ILDs):** Identifies the fail-safe states (Fail Open / Fail Closed) of automated control valves if instrument air or power is severed.


### **D. Process Safety & Design Specifications**

* **Line Lists / Piping Class Specifications:** Databases containing design pressures, design temperatures, and pipe materials. This data dictates whether the system mandates a Single Block Valve or a strict Double Block and Bleed (DBB) protocol.  
* **Safety Data Sheets (SDS / MSDS):** Outlines the toxicity, flammability, and volatility metrics of process chemicals to feed the logic engine's risk evaluation.


### **E. Historical Compliance Baselines**

* **Legacy LOTO Master Cards / Standard Operating Procedures (SOPs):** PDF records of previously approved manual plans used to pre-populate or train graph logic models against tribal plant knowledge.  
    
    
    
    
  


## **4\. Functional Requirements**

### **FR-1: Isolation Boundary Discovery (Graph Traversal)**

* **Description:** Given a target equipment ID selected from the JanusGraph, the system must execute a graph traversal algorithm to locate the nearest isolation points, both upstream (suction) and downstream (discharge).  
* **Technical Input:** The query must traverse edges representing piping segments, filtering past instruments, until it hits vertices tagged as Valve with an attribute of Is\_Isolatable \= True.  
* **Output:** A deterministic list of boundary valves required for complete physical isolation.


### **FR-2: Isolation Scheme Selection**

* **Description:** The system must evaluate the required safety standard based on fluid properties, pressure, and temperature pulled from the Piping Class Specs:  
  * **Single Block Valve:** Assigned automatically for low-risk utility lines like cooling water.  
  * **Double Block and Bleed (DBB):** Mandated for hazardous, high-pressure, or toxic chemicals. The algorithm must search for two block valves in series with a bleed/drain valve vertex located between them.  
  * **Spectacle Blinds / Line Breaking:** Identification of the closest flange pairs where physical blinds must be inserted for positive isolation.  
    

### **FR-3: Vent & Drain Localisation (Depressurisation)**

* **Description:** To address trapped pressure inside the isolated equipment envelope, the system must automatically identify all Drain\_Valve and Vent\_Valve vertices located inside the isolated boundary. This allows the engineer to safely depressurize and drain the system before line breaking.


### **FR-4: Downstream Impact Analysis**

* **Description:** The system must perform a downstream reachability check from the isolated boundary to see which processes, equipment, or control loops will lose feed or pressure.  
* **Output:** A warning list (e.g., *"Isolating P-101A will cut feed to Heat Exchanger E-102 and trigger Low-Flow Alarm on FIC-201"*).


### **FR-5: Interactive P\&ID Component Highlighting**

* **Description:** The system must render an interactive digital view of the P\&ID using the coordinates stored in the P\&ID JSON, dynamically highlighting lines and components based on their safety states.  
* **UI Colour-Coding Standards:**  
  * **Red (De-energised/Isolated Envelope):** Target equipment and internal piping segments being isolated.  
  * **Yellow/Orange (Energy Isolation Points):** Boundary block valves, blinds, or breakers that must be locked out.  
  * **Blue (Depressurization Points):** Specific vent and drain valves that need to be opened.  
  * **Grey (Operational/Live):** Surrounding plant equipment and lines remaining live and unaffected.

### **FR-6: Deterministic Sequence Step Generator**

* **Description:** The system must use a rules-based engine to sequence the JanusGraph output into a logical, step-by-step field checklist rather than a randomized list.  
* **Mandatory Execution Sequence:**  
  1. **Command & Control:** Shut down target equipment (e.g., stop pump motor) and rack out the electrical breaker.  
  2. **Primary Isolation:** Close upstream (suction) block valves.  
  3. **Secondary Isolation:** Close downstream (discharge) block valves.  
  4. **Depressurization:** Open designated vents and drains to bleed trapped pressure.  
  5. **Positive Isolation:** Insert physical spectacle blinds or skillets if required for line breaking.  
     

## **5\. Edge Cases & Safety Failure Modes**

### **~~Edge Case 1: The "Passing" (Leaking) Valve Bypass~~**

* **~~Scenario:~~** ~~A field operator attempts to close the designated isolation valve (V-001), but it is leaking internally ("passing"), compromising safety.~~  
* **~~Requirement:~~** ~~The UI must allow the engineer to flag a valve as "Defective/Passing". The backend algorithm must instantly re-run the JanusGraph traversal, skip that valve, and locate the next available backup isolation valve further upstream or downstream.~~


### **Edge Case 2: Control Valves Used as Isolations**

* **Scenario:** The shortest path algorithm stops at an Automated Control Valve (FCV/PCV) or a Pressure Safety Valve (PSV) and marks it as an isolation point.  
* **Requirement (Strict Guardrail):** Control valves can leak or actuate unexpectedly upon losing instrument air. The system must never default to a control valve for isolation unless explicitly overridden by a supervisor, and must traverse past it to find a manual block valve (e.g., Gate or Ball valve).


### **Edge Case 3: Shared Headers and Parallel Equipment (Manifolds)**

* **Scenario:** Two pumps (P-101A and P-101B) run in parallel and share a common suction header.  
* **Requirement:** If P-101A is isolated, the algorithm must ensure the selected valves do not cut off the suction fluid to P-101B, which must remain operational. The system must recognise manifold/tee vertices and validate the operational status of sibling paths.


### **Edge Case 4: Auxiliary & Utility Line Missing Links**

* **Scenario:** The engineer isolates main process lines but forgets auxiliary attachments like gland cooling water lines, steam jackets, or chemical seal flush plans.  
* **Requirement:** The JanusGraph schema must link the equipment vertex to both process streams and utility sub-graphs. The final isolation plan must feature distinct sections for Process Isolation, Utility Isolation, and Electrical Isolation.

### **Edge Case 5: Closed-Loop Systems & Back-Pressure**

* **Scenario:** A relief line connects the equipment to a flare header or closed blowdown drum, allowing high back-pressure to flow into the open equipment even if suction and discharge are isolated.  
* **Requirement:** The graph algorithm must trace all outgoing connections, including relief/vent systems, and identify the check valves or isolation valves required to prevent back-flow.

### **Edge Case 6: The Multi-Page P\&ID Jump (Cross-Drawing Boundaries)**

* **Scenario:** Target equipment sits on Drawing \#1, but the nearest safe upstream isolation valve is located on Drawing \#2 via an Off-Page Connector (OPC).  
* **Requirement (Visual):** The UI must support a multi-drawing view. The OPC on Drawing \#1 must pulse yellow; clicking it must flip the user to Drawing \#2, auto-centering and highlighting the remaining boundary path.  
* **Requirement (Procedural):** The checklist must explicitly group tasks by drawing ID and location tag (e.g., *"Step 3: Go to Unit 12, Layer 2\. Refer to Drawing PND-102. Close Valve V-105"*).

### **Edge Case 7: The "Trapped Fluid" Thermal Expansion Risk**

* **Scenario:** In a DBB setup, closing both block valves before opening the bleed valve can trap liquid inside a tight piping segment, causing a line rupture if ambient temperatures rise.  
* **Requirement:** The sequence generator must strictly enforce that a bleed/drain valve step is paired with the closure of the secondary block valve, explicitly prompting: *"Verify Bleed Valve V-002 is open BEFORE locking Downstream Valve V-003."*.

### **Edge Case 8: Visual Chaos / "Spaghetti Highlighting"**

* **Scenario:** In highly dense piping headers, highlighting dozens of interconnected lines makes the digital drawing unreadable, causing operators to misidentify look-alike valves in the field.  
* **Requirement:** The UI must include an "Isolation Mode" toggle. When enabled, it dims or fades out all non-essential layers of the P\&ID, leaving only the isolated equipment envelope, its boundary vertices, and adjacent context visible.


## **6\. UI/UX Component & Data Mapping**

To ensure the relationship between the drawing and the checklist is fully bidirectional, the UI elements are mapped to the backend JSON and graph layers as follows:

| UI Component | Action / Interaction | Backend Connection (JanusGraph / JSON) |
| :---- | :---- | :---- |
| **Interactive Checklist Step** | Hovering over a step (e.g., *"Step 4: Close Valve V-101"*). | Triggers the P\&ID viewer to instantly pan, zoom, and flash the bounding-box coordinates of Valve\_V-101 from the JSON layer. |
| **P\&ID Canvas** | Clicking a highlighted Valve on the drawing. | Highlights the corresponding step in the sidebar checklist and displays its current graph properties like size, valve type, and normal position. |
| **"Flag Variant" Button** | Clicking a valve on the drawing and selecting *"Mark as Frozen/Passing"*. | Triggers a partial graph re-traversal to find an alternative boundary vertex, updating both the visual overlay and the step list in real time. |

## 

## **7\. Human-in-the-Loop (HITL) Guardrails & Verification**

Because automated algorithms cannot replace final accountability in safety-critical environments, the system enforces two primary verification workflows:

1. **Error Resolution Workflow:** If the algorithm hits a dead end due to an un-digitized P\&ID boundary or a pipe edge at a sub-graph limit, it must not crash or output an incomplete boundary. It must flag an *"Incomplete Graph Boundary Warning,"* highlight the exact open-ended pipe segment on the digital UI, and force the Process Engineer to manually select an isolation boundary point on the screen.  
2. **Review & Approval Gate:** No generated isolation checklist can be dispatched to the field or synced to an external CMMS/EAM (like SAP or Maximo) without an explicit digital sign-off from the Chief Process Engineer, who reviews the plan using the interactive visual interface.

