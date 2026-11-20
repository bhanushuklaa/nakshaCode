from app.llm.client import parse_json
from app.llm.validate import auto_repair, find_problems
from app.schema import Edge, Graph, Node


def graph(nodes, edges):
    return Graph(title="t", nodes=nodes, edges=edges)


def test_valid_graph_has_no_problems():
    g = graph([Node(id="a", label="Start", type="start"), Node(id="b", label="Do it")],
              [Edge(source="a", target="b")])
    assert find_problems(g) == []


def test_detects_common_problems():
    g = graph([Node(id="a", label="Start", type="start"), Node(id="b", label="Check", type="decision"),
               Node(id="c", label="Lonely")],
              [Edge(source="a", target="b"), Edge(source="b", target="zzz")])
    problems = "\n".join(find_problems(g))
    assert "'zzz' does not exist" in problems
    assert "'c' is not connected" in problems
    assert "Decision 'b'" in problems


def test_auto_repair_drops_broken_edges():
    g = graph([Node(id="a", label="A", type="start"), Node(id="a", label="dup")],
              [Edge(source="a", target="missing")])
    fixed = auto_repair(g)
    assert len(fixed.nodes) == 1 and fixed.edges == []


def test_parse_json_tolerates_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": 2} hope this helps') == {"a": 2}
