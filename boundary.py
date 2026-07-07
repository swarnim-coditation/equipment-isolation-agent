from gremlin_python.process.graph_traversal import __

from graph_client import GraphClient, normalize_vertex, props_only, vertex_id, vertex_label


def fetch_boundaries(config):
    with GraphClient(config.graph) as client:
        g = client.g
        equipment_rows = _fetch_equipment_vertices(g, config.equipment_tag)
        equipment_vertices = _dedupe_vertices(normalize_vertex(row) for row in equipment_rows)
        equipment_results = []
        traversal_limit_hit = False

        for equipment in equipment_vertices:
            equipment_id = vertex_id(equipment)
            component_rows = (
                g.V(equipment_id)
                .out("PHYSICALLY_HAS_A")
                .hasLabel("Component")
                .valueMap(True)
                .toList()
            )
            components = [normalize_vertex(row) for row in component_rows]
            edge_labels = g.V(equipment_id).bothE().label().dedup().toList()
            component_boundaries = []

            for component in components:
                boundary, hit_limit = _component_boundary(g, component, config.policy)
                traversal_limit_hit = traversal_limit_hit or hit_limit
                component_boundaries.append(boundary)

            equipment_results.append(
                {
                    "equipment": {
                        "id": equipment_id,
                        "label": vertex_label(equipment),
                        "properties": props_only(equipment),
                    },
                    "edge_labels": edge_labels,
                    "components": [
                        {
                            "id": vertex_id(component),
                            "label": vertex_label(component),
                            "properties": props_only(component),
                        }
                        for component in components
                    ],
                    "component_boundaries": component_boundaries,
                }
            )

    return {
        "error": False,
        "target_mode": "selected_equipment",
        "requested_equipment_tags": [config.equipment_tag],
        "matched_equipment_count": len(equipment_results),
        "max_traversal_depth": config.policy.max_traversal_depth,
        "traversal_limit_hit": traversal_limit_hit,
        "equipment_boundaries": equipment_results,
        "context": config.context,
    }


def _fetch_equipment_vertices(g, equipment_tag):
    rows = (
        g.V()
        .hasLabel("Equipment")
        .or_(
            __.has("tag", equipment_tag),
            __.has("tag_number", equipment_tag),
            __.has("Equipment Name", equipment_tag),
            __.has("name", equipment_tag),
        )
        .valueMap(True)
        .toList()
    )
    if rows:
        return rows

    requested_key = _tag_key(equipment_tag)
    if not requested_key:
        return rows
    fallback_rows = g.V().hasLabel("Equipment").valueMap(True).toList()
    return [
        row
        for row in fallback_rows
        if requested_key
        in {
            _tag_key(_raw_property(row, "tag")),
            _tag_key(_raw_property(row, "tag_number")),
            _tag_key(_raw_property(row, "Equipment Name")),
            _tag_key(_raw_property(row, "name")),
            _tag_key(_raw_property(row, "equipment_number")),
        }
    ]


def _raw_property(row, key):
    value = row.get(key)
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _tag_key(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _dedupe_vertices(vertices):
    seen = set()
    result = []
    for vertex in vertices:
        key = vertex_id(vertex)
        if key in seen:
            continue
        seen.add(key)
        result.append(vertex)
    return result


def _component_boundary(g, component, policy):
    component_id = vertex_id(component)
    component_edge_labels = g.V(component_id).bothE().label().dedup().toList()
    component_neighbors = (
        g.V(component_id)
        .both(*policy.candidate_edge_labels)
        .dedup()
        .limit(policy.traversal_limit_per_depth)
        .valueMap(True)
        .toList()
    )
    component_neighbors = [normalize_vertex(row) for row in component_neighbors]

    traversal_by_id = {}
    limit_hit = False
    for depth in range(1, policy.max_traversal_depth + 1):
        rows = (
            g.V(component_id)
            .repeat(__.both(*policy.candidate_edge_labels).simplePath())
            .times(depth)
            .dedup()
            .limit(policy.traversal_limit_per_depth)
            .valueMap(True)
            .toList()
        )
        limit_hit = limit_hit or len(rows) >= policy.traversal_limit_per_depth
        for row in rows:
            vertex = normalize_vertex(row)
            key = vertex_id(vertex)
            if key in traversal_by_id:
                continue
            traversal_by_id[key] = {
                "id": key,
                "label": vertex_label(vertex),
                "properties": props_only(vertex),
                "traversal_depth": depth,
            }

    return (
        {
            "component": {
                "id": component_id,
                "label": vertex_label(component),
                "properties": props_only(component),
            },
            "edge_labels": component_edge_labels,
            "direct_neighbors": [
                {
                    "id": vertex_id(vertex),
                    "label": vertex_label(vertex),
                    "properties": props_only(vertex),
                    "traversal_depth": 1,
                }
                for vertex in component_neighbors
            ],
            "traversal_sample": list(traversal_by_id.values()),
        },
        limit_hit,
    )
