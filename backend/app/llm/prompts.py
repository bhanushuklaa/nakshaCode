"""Prompts and JSON schemas.

Qwen3-Coder has no built-in thinking mode, so every schema starts with an
`analysis` field: the model writes its reasoning first, then the answer.
That gives most of the benefit of "deep thinking" at a fraction of the cost.
"""

PROMPT_VERSION = "v3"  # bump to invalidate cached LLM results


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


STR = {"type": "string"}

# ----------------------------------------------------------- file summaries
SUMMARY_SYSTEM = (
    "You are a senior engineer documenting a codebase. For each file, write ONE "
    "plain-English sentence (max 20 words) saying what the file is responsible for. "
    "No filler like 'This file'. Start with a verb, e.g. 'Stores orders in Postgres.'"
)
SUMMARY_SCHEMA = _obj({
    "files": {"type": "array", "items": _obj({"path": STR, "summary": STR})},
})

# --------------------------------------------------------------- workflows
WORKFLOW_SYSTEM = (
    "You explain backend code as a visual workflow for people who do not read code.\n"
    "You receive a workflow extracted from real code: nodes (with type, function, file, "
    "services, code or docs) and edges [source, target, kind]. Edge kinds: flow, yes/no "
    "(decision outcomes), error (exception handler), loop (back to a loop), tool (agent tool).\n"
    "1. In `analysis`, think step by step (max 120 words): what happens end to end, "
    "including the important branches and failure paths?\n"
    "2. `title`: a short name for the whole workflow (3-6 words), e.g. 'Checkout and payment'.\n"
    "   `summary`: 1-2 plain sentences saying what this workflow does for the user.\n"
    "3. For EVERY node id given, return a `label` and a one-sentence `summary`:\n"
    "   - step / tool / integration: short imperative phrase (2-5 words), e.g. 'Lookup the order'.\n"
    "   - decision: a short yes/no question from the condition, e.g. 'not order' -> 'Order found?'. "
    "Keep the meaning of the yes edge: yes = the condition is true.\n"
    "   - loop: 'For each ...', e.g. 'For each cart item'.\n"
    "   - end: the outcome, e.g. 'Reject: order not found (404)'.\n"
    "Never invent nodes. Use exactly the ids you were given."
)
WORKFLOW_SCHEMA = _obj({
    "analysis": STR,
    "title": STR,
    "summary": STR,
    "steps": {"type": "array", "items": _obj({"id": STR, "label": STR, "summary": STR})},
})

# ----------------------------------------------------------- project overview
OVERVIEW_SYSTEM = (
    "You explain how a whole software project works, for people who do not read code.\n"
    "You receive an overview graph built from real code: entry points (trigger nodes: API "
    "routes, commands, app start), code boxes (a file or a folder, with its files and "
    "functions in `code_or_doc`) and services (databases, APIs, AI models). Edges mean "
    "'uses'.\n"
    "1. In `analysis`, think step by step (max 150 words): what is this project for, and "
    "how does a typical request travel from the entry points through the code to the services?\n"
    "2. `title`: what the project is, 2-5 words, e.g. 'Refund automation service'.\n"
    "3. `summary`: 2-3 plain sentences describing what the project does and how it is built.\n"
    "4. For EVERY node id, a `label` and a one-sentence `summary`. Code boxes get a role "
    "label of 2-4 words, e.g. 'Refund API', 'Order storage', 'AI review agent'. Keep "
    "service names as they are.\n"
    "Never invent nodes. Use exactly the ids you were given."
)

# -------------------------------------------------------------- single file
FILE_SYSTEM = (
    "You turn one source file into a clear workflow for people who do not read code.\n"
    "You receive the file name, its language, sometimes an outline of its functions, and "
    "the code with line numbers ('12| code').\n"
    "Rules:\n"
    "- Write `analysis` first (max 150 words): what the file does, its main path, the key "
    "decisions, loops and failure handling.\n"
    "- `summary`: 2-3 plain sentences saying what the file does and when it is used.\n"
    "- `title`: 3-6 words naming what the file does.\n"
    "- Pick `mode`: 'workflow' when the file mainly connects services or handles requests, "
    "'flowchart' when it is mostly logic with decisions.\n"
    "- Exactly one node of type 'trigger' (how the code starts: request, command, function "
    "call, event) or 'start'.\n"
    "- Node types: trigger, start, step, decision, loop, agent, tool, integration, end.\n"
    "- A 'decision' has outgoing edges with kind 'yes' and 'no'. A 'loop' has an edge "
    "labelled 'each' into its body, a 'loop' edge back from the body, and an edge labelled "
    "'done'. Use kind 'error' for failure paths.\n"
    "- `service` names an external system a node uses (e.g. 'Postgres', 'Stripe') or ''.\n"
    "- `line` is the line number where the step happens in the code, or 0.\n"
    "- Labels are short imperative phrases (2-5 words). Ids are short like 'n1', 'n2'.\n"
    "- Follow the real code; do not invent steps. Every node must be connected. Use 4 to 25 nodes."
)

# ------------------------------------------------------------- prompt mode
FLOWCHART_SYSTEM = (
    "You design clear workflows and flowcharts from a user's description.\n"
    "Pick `mode`: 'workflow' for automations that connect apps/services (like n8n), "
    "'flowchart' for processes with decisions.\n"
    "Rules:\n"
    "- Write `analysis` first (max 150 words): plan the steps, branches and services.\n"
    "- Exactly one node of type 'trigger' (workflow) or 'start' (flowchart).\n"
    "- Node types: trigger, start, step, decision, loop, agent, tool, integration, end.\n"
    "- A 'decision' has two or more outgoing edges with kind 'yes'/'no' (or kind 'flow' with a label).\n"
    "- A 'loop' node has an edge labelled 'each' into its body, a 'loop' edge from the body back "
    "to it, and an edge labelled 'done' to what comes after.\n"
    "- Use kind 'error' for what happens when a step fails.\n"
    "- Use kind 'tool' only for edges from an 'agent' to its 'tool' nodes.\n"
    "- `service` names the external app used by the node (e.g. 'Slack', 'Postgres') or ''.\n"
    "- Labels are short imperative phrases (2-5 words). Ids are short like 'n1', 'n2'.\n"
    "- Every node must be connected. Use 4 to 20 nodes."
)
NODE_TYPES = ["trigger", "start", "step", "decision", "loop", "agent", "tool", "integration", "end"]
EDGE_KINDS = ["flow", "tool", "yes", "no", "error", "loop"]
FLOWCHART_SCHEMA = _obj({
    "analysis": STR,
    "mode": {"type": "string", "enum": ["workflow", "flowchart"]},
    "title": STR,
    "nodes": {"type": "array", "items": _obj({
        "id": STR,
        "label": STR,
        "type": {"type": "string", "enum": NODE_TYPES},
        "service": STR,
    })},
    "edges": {"type": "array", "items": _obj({
        "source": STR,
        "target": STR,
        "kind": {"type": "string", "enum": EDGE_KINDS},
        "label": STR,
    })},
})

FIX_INSTRUCTION = (
    "Your JSON has these problems:\n{problems}\n"
    "Return the complete corrected JSON object. Keep everything else the same."
)

FILE_SCHEMA = _obj({
    "analysis": STR,
    "summary": STR,
    "mode": {"type": "string", "enum": ["workflow", "flowchart"]},
    "title": STR,
    "nodes": {"type": "array", "items": _obj({
        "id": STR,
        "label": STR,
        "type": {"type": "string", "enum": NODE_TYPES},
        "service": STR,
        "line": {"type": "integer"},
    })},
    "edges": FLOWCHART_SCHEMA["properties"]["edges"],
})
