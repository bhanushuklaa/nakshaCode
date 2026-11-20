"""The single graph schema shared by the pipeline, the LLM, the API and the UI.

Every view in the frontend (workflow, flowchart, file graph, map) renders
data shaped by these models, so change them with care.
"""

from typing import Literal

from pydantic import BaseModel, Field

NodeType = Literal[
    "trigger", "step", "agent", "tool", "decision", "loop", "integration", "start", "end"
]
# flow = normal next step, yes/no = decision outcomes, tool = agent -> tool,
# error = exception handler path, loop = back-edge from a loop body to its loop node
EdgeKind = Literal["flow", "tool", "yes", "no", "error", "loop"]
GraphMode = Literal["workflow", "flowchart"]


class Node(BaseModel):
    id: str
    label: str
    type: NodeType = "step"
    icon: str = "code"  # category name; the UI maps it to an icon
    summary: str = ""
    file: str | None = None
    line: int | None = None
    function: str | None = None
    services: list[str] = Field(default_factory=list)


class Edge(BaseModel):
    source: str
    target: str
    kind: EdgeKind = "flow"
    label: str = ""


class Graph(BaseModel):
    mode: GraphMode = "workflow"
    title: str
    analysis: str = ""  # the model's written reasoning ("thinking") for this graph
    summary: str = ""   # 1-3 sentence plain-English summary (set by the AI)
    overview: bool = False  # True for the whole-project overview workflow
    nodes: list[Node]
    edges: list[Edge]
    warnings: list[str] = Field(default_factory=list)


class FileEntry(BaseModel):
    path: str
    language: str
    lines: int
    size: int
    parsed: bool
    summary: str = ""
    functions: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)  # repo files this file imports
    imported_by: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)  # external packages
    services: list[str] = Field(default_factory=list)  # detected integrations


class Stats(BaseModel):
    files_total: int
    files_parsed: int
    lines_total: int
    languages: dict[str, int]
    skipped: int
    workflows: int


class AnalysisResult(BaseModel):
    project: str
    summary: str = ""  # AI summary of the whole project (from the overview)
    stats: Stats
    files: list[FileEntry]
    workflows: list[Graph]
    warnings: list[str] = Field(default_factory=list)
    llm_used: bool = False


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=5, max_length=4000)


class ExplainRequest(BaseModel):
    """Ask the AI to name the steps of one workflow (sent back by the UI on demand)."""
    graph: Graph
    file_summaries: dict[str, str] = Field(default_factory=dict)
