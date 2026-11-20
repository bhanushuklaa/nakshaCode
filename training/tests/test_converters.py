import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import clean_graph, graph_to_target, prompts  # noqa: E402
from convert_mermaid import extract as extract_mermaid  # noqa: E402
from convert_mermaid import mermaid_to_graph
from convert_n8n import extract as extract_n8n  # noqa: E402
from convert_n8n import n8n_to_graph

N8N_WORKFLOW = {
    "name": "Refund triage",
    "nodes": [
        {"name": "Refund webhook", "type": "n8n-nodes-base.webhook"},
        {"name": "Look up order", "type": "n8n-nodes-base.postgres"},
        {"name": "Order exists?", "type": "n8n-nodes-base.if"},
        {"name": "Investigate claim", "type": "@n8n/n8n-nodes-langchain.agent"},
        {"name": "OpenAI Chat Model", "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi"},
        {"name": "Read policy", "type": "n8n-nodes-base.notionTool"},
        {"name": "Ask approval", "type": "n8n-nodes-base.slack", "onError": "continueErrorOutput"},
        {"name": "Email support", "type": "n8n-nodes-base.gmail"},
        {"name": "Reply not found", "type": "n8n-nodes-base.respondToWebhook"},
        {"name": "Note", "type": "n8n-nodes-base.stickyNote"},
    ],
    "connections": {
        "Refund webhook": {"main": [[{"node": "Look up order", "type": "main", "index": 0}]]},
        "Look up order": {"main": [[{"node": "Order exists?", "type": "main", "index": 0}]]},
        "Order exists?": {"main": [[{"node": "Investigate claim"}], [{"node": "Reply not found"}]]},
        "Investigate claim": {"main": [[{"node": "Ask approval"}]]},
        "OpenAI Chat Model": {"ai_languageModel": [[{"node": "Investigate claim", "type": "ai_languageModel"}]]},
        "Read policy": {"ai_tool": [[{"node": "Investigate claim", "type": "ai_tool"}]]},
        "Ask approval": {"main": [[], [{"node": "Email support"}]]},
    },
}

MERMAID = """```mermaid
flowchart TD
    A([Start]) --> B{Is the form valid?}
    B -- Yes --> C[Save to Postgres]
    B -->|No| D["Show error & retry"]
    C & D --> E((End))
```"""


def _edges(graph):
    labels = {n.id: n.label for n in graph.nodes}
    return {(labels[e.source], labels[e.target], e.kind) for e in graph.edges}


def test_n8n_conversion_maps_types_services_and_branches():
    graph = clean_graph(n8n_to_graph(N8N_WORKFLOW))
    assert graph is not None
    types = {n.label: n.type for n in graph.nodes}
    services = {n.label: n.services for n in graph.nodes}
    edges = _edges(graph)

    assert "Note" not in types and "OpenAI Chat Model" not in types   # sticky note + model sub-node removed
    assert types["Refund webhook"] == "trigger"
    assert types["Order exists?"] == "decision"
    assert types["Investigate claim"] == "agent" and services["Investigate claim"] == ["OpenAI"]
    assert types["Read policy"] == "tool" and services["Read policy"] == ["Notion"]
    assert types["Reply not found"] == "end"
    assert services["Look up order"] == ["Postgres"]
    assert ("Order exists?", "Investigate claim", "yes") in edges
    assert ("Order exists?", "Reply not found", "no") in edges
    assert ("Investigate claim", "Read policy", "tool") in edges
    assert ("Ask approval", "Email support", "error") in edges         # continueErrorOutput
    assert [n.id for n in graph.nodes] == [f"n{i}" for i in range(1, len(graph.nodes) + 1)]


def test_n8n_extract_finds_prompt_and_workflow_string():
    record = {"instruction": "Triage refund requests", "output": "```json\n" + json.dumps(N8N_WORKFLOW) + "\n```"}
    prompt, workflow = extract_n8n(record, None, None)
    assert prompt == "Triage refund requests" and workflow["name"] == "Refund triage"


def test_n8n_loop_back_edges():
    wf = {"nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"},
                    {"name": "Loop", "type": "n8n-nodes-base.splitInBatches"},
                    {"name": "Send", "type": "n8n-nodes-base.slack"},
                    {"name": "Finish", "type": "n8n-nodes-base.noOp"}],
          "connections": {"Start": {"main": [[{"node": "Loop"}]]},
                          "Loop": {"main": [[{"node": "Finish"}], [{"node": "Send"}]]},
                          "Send": {"main": [[{"node": "Loop"}]]}}}
    edges = _edges(clean_graph(n8n_to_graph(wf)))
    assert ("Send", "Loop", "loop") in edges and ("Loop", "Finish", "flow") in edges


def test_mermaid_conversion():
    graph = clean_graph(mermaid_to_graph(MERMAID, title="Form"))
    assert graph is not None
    types = {n.label: n.type for n in graph.nodes}
    edges = _edges(graph)
    assert types == {"Start": "start", "Is the form valid?": "decision", "Save to Postgres": "step",
                     "Show error & retry": "step", "End": "end"}
    assert ("Is the form valid?", "Save to Postgres", "yes") in edges
    assert ("Is the form valid?", "Show error & retry", "no") in edges
    assert ("Show error & retry", "End", "flow") in edges              # "C & D --> E"
    assert next(n for n in graph.nodes if n.label == "Save to Postgres").services == ["Postgres"]


def test_mermaid_extract_and_rejects_non_flowcharts():
    assert extract_mermaid({"description": "A form", "mermaid": MERMAID}, None, None)[0] == "A form"
    assert mermaid_to_graph("sequenceDiagram\n A->>B: hi") is None


def test_target_matches_backend_schema():
    graph = clean_graph(mermaid_to_graph(MERMAID))
    target = graph_to_target(graph)
    assert set(target) == set(prompts.FLOWCHART_SCHEMA["properties"])
    node_keys = set(prompts.FLOWCHART_SCHEMA["properties"]["nodes"]["items"]["properties"])
    assert all(set(n) == node_keys for n in target["nodes"])
