import zipfile

import pytest

from app.pipeline.analyzer import analyze_repository
from app.pipeline.ingest import IngestError, extract_zip

LIMITS = {"max_files": 100, "max_uncompressed_bytes": 10**8, "max_file_bytes": 10**6}


def analyze(zip_path, tmp_path):
    ingested = extract_zip(zip_path, tmp_path / "repo", **LIMITS)
    result, _ = analyze_repository(ingested, "test", lambda message, progress: None)
    return result


def test_python_agent_workflow(zip_fixture, tmp_path):
    result = analyze(zip_fixture("refund_app"), tmp_path)
    refund = next(w for w in result.workflows if w.title == "POST /refunds")
    nodes = {n.label: n for n in refund.nodes}

    assert [n.label for n in refund.nodes if n.type in {"step", "agent"}] == [
        "Lookup order", "Investigate claim", "Parse verdict", "Ask for approval",
    ]
    assert nodes["Lookup order"].services == ["Postgres"]
    agent = nodes["Investigate claim"]
    assert agent.type == "agent" and agent.services == ["Claude"]
    tools = [n for n in refund.nodes if n.type == "tool"]
    assert {t.label for t in tools} == {"Read refund policy", "Check order history", "Read past tickets", "Track delivery"}
    assert next(t for t in tools if t.label == "Track delivery").services == ["HTTP request"]
    tool_ids = {t.id for t in tools}
    assert {e.target for e in refund.edges if e.kind == "tool"} == tool_ids
    assert all(e.source == agent.id for e in refund.edges if e.kind == "tool")


def test_file_graph(zip_fixture, tmp_path):
    result = analyze(zip_fixture("refund_app"), tmp_path)
    files = {f.path: f for f in result.files}
    assert files["app/main.py"].imports == ["app/agent.py", "app/db.py", "app/notify.py", "app/parsing.py"]
    assert "app/main.py" in files["app/db.py"].imported_by
    assert files["app/tools.py"].services  # Notion, Shopify, Zendesk, HTTP


def test_express_routes_and_require(zip_fixture, tmp_path):
    result = analyze(zip_fixture("express_app"), tmp_path)
    assert result.workflows[0].overview                    # the project overview comes first
    titles = {w.title for w in result.workflows if not w.overview}
    assert titles == {"POST /orders", "GET /orders/:id"}
    post = next(w for w in result.workflows if w.title == "POST /orders")
    assert [n.services for n in post.nodes[1:]] == [["Postgres"], ["Email (SMTP)"]]


def test_zip_slip_is_skipped(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../evil.py", "print('x')")
        z.writestr("ok.py", "print('ok')")
    result = extract_zip(archive, tmp_path / "out", **LIMITS)
    assert [f.path for f in result.files] == ["ok.py"]
    assert not (tmp_path / "evil.py").exists()


def test_invalid_zip(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    with pytest.raises(IngestError):
        extract_zip(bad, tmp_path / "out", **LIMITS)


def _edges(graph):
    labels = {n.id: n.label for n in graph.nodes}
    return {(labels[e.source], labels[e.target], e.kind) for e in graph.edges}


def test_python_branches_errors_loops_and_parallel(zip_fixture, tmp_path):
    result = analyze(zip_fixture("branch_app"), tmp_path)
    checkout = next(w for w in result.workflows if w.title == "POST /checkout")
    types = {n.label: n.type for n in checkout.nodes}
    edges = _edges(checkout)

    assert types["No order?"] == "decision"
    assert ("No order?", "Error 404", "yes") in edges
    assert ("No order?", "Charge", "no") in edges
    assert ("Charge", "Page on call", "error") in edges          # except PaymentError
    assert ("Page on call", "Error 402", "flow") in edges
    assert types["For each item"] == "loop"
    assert ("For each item", "Reserve stock", "flow") in edges
    assert ("Reserve stock", "For each item", "loop") in edges   # back-edge
    assert ("For each item", "Save payment", "flow") in edges    # "done" path
    assert ("Save payment", "Email customer", "flow") in edges   # asyncio.gather fan-out
    assert ("Save payment", "Post to slack", "flow") in edges
    services = {n.label: n.services for n in checkout.nodes}
    assert services["Email customer"] == ["Email (SMTP)"]        # stdlib smtplib still counts


def test_js_branches_and_foreach(zip_fixture, tmp_path):
    result = analyze(zip_fixture("branch_express"), tmp_path)
    orders = next(w for w in result.workflows if w.title == "POST /orders")
    edges = _edges(orders)
    assert ("No order?", "Return 400", "yes") in edges           # early return
    assert ("Charge card", "Refund card", "error") in edges      # catch block
    assert ("Refund card", "Raise error", "flow") in edges       # re-throw
    assert ("For each item", "Reserve", "flow") in edges         # items.forEach(...)


def test_helpers_without_branches_stay_linear(zip_fixture, tmp_path):
    """Branches that lead nowhere interesting must not add noise."""
    result = analyze(zip_fixture("refund_app"), tmp_path)
    refund = next(w for w in result.workflows if w.title == "POST /refunds")
    assert not any(n.type in {"decision", "loop", "end"} for n in refund.nodes)


def test_monorepo_tsconfig_aliases(zip_fixture, tmp_path):
    """Aliases from frontend/tsconfig.app.json and per-package "@/" defaults resolve."""
    result = analyze(zip_fixture("monorepo"), tmp_path)
    files = {f.path: f for f in result.files}
    assert files["frontend/src/pages/Orders.tsx"].imports == ["frontend/components/Button.tsx", "frontend/src/lib/api.ts"]
    assert files["admin/src/index.ts"].imports == ["admin/src/utils/fmt.ts"]


def test_project_overview_follows_imports_from_entry_points(zip_fixture, tmp_path):
    result = analyze(zip_fixture("refund_app"), tmp_path)
    overview = result.workflows[0]
    assert overview.overview and overview.title == "test overview"
    label = {n.id: n.label for n in overview.nodes}
    arrows = {(label[e.source], label[e.target]) for e in overview.edges}
    assert ("POST /refunds", "main.py") in arrows          # one route per feature: shown by name
    assert ("main.py", "agent.py") in arrows and ("agent.py", "tools.py") in arrows
    assert ("db.py", "Postgres") in arrows and ("agent.py", "Claude") in arrows
    assert {n.label for n in overview.nodes if n.type == "trigger"} == {"POST /refunds", "GET /health"}
    assert result.stats.workflows == 2                      # the overview is not counted


def test_overview_groups_routes_by_feature_and_router_prefix(zip_fixture, tmp_path):
    result = analyze(zip_fixture("feature_api"), tmp_path)
    titles = {w.title for w in result.workflows}
    assert "POST /training/rgb" in titles                      # APIRouter(prefix="/training")
    overview = result.workflows[0]
    entries = {n.label: n for n in overview.nodes if n.type == "trigger"}
    assert entries["Training API"].summary.startswith("3 routes")
    assert "Inspection API" in entries
    services = {n.label for n in overview.nodes if n.type == "integration"}
    assert {"Vector database", "ML model", "OpenAI", "SQL database"} <= services


def test_repeated_steps_are_merged(zip_fixture, tmp_path):
    result = analyze(zip_fixture("feature_api"), tmp_path)
    thermal = next(w for w in result.workflows if w.title == "POST /training/thermal")
    labels = [n.label for n in thermal.nodes]
    assert "Database session (x3)" in labels
    assert labels.count("Database session") == 2             # separated by the "if": kept apart
