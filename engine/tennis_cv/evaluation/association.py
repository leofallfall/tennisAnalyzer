"""One-to-one association of events and labels by connected components (M2.3, ADR-021).

For one radius ``r`` the bipartite graph has an edge between an event and a label when
their distance is at most ``r`` (plus a declared numeric tolerance). The outcome of every
member depends only on the shape of its connected component, never on the input order or
on a "nearest wins" choice:

* 1 event + 1 label: associated;
* 1 label, no event: label not associated (a miss, for target impacts);
* 1 event, no label: event not associated;
* anything with several events or several labels: **ambiguous**, nothing assigned.

Distance: an event with an instant ``t_e`` (representative wrist determined) is at
``|t_e - t_l|``; an event with only a window ``[a, b]`` is at 0 when ``a <= t_l <= b``,
otherwise at the distance from the nearer edge. The signed offset is ``event - label``
(for a window: towards the nearer edge, 0 inside).

The function has no state and receives one radius: callers run it from scratch for each
radius. Pure Python, no file access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

NUMERIC_TOLERANCE_S = 1e-9
"""Only numeric: an edge exists when ``distance <= r + NUMERIC_TOLERANCE_S``."""


@dataclass(frozen=True)
class EventNode:
    id: str
    start_s: float
    end_s: float
    """``start_s == end_s`` for an instant event."""


@dataclass(frozen=True)
class LabelNode:
    id: str
    """A stable key, e.g. the line of the label in labels.csv."""
    t_s: float


@dataclass(frozen=True)
class Edge:
    event_id: str
    label_id: str
    distance_s: float
    signed_s: float


class GroupShape(StrEnum):
    ASSOCIATED = "associated"
    LABEL_ONLY = "label_only"
    EVENT_ONLY = "event_only"
    AMBIGUOUS = "ambiguous"


class AmbiguityReason(StrEnum):
    SEVERAL_EVENTS = "several_events"
    SEVERAL_LABELS = "several_labels"


@dataclass(frozen=True)
class Group:
    id: str
    event_ids: tuple[str, ...]
    label_ids: tuple[str, ...]
    shape: GroupShape
    ambiguity: tuple[AmbiguityReason, ...]


@dataclass(frozen=True)
class Association:
    radius_s: float
    edges: tuple[Edge, ...]
    groups: tuple[Group, ...]

    def group_of_event(self, event_id: str) -> Group:
        return next(g for g in self.groups if event_id in g.event_ids)

    def group_of_label(self, label_id: str) -> Group:
        return next(g for g in self.groups if label_id in g.label_ids)


def signed_offset(event: EventNode, t_s: float) -> float:
    """``event - label``; 0 inside a window, else towards the nearer edge."""
    if event.start_s <= t_s <= event.end_s:
        return 0.0
    if t_s < event.start_s:
        return event.start_s - t_s
    return event.end_s - t_s


def distance(event: EventNode, t_s: float) -> float:
    return abs(signed_offset(event, t_s))


def find_edges(
    events: list[EventNode], labels: list[LabelNode], radius_s: float
) -> tuple[Edge, ...]:
    """Every event-label pair within ``radius_s``, sorted by (event id, label id)."""
    edges = [
        Edge(e.id, lb.id, distance(e, lb.t_s), signed_offset(e, lb.t_s))
        for e in events
        for lb in labels
        if distance(e, lb.t_s) <= radius_s + NUMERIC_TOLERANCE_S
    ]
    return tuple(sorted(edges, key=lambda x: (x.event_id, x.label_id)))


def groups_from_edges(
    events: list[EventNode], labels: list[LabelNode], edges: tuple[Edge, ...], prefix: str
) -> tuple[Group, ...]:
    """Connected components (union-find) and their shape, from the edges alone.

    Groups are ordered by the earliest time of a member, then by the smallest member key,
    and numbered ``<prefix>001``...: ids do not depend on the input order."""
    parent: dict[tuple[str, str], tuple[str, str]] = {}
    nodes = [("e", e.id) for e in events] + [("l", lb.id) for lb in labels]
    if len(set(nodes)) != len(nodes):
        raise ValueError("duplicate event or label id")
    for n in nodes:
        parent[n] = n

    def root(n: tuple[str, str]) -> tuple[str, str]:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for ed in edges:
        a, b = root(("e", ed.event_id)), root(("l", ed.label_id))
        if a != b:
            parent[max(a, b)] = min(a, b)
    members: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for n in nodes:
        members.setdefault(root(n), []).append(n)
    start = {("e", e.id): e.start_s for e in events} | {("l", lb.id): lb.t_s for lb in labels}

    def order(ms: list[tuple[str, str]]) -> tuple[float, tuple[str, str]]:
        return (min(start[m] for m in ms), min(ms))

    out: list[Group] = []
    for i, ms in enumerate(sorted(members.values(), key=order), start=1):
        ev = tuple(sorted(m[1] for m in ms if m[0] == "e"))
        lb = tuple(sorted(m[1] for m in ms if m[0] == "l"))
        reasons: list[AmbiguityReason] = []
        if len(ev) > 1:
            reasons.append(AmbiguityReason.SEVERAL_EVENTS)
        if len(lb) > 1:
            reasons.append(AmbiguityReason.SEVERAL_LABELS)
        if reasons:
            shape = GroupShape.AMBIGUOUS
        elif ev and lb:
            shape = GroupShape.ASSOCIATED
        elif lb:
            shape = GroupShape.LABEL_ONLY
        else:
            shape = GroupShape.EVENT_ONLY
        out.append(Group(f"{prefix}{i:03d}", ev, lb, shape, tuple(reasons)))
    return tuple(out)


def associate(
    events: list[EventNode], labels: list[LabelNode], radius_s: float, prefix: str = "g"
) -> Association:
    """Edges and connected components for one radius (no state, no order dependence)."""
    if not radius_s > 0:
        raise ValueError("radius must be > 0")
    edges = find_edges(events, labels, radius_s)
    return Association(radius_s, edges, groups_from_edges(events, labels, edges, prefix))
