from contextlib import AbstractContextManager

from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.traversal import T


class GraphClient(AbstractContextManager):
    def __init__(self, graph_config):
        self.graph_config = graph_config
        self.connection = None
        self.g = None

    def __enter__(self):
        self.connection = DriverRemoteConnection(
            self.graph_config.gremlin_url,
            self.graph_config.traversal_source,
        )
        self.g = traversal().withRemote(self.connection)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.connection:
            self.connection.close()
        return False


def normalize_vertex(vertex):
    normalized = {}
    for key, value in vertex.items():
        if key in [T.id, T.label]:
            normalized[str(key)] = value
        elif isinstance(value, list) and len(value) == 1:
            normalized[str(key)] = value[0]
        else:
            normalized[str(key)] = value
    return normalized


def vertex_id(vertex):
    for key in [T.id, "id", "T.id"]:
        if key in vertex:
            return vertex[key]
    return None


def vertex_label(vertex):
    for key in [T.label, "label", "T.label"]:
        if key in vertex:
            return vertex[key]
    return None


def props_only(vertex):
    excluded = {str(T.id), str(T.label), "id", "label", "T.id", "T.label"}
    return {key: value for key, value in vertex.items() if key not in excluded and value is not None}
