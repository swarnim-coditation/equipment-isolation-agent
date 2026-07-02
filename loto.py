"""Deterministic LOTO procedure sequencer.

Builds an OSHA 29 CFR 1910.147(d)-structured procedure from the validated
isolation data. The PHASE ORDER IS FIXED BY REGULATION and is produced
deterministically here -- it is the authoritative procedure skeleton, just as
validate() is the authoritative assurance_status. The agent reasons WITHIN phases
(device ordering, rationale, citing OSHA via the RAG retriever) but cannot
reorder the regulated phases or skip them.

This is decision support for a POC, not a certified LOTO procedure.
"""
from __future__ import annotations

from evidence import BARRIER_KEYWORDS, POSITIVE_ENTITY_KEYWORDS

STANDARD = "29 CFR 1910.147"

# OSHA verification keywords split into the two LOTO purposes they serve:
# stored-energy RELIEF (d)(5) vs zero-energy VERIFICATION (d)(6).
RELIEF_KEYWORDS = {"bleed", "vent", "drain"}
VERIFY_KEYWORDS = {"gauge", "indicator", "test point"}
VERIFY_TAG_PREFIXES = {"pi", "pg"}


def build_loto_procedure(validation_data: dict, config, isolation_order: list | None = None) -> dict:
    candidates = validation_data.get("candidates", []) or []
    evidence = validation_data.get("evidence_state") or {}
    validation = validation_data.get("isolation_validation") or {}
    work_scope = (config.work_scope.__dict__ if hasattr(config.work_scope, "__dict__") else {})
    energy_types = sorted({(c.get("energy_type") or ["process"])[0] for c in candidates} or ["process"])

    isolation_devices, positive_devices = _devices(candidates, BARRIER_KEYWORDS, POSITIVE_ENTITY_KEYWORDS)
    relief_devices, verify_devices = _relief_and_verify(candidates)
    # NOTE: OSHA 1910.147 prescribes only the PHASE order, NOT the within-phase
    # device order. Resolution order:
    #   1. agent-committed order (engineering judgment) -- authoritative if present
    #   2. flow-grounded default (inlet/upstream first) from HILT-parsed flow direction
    #   3. engine candidate order (no flow data, agent has not proposed)
    has_known_flow = any(
        d.get("source_flow_role") in ("inlet", "outlet", "bidirectional") for d in isolation_devices
    )
    if isolation_order:
        isolation_devices = _apply_order(isolation_devices, isolation_order)
        positive_devices = _apply_order(positive_devices, isolation_order)
        order_source = "agent_engineering_judgment"
    elif has_known_flow:
        isolation_devices = _flow_default_order(isolation_devices)
        positive_devices = _flow_default_order(positive_devices)
        order_source = "flow_grounding_inlet_first_default"
    else:
        order_source = "engine_candidate_order_not_proposed"

    missing_evidence = validation.get("missing_evidence") or evidence.get("missing_evidence") or []

    phases = [
        _phase_1_preparation(config, energy_types, work_scope, isolation_devices),
        _phase_2_shutdown(),
        _phase_3_isolation(isolation_devices, positive_devices, order_source),
        _phase_4_lockout(isolation_devices, positive_devices),
        _phase_5_stored_energy(relief_devices, evidence),
        _phase_6_verification(verify_devices, evidence),
    ]

    procedure = {
        "standard": STANDARD,
        "regulatory_sequence_ref": "1910.147(d)",
        "equipment_tag": config.equipment_tag,
        "energy_types": energy_types,
        "work_scope": work_scope,
        "assurance_status": validation_data.get("assurance_status"),
        "phase_order_is_regulatory": True,
        "within_phase_order_is_regulatory": False,
        "within_phase_order_source": order_source,
        "phases": phases,
        "ordered_steps": _ordered_steps(phases),
        "release_from_loto_ref": "1910.147(e)",
        "release_note": "On completion: inspect area, ensure personnel clear, verify controls neutral, "
        "remove locks (reverse order), re-energize, notify affected employees.",
        "open_gaps": _open_gaps(missing_evidence, relief_devices, verify_devices),
    }
    return procedure


def _apply_order(devices: list, ordered_uuids: list) -> list:
    """Reorder devices to match the agent's chosen within-phase order. Devices not
    in the list are appended at the end in their original order. OSHA does not
    prescribe within-phase order; this is the agent's engineering judgment."""
    by_uuid = {str(d.get("uuid")): d for d in devices}
    result = []
    seen = set()
    for uid in ordered_uuids:
        uid = str(uid)
        if uid in by_uuid and uid not in seen:
            result.append(by_uuid[uid])
            seen.add(uid)
    for d in devices:
        if str(d.get("uuid")) not in seen:
            result.append(d)
    return result


def _ordered_steps(phases: list) -> list:
    """Flatten the procedure into a single numbered, ordered action list a field
    engineer can follow top-to-bottom. The PHASE order is OSHA-fixed; the within-
    Phase-3/4 device order is the agent's engineering judgment (OSHA is silent on it)."""
    steps = []
    n = 0
    for phase in phases:
        phase_num = phase.get("phase")
        ref = phase.get("ref")
        title = phase.get("title")
        if phase_num in (3, 4):
            devices = phase.get("devices") or []
            if not devices:
                n += 1
                steps.append(_step(n, phase_num, ref, title, f"{title}: no isolation devices identified."))
            for device in devices:
                n += 1
                action = _device_action(phase_num, device)
                steps.append(_step(n, phase_num, ref, title, action, device))
        elif phase_num == 5:
            reliefs = phase.get("relief_devices") or []
            if reliefs:
                for device in reliefs:
                    n += 1
                    steps.append(_step(n, phase_num, ref, title, f"Bleed/vent/drain: open {device.get('tag') or device.get('uuid')} to relieve stored energy.", device))
            else:
                n += 1
                steps.append(_step(n, phase_num, ref, title, "Relieve stored/residual energy. FIELD GAP: no bleed/vent/drain on P&ID -- field-locate one.", field_gap=True))
        elif phase_num == 6:
            verifies = phase.get("verify_devices") or []
            if verifies:
                for device in verifies:
                    n += 1
                    steps.append(_step(n, phase_num, ref, title, f"Verify zero energy at {device.get('tag') or device.get('uuid')} (gauge/indicator/test point).", device))
            else:
                n += 1
                steps.append(_step(n, phase_num, ref, title, "Verify isolation & de-energization. FIELD GAP: no gauge/test point on P&ID -- field-verify zero energy.", field_gap=True))
        else:
            n += 1
            steps.append(_step(n, phase_num, ref, title, f"{title}."))
    return steps


def _step(n, phase, ref, title, action, device=None, field_gap=False):
    step = {"step": n, "phase": phase, "ref": ref, "title": title, "action": action}
    if device:
        step["device_uuid"] = device.get("uuid")
        step["device_tag"] = device.get("tag")
        step["source"] = device.get("source_component")
    if field_gap:
        step["field_gap"] = True
    return step


def _device_action(phase_num, device):
    tag = device.get("tag") or device.get("uuid")
    source = device.get("source_component") or "?"
    role = device.get("source_flow_role")
    role_str = f" [{role.upper()}]" if role and role != "unknown" else ""
    verb = "Close & lock" if phase_num == 3 else "Affix lock/tag to"
    return f"{verb} {device.get('entity_class') or 'valve'} {tag} (source {source}{role_str})"


def _devices(candidates, barrier_kw, positive_kw):
    isolation = []
    positive = []
    for candidate in candidates:
        entity_text = _entity_text(candidate)
        props = candidate.get("properties") or {}
        is_barrier = any(kw in entity_text for kw in barrier_kw) or "close and lock" in str(candidate.get("isolation_method", "")).lower()
        is_positive = any(kw in entity_text for kw in positive_kw)
        device = _device_summary(candidate)
        if is_positive:
            positive.append(device)
        if is_barrier:
            isolation.append(device)
    if not isolation:
        # every selected candidate is a barrier candidate by construction; fall back to all
        isolation = [_device_summary(c) for c in candidates]
    return isolation, positive


def _relief_and_verify(candidates):
    relief = []
    verify = []
    for candidate in candidates:
        entity_text = _entity_text(candidate)
        tag_prefix = _tag_prefix((candidate.get("properties") or {}).get("tag") or candidate.get("tag_number"))
        if any(kw in entity_text for kw in RELIEF_KEYWORDS):
            relief.append(_device_summary(candidate))
        if any(kw in entity_text for kw in VERIFY_KEYWORDS) or tag_prefix in VERIFY_TAG_PREFIXES:
            verify.append(_device_summary(candidate))
    return relief, verify


def _device_summary(candidate):
    props = candidate.get("properties") or {}
    return {
        "uuid": str(candidate.get("candidate_id")),
        "tag": candidate.get("tag_number") or _first(props, ("tag", "name")),
        "entity_class": props.get("entity_class") or candidate.get("candidate_label"),
        "method": candidate.get("isolation_method"),
        "source_component": candidate.get("source_component_tag"),
        "traversal_depth": candidate.get("traversal_depth"),
        "energy_type": (candidate.get("energy_type") or ["process"])[0],
        "bbox_present": bool(candidate.get("bbox")),
        "source_flow_role": candidate.get("source_flow_role") or "unknown",
    }


_FLOW_ROLE_RANK = {"inlet": 0, "bidirectional": 1, "outlet": 2, "unknown": 3}


def _flow_default_order(devices):
    """Grounded default within-phase order: isolate INLET (upstream) devices first,
    then bidirectional, then outlet (downstream). Derived from the HILT-parsed flow
    direction (source_flow_role), NOT an OSHA rule. Within equal role, keep stable."""
    return sorted(devices, key=lambda d: (_FLOW_ROLE_RANK.get(d.get("source_flow_role"), 3), str(d.get("source_component") or "")))


def _phase_1_preparation(config, energy_types, work_scope, isolation_devices):
    requires_positive = any((work_scope or {}).get(k) for k in ("intrusive_work", "confined_space_entry", "hot_work", "high_risk_service"))
    return {
        "phase": 1,
        "ref": "1910.147(d)(1)",
        "title": "Preparation for shutdown",
        "objective": "Identify type and magnitude of energy, hazards, and control method.",
        "known": {
            "energy_types": energy_types,
            "requires_positive_isolation": requires_positive,
            "isolation_device_count": len(isolation_devices),
        },
        "field_action_required": [
            "Confirm actual energy type and magnitude (pressure/temperature) at the equipment.",
        ],
    }


def _phase_2_shutdown():
    return {
        "phase": 2,
        "ref": "1910.147(d)(2)",
        "title": "Equipment shutdown",
        "objective": "Orderly shutdown using the established process procedure.",
        "steps": [],
        "field_action_required": [
            "Follow the plant's established shutdown procedure for this equipment "
            "(not derivable from the P&ID graph). An orderly shutdown avoids additional hazard.",
        ],
    }


def _phase_3_isolation(isolation_devices, positive_devices, order_source):
    return {
        "phase": 3,
        "ref": "1910.147(d)(3)",
        "title": "Equipment isolation",
        "objective": "Operate each energy-isolating device to isolate the equipment from every energy source.",
        "devices": isolation_devices,
        "positive_isolation_devices": positive_devices,
        "within_phase_order_is_regulatory": False,
        "within_phase_order_source": order_source,
        "within_phase_ordering_note": (
            "OSHA 1910.147 does NOT prescribe the order in which multiple isolating devices are "
            "operated within this phase -- only that all are operated. The within-phase order shown "
            "is either the agent's committed judgment or a flow-grounded default (isolate INLET / "
            "upstream devices first) derived from the HILT-parsed flow direction (source_flow_role). "
            "Each device's source_flow_role (inlet/outlet) is provided -- use it to justify the order."
        ),
        "devices_by_source": _group_by_source(isolation_devices),
    }


def _phase_4_lockout(isolation_devices, positive_devices):
    all_devices = isolation_devices + [d for d in positive_devices if d not in isolation_devices]
    return {
        "phase": 4,
        "ref": "1910.147(d)(4)",
        "title": "Lockout/tagout device application",
        "objective": "Affix an individual lock (and tag) to EACH energy-isolating device, holding it in safe/off.",
        "devices": all_devices,
        "field_action_required": [
            "Each authorized employee applies their assigned individual lock to every isolating device.",
        ],
    }


def _phase_5_stored_energy(relief_devices, evidence):
    has_relief = bool(relief_devices)
    return {
        "phase": 5,
        "ref": "1910.147(d)(5)",
        "title": "Stored / residual energy relief",
        "objective": "Relieve, disconnect, restrain, or render safe all stored/residual energy (bleed/vent/drain).",
        "relief_devices": relief_devices,
        "field_action_required": [] if has_relief else [
            "NO bleed/vent/drain point was found on the P&ID. Treat trapped process energy as present; "
            "field-verify a means to bleed/vent before beginning work, and continue monitoring if "
            "reaccumulation is possible (1910.147(d)(5)(ii)).",
        ],
    }


def _phase_6_verification(verify_devices, evidence):
    has_verify = bool(verify_devices)
    return {
        "phase": 6,
        "ref": "1910.147(d)(6)",
        "title": "Verification of isolation",
        "objective": "Verify isolation and de-energization (e.g. pressure gauge reads zero / test point confirms no flow).",
        "verify_devices": verify_devices,
        "field_action_required": [] if has_verify else [
            "NO pressure gauge / indicator / test point was found on the P&ID near the isolated section. "
            "Field-verify zero energy by an approved method before starting work.",
        ],
    }


def _open_gaps(missing_evidence, relief_devices, verify_devices):
    gaps = []
    if not relief_devices:
        gaps.append("stored_energy_relief_unknown")
    if not verify_devices:
        gaps.append("verification_method_unknown")
    if missing_evidence:
        gaps.append("deterministic_missing_evidence")
    return gaps


def _group_by_source(devices):
    groups = {}
    for device in devices:
        key = device.get("source_component") or "unknown"
        groups.setdefault(key, []).append(device["uuid"])
    return groups


def _entity_text(candidate):
    props = candidate.get("properties") or {}
    return " ".join(
        str(candidate.get(k) or props.get(k) or "").lower()
        for k in ("entity_class", "candidate_label", "type", "entity_type", "valve_type", "category", "isolation_method", "reason")
    )


def _first(props, keys):
    for key in keys:
        value = props.get(key)
        if value not in (None, "", []):
            return str(value)
    return None


def _tag_prefix(value):
    result = []
    for char in str(value or "").strip().lower():
        if char.isalpha():
            result.append(char)
            continue
        break
    return "".join(result)
