"""One workflow for the whole project: entry points -> code -> services."""

from collections import Counter, defaultdict
from pathlib import PurePosixPath

from ..schema import Edge, FileEntry, Graph, Node
from .integrations import find_by_name
from .triggers import ENTRY_FILES, TEST_FILE

MAX_BOXES = 12
MAX_FEATURES = 8
SKIP_SEGMENTS = {"api", "apis", "rest", "v1", "v2", "v3", "v4"}
APP_ENTRY_FILES = ENTRY_FILES | {"main.tsx", "main.jsx", "index.tsx", "index.jsx"}
ENTRY_GROUPS = {  # trigger icon -> (name when there are several, plural noun)
    "webhook": ("HTTP API", "routes"),
    "schedule": ("Scheduled jobs", "jobs"),
    "queue": ("Background workers", "workers"),
    "terminal": ("Command line", "commands"),
    "event": ("Event handlers", "handlers"),
    "cloud": ("Serverless functions", "functions"),
    "play": ("Program start", "entry points"),
}


def _api_features(labels: list[str]) -> dict[str, str]:
    """Group HTTP routes by feature: "POST /api/v1/training/rgb" -> "Training API".

    The first path segment that is not "api" or a version names the feature.
    Small features beyond MAX_FEATURES are merged into "Other API routes".
    """
    def feature(label: str) -> str:
        path = label.split(" ", 1)[-1]
        parts = [p for p in path.strip("/").split("/") if p and p.lower() not in SKIP_SEGMENTS and not p.startswith(("{", ":", "<"))]
        if not parts:
            return "Main API"
        return parts[0].replace("-", " ").replace("_", " ").title() + " API"

    by_label = {label: feature(label) for label in labels}
    counts = Counter(by_label.values())
    keep = {name for name, _ in counts.most_common(MAX_FEATURES)}
    if len(counts) > MAX_FEATURES:
        keep = {name for name, _ in counts.most_common(MAX_FEATURES - 1)}
    return {label: name if name in keep else "Other API routes" for label, name in by_label.items()}


def _module_key(path: str, depth: int) -> str:
    """Folder prefix of `depth` levels; files at the repo root are their own box."""
    parts = PurePosixPath(path).parent.parts
    return "/".join(parts[:depth]) if parts else path


def _grouper(paths: set[str]):
    """One box per file for small projects, otherwise one per folder."""
    if len(paths) <= MAX_BOXES:
        return lambda p: p
    deepest = max(len(PurePosixPath(p).parent.parts) for p in paths)
    for depth in range(max(deepest, 1), 0, -1):
        if len({_module_key(p, depth) for p in paths}) <= MAX_BOXES:
            return lambda p, d=depth: _module_key(p, d)
    return lambda p: _module_key(p, 1)


def _labels(keys: set[str]) -> dict[str, str]:
    """Last path part ("pipeline", "main.py"); two parts when names clash."""
    last = {k: k.split("/")[-1] for k in keys}
    clashes = {name for name, n in Counter(last.values()).items() if n > 1}
    return {k: "/".join(k.split("/")[-2:]) if last[k] in clashes else last[k] for k in keys}


def build_overview(project: str, workflows: list[Graph], files: list[FileEntry]) -> Graph | None:
    by_path = {f.path: f for f in files if f.parsed and not TEST_FILE.search(f.path)}
    if not by_path:
        return None

    # 1. Entry points: workflow triggers, grouped by kind.
    entries: dict[str, dict] = {}
    triggers = [w.nodes[0] for w in workflows if w.nodes[0].type == "trigger" and w.nodes[0].file in by_path]
    features = _api_features([t.label for t in triggers if t.icon == "webhook"])
    for trigger in triggers:
        key = f"webhook:{features[trigger.label]}" if trigger.icon == "webhook" else trigger.icon
        group = entries.setdefault(key, {"labels": [], "files": set(), "line": trigger.line, "file": trigger.file,
                                         "icon": trigger.icon})
        group["labels"].append(trigger.label)
        group["files"].add(trigger.file)
    # App entry files nothing imports (e.g. frontend/src/main.tsx) are entry points too.
    covered = {f for g in entries.values() for f in g["files"]}
    for path, entry in by_path.items():
        if PurePosixPath(path).name in APP_ENTRY_FILES and not entry.imported_by and path not in covered:
            entries[f"file:{path}"] = {"labels": [f"Start {PurePosixPath(path).name}"], "files": {path},
                                       "line": 1, "file": path, "icon": "play"}

    # 2. Code reachable from the entry points (all code when nothing was found).
    roots = {f for g in entries.values() for f in g["files"]}
    reachable, stack = set(), list(roots)
    while stack:
        path = stack.pop()
        if path in reachable or path not in by_path:
            continue
        reachable.add(path)
        stack.extend(by_path[path].imports)
    if len(reachable) <= 1 and not entries:
        reachable = set(by_path)

    group_of = _grouper(reachable)
    labels = _labels({group_of(p) for p in reachable})
    nodes: dict[str, Node] = {}
    edges: set[tuple[str, str]] = set()
    members: dict[str, list[str]] = defaultdict(list)

    for path in sorted(reachable):
        box = f"code:{group_of(path)}"
        members[box].append(path)
        nodes.setdefault(box, Node(id=box, label=labels[group_of(path)], icon="code"))
        for target in by_path[path].imports:
            if target in reachable and f"code:{group_of(target)}" != box:
                edges.add((box, f"code:{group_of(target)}"))
        for service in by_path[path].services:
            integration = find_by_name(service)
            service_id = f"service:{service}"
            nodes.setdefault(service_id, Node(id=service_id, label=service, type="integration",
                                              icon=integration.category if integration else "cloud",
                                              services=[service]))
            edges.add((box, service_id))
            if integration and integration.category == "llm":
                nodes[box].icon = "agent"  # this code calls an AI model

    for box, paths in members.items():
        paths.sort(key=lambda p: (-len(by_path[p].imported_by), p))
        nodes[box].file = paths[0]
        nodes[box].summary = _describe_files(paths, by_path)

    for key, group in entries.items():
        unique = list(dict.fromkeys(group["labels"]))
        icon = group["icon"]
        name, noun = ENTRY_GROUPS.get(icon, ("Entry points", "entry points"))
        if key.startswith("webhook:"):
            name = key.removeprefix("webhook:")  # the API feature, e.g. "Training API"
        entry_id = f"entry:{key}"
        nodes[entry_id] = Node(
            id=entry_id, label=unique[0] if len(unique) == 1 else name, type="trigger", icon=icon,
            file=group["file"], line=group["line"],
            summary=(f"{len(unique)} {noun}: " if len(unique) > 1 else "") + ", ".join(unique[:12]),
        )
        for path in group["files"]:
            edges.add((entry_id, f"code:{group_of(path)}"))

    return _finish(project, nodes, _forward_only(edges, [n for n in nodes if n.startswith("entry:")]))


def _forward_only(edges: set[tuple[str, str]], starts: list[str]) -> set[tuple[str, str]]:
    """Drop arrows that point back towards the entry.

    Shared code (config, schema, types) is imported from everywhere, which
    draws arrows back up the chart. Keeping only arrows that go to the same or
    a later step (by distance from the entry points) leaves a clean left-to-right flow.
    """
    following: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        following[source].append(target)
    distance = {s: 0 for s in starts}
    queue = list(starts)
    for current in queue:  # breadth-first: queue grows while we walk it
        for target in sorted(following[current]):
            if target not in distance:
                distance[target] = distance[current] + 1
                queue.append(target)
    far = len(distance) + 1
    kept = {(s, t) for s, t in edges if distance.get(s, far) < distance.get(t, far)}
    # Same-level pairs: keep one direction only.
    for s, t in sorted(edges):
        if distance.get(s, far) == distance.get(t, far) and (t, s) not in kept:
            kept.add((s, t))
    return kept


def _describe_files(paths: list[str], by_path: dict[str, FileEntry]) -> str:
    """Code-derived summary of a box, shown until (and sent as input for) the AI summary."""
    if len(paths) == 1:
        entry = by_path[paths[0]]
        return entry.summary or ("Functions: " + ", ".join(entry.functions[:6]) if entry.functions else paths[0])
    shown = []
    for path in paths[:6]:
        functions = ", ".join(by_path[path].functions[:3])
        name = PurePosixPath(path).name
        shown.append(f"{name} ({functions})" if functions else name)
    more = f" and {len(paths) - 6} more" if len(paths) > 6 else ""
    return f"{len(paths)} files: " + "; ".join(shown) + more


def _finish(project: str, nodes: dict[str, Node], edges: set[tuple[str, str]]) -> Graph:
    """Entries first, services last, with short ids ("o1") the model can echo back."""
    rank = {"trigger": 0, "integration": 2}
    ordered = sorted(nodes.values(), key=lambda n: rank.get(n.type, 1))
    new_id = {node.id: f"o{i}" for i, node in enumerate(ordered, start=1)}
    for node in ordered:
        node.id = new_id[node.id]
    return Graph(mode="workflow", title=f"{project} overview", overview=True, nodes=ordered,
                 edges=[Edge(source=new_id[s], target=new_id[t]) for s, t in sorted(edges)])
