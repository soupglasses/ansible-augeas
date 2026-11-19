# Copyright (c) 2026, Sofie Finnes <sofie+git@finnes.dev>
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)

"""Order the children of one Augeas node using sibling constraints."""

from bisect import bisect_left
from heapq import heappop, heappush


def _plan(size, relations):
    sources = set()
    participants = set()
    edges = set()
    for source, anchors, after in relations:
        for anchor in anchors:
            if anchor == source:
                continue
            sources.add(source)
            participants.update((source, anchor))
            edges.add((anchor, source) if after else (source, anchor))

    original = list(range(size))
    if all(a < b for a, b in edges):
        return original

    if len(relations) == 1:
        source, anchors, after = relations[0]
        target = (max if after else min)(a for a in anchors if a != source)
        original.remove(source)
        original.insert(original.index(target) + int(after), source)
        return original

    # Siblings outside all declarations retain relative order. Also retain the
    # order of anchors that are never sources whenever the constraints allow it.
    outsiders = [node for node in original if node not in participants]
    anchors = sorted(participants - sources)
    required = edges.union(zip(outsiders, outsiders[1:]))
    preferred = required.union(zip(anchors, anchors[1:]))
    for constraints in (preferred, required):
        order = _sort(size, participants, constraints)
        if order is not None:
            return order
    raise ValueError("conflicting sibling order constraints")


def _sort(size, participants, constraints):
    outgoing = [[] for _ in range(size)]
    indegree = [0] * size
    for a, b in constraints:
        outgoing[a].append(b)
        indegree[b] += 1

    # Reuse participant slots where possible, so ordering repeated records
    # does not displace unrelated directives interleaved among them.
    ready = ([], [])
    for node in range(size):
        if not indegree[node]:
            heappush(ready[node in participants], node)
    order = []
    while any(ready):
        desired = len(order) in participants
        node = heappop(ready[desired] or ready[not desired])
        order.append(node)
        for successor in outgoing[node]:
            indegree[successor] -= 1
            if not indegree[successor]:
                heappush(ready[successor in participants], successor)
    return order if len(order) == size else None


def _increasing(nodes):
    """Return a longest subsequence already in its original relative order."""
    tails = []
    previous = {}
    for node in nodes:
        pos = bisect_left(tails, node)
        if pos:
            previous[node] = tails[pos - 1]
        if pos == len(tails):
            tails.append(node)
        else:
            tails[pos] = node
    keep = set()
    if tails:
        node = tails[-1]
        while node is not None:
            keep.add(node)
            node = previous.get(node)
    return keep


def apply_constraints(aug, parent, siblings, relations):
    """Apply resolved constraints and return the original paths in final order."""
    index = {node: i for i, node in enumerate(siblings)}
    try:
        resolved = list(
            dict.fromkeys(
                (index[source], tuple(index[node] for node in anchors), after)
                for source, anchors, after in relations
            )
        )
    except KeyError as error:
        raise ValueError(f"anchor must select siblings of path; got {error.args[0]!r}") from error
    order = _plan(len(siblings), resolved)
    current = list(range(len(siblings)))
    if current == order:
        return siblings

    # Keep stationary siblings in the tree. Within each gap, keep as many
    # additional already-ordered nodes as possible to avoid redundant moves.
    sources = {source for source, anchors, _after in resolved if any(a != source for a in anchors)}
    stationary = [node for node in order if node not in sources]
    if stationary == sorted(stationary):
        fixed = set(stationary)
    else:
        anchors = {node for _source, nodes, _after in resolved for node in nodes}
        fixed = set(stationary) - anchors
    keep = fixed.copy()
    lower = start = -1
    for end, upper in enumerate([*order, len(order)]):
        if upper in fixed or end == len(order):
            keep.update(_increasing(n for n in order[start + 1 : end] if lower < n < upper))
            lower, start = upper, end

    for pos in reversed(range(len(order))):
        node = order[pos]
        if node in keep:
            continue
        before = pos + 1 < len(order)
        anchor = order[pos + 1] if before else current[-1]
        if node == anchor:
            continue
        paths = aug.match(parent + "/*")
        source_path = paths[current.index(node)]
        # Variables preserve node identity across insertion of a same-label
        # sibling, which may immediately renumber the source and anchor paths.
        aug.defvar("placement_source", source_path)
        aug.defvar("placement_anchor", paths[current.index(anchor)])
        aug.insert("$placement_anchor", aug.label(source_path), before)
        axis = "preceding-sibling" if before else "following-sibling"
        aug.move("$placement_source", f"$placement_anchor/{axis}::*[1]")
        current.remove(node)
        current.insert(current.index(anchor) + int(not before), node)
    return [siblings[i] for i in order]
