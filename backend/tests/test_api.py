import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LLM_ENABLED", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def wait_for(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"done", "error"}:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_analyze_without_llm(client, zip_fixture):
    with open(zip_fixture("refund_app"), "rb") as fh:
        response = client.post("/api/analyze", files={"file": ("refund_app.zip", fh, "application/zip")})
    assert response.status_code == 200
    job = wait_for(client, response.json()["job_id"])
    assert job["status"] == "done"
    result = job["result"]
    assert result["project"] == "refund_app"
    assert result["stats"]["workflows"] == 2
    assert result["llm_used"] is False


def test_rejects_non_zip(client):
    response = client.post("/api/analyze", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert response.status_code == 400


def test_prompt_needs_llm(client):
    response = client.post("/api/prompt", json={"prompt": "When a form is submitted, email the team"})
    assert response.status_code == 503


def test_prompt_mode_with_fake_model(client, monkeypatch):
    reply = {
        "analysis": "Form → check → email.", "mode": "flowchart", "title": "Form handling",
        "nodes": [
            {"id": "n1", "label": "Form submitted", "type": "start", "service": ""},
            {"id": "n2", "label": "Is it spam?", "type": "decision", "service": ""},
            {"id": "n3", "label": "Email the team", "type": "step", "service": "SendGrid"},
            {"id": "n4", "label": "Discard", "type": "end", "service": ""},
        ],
        "edges": [
            {"source": "n1", "target": "n2", "kind": "flow", "label": ""},
            {"source": "n2", "target": "n3", "kind": "no", "label": ""},
            {"source": "n2", "target": "n4", "kind": "yes", "label": ""},
        ],
    }
    llm = client.app.state.llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", lambda *args, **kwargs: reply)
    response = client.post("/api/prompt", json={"prompt": "When a form is submitted, email the team"})
    job = wait_for(client, response.json()["job_id"])
    assert job["status"] == "done"
    assert job["result"]["title"] == "Form handling"
    assert job["result"]["nodes"][2]["icon"] == "email"


def test_analyze_explains_overview_and_workflows_on_demand(client, zip_fixture, monkeypatch):
    import json
    calls = []

    def fake_chat(messages, schema, name, max_tokens=None):
        calls.append(name)
        if name == "file_summaries":
            files = json.loads(messages[-1]["content"])
            return {"files": [{"path": f["path"], "summary": "Does things."} for f in files]}
        payload = json.loads(messages[-1]["content"])
        if payload["title"].endswith("overview"):
            return {"analysis": "a", "title": "Refund service", "summary": "Reviews refunds.",
                    "steps": [{"id": "o2", "label": "Refund agent", "summary": "Decides."}]}
        return {"analysis": "Checks refunds.", "title": "Refund review", "summary": "Reviews one refund.",
                "steps": [{"id": "n1", "label": "Lookup the order", "summary": "Reads the order."},
                          {"id": "bogus", "label": "Invented", "summary": ""}]}

    llm = client.app.state.llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_chat)
    with open(zip_fixture("refund_app"), "rb") as fh:
        response = client.post("/api/analyze", files={"file": ("refund_app.zip", fh, "application/zip")})
    result = wait_for(client, response.json()["job_id"])["result"]
    overview = result["workflows"][0]
    assert overview["overview"] and overview["title"] == "Refund service (overview)"
    assert result["summary"] == "Reviews refunds."
    assert calls.count("workflow_labels") == 1             # only the overview, not every workflow

    refund = next(w for w in result["workflows"] if not w["overview"] and w["nodes"][0]["label"] == "POST /refunds")
    assert refund["title"] == "POST /refunds" and refund["analysis"] == ""
    job_id = client.post("/api/explain", json={"graph": refund, "file_summaries": {}}).json()["job_id"]
    explained = wait_for(client, job_id)["result"]
    assert explained["title"] == "Refund review" and explained["summary"] == "Reviews one refund."
    assert explained["nodes"][1]["label"] == "Lookup the order"
    assert all(n["label"] != "Invented" for n in explained["nodes"])

    # Uploading the same code again reuses the cached names without asking the model.
    with open(zip_fixture("refund_app"), "rb") as fh:
        again = wait_for(client, client.post("/api/analyze", files={"file": ("refund_app.zip", fh, "application/zip")}).json()["job_id"])
    titles = {w["title"] for w in again["result"]["workflows"]}
    assert "Refund review" in titles and calls.count("workflow_labels") == 2


def test_explain_failure_is_a_clear_error(client, zip_fixture, monkeypatch):
    from app.llm.client import LLMError

    def broken(*args, **kwargs):
        raise LLMError("Model returned invalid JSON")

    llm = client.app.state.llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", broken)
    graph = {"title": "POST /x", "nodes": [{"id": "trigger", "label": "POST /x", "type": "trigger"},
                                           {"id": "n1", "label": "Save", "type": "step"}],
             "edges": [{"source": "trigger", "target": "n1"}]}
    job = wait_for(client, client.post("/api/explain", json={"graph": graph}).json()["job_id"])
    assert job["status"] == "error" and "could not explain this workflow" in job["error"]


def test_single_file_mode_any_language(client, monkeypatch):
    seen = {}
    reply = {"analysis": "a", "summary": "Serves orders over HTTP.", "mode": "workflow", "title": "Order handler",
             "nodes": [{"id": "n1", "label": "Receive request", "type": "trigger", "service": "", "line": 3},
                       {"id": "n2", "label": "Save order", "type": "step", "service": "Postgres", "line": 5}],
             "edges": [{"source": "n1", "target": "n2", "kind": "flow", "label": ""}]}

    def fake_chat(messages, schema, name, max_tokens=None):
        seen["prompt"] = messages[-1]["content"]
        return reply

    llm = client.app.state.llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", fake_chat)
    code = b"package main\n\nfunc handle(w http.ResponseWriter) {\n  // ...\n  db.Save(order)\n}\n"
    job = wait_for(client, client.post("/api/file", files={"file": ("orders.go", code, "text/plain")}).json()["job_id"])
    graph = job["result"]
    assert graph["summary"] == "Serves orders over HTTP." and graph["title"] == "Order handler"
    assert graph["nodes"][1]["line"] == 5 and graph["nodes"][1]["file"] == "orders.go"
    assert "Language: go" in seen["prompt"] and "5|   db.Save(order)" in seen["prompt"]

    py = b"def checkout(cart):\n    total = price(cart)\n    return charge(total)\n"
    wait_for(client, client.post("/api/file", files={"file": ("shop.py", py, "text/plain")}).json()["job_id"])
    assert "checkout (line 1) calls: price, charge" in seen["prompt"]   # parser outline for Python

    binary = client.post("/api/file", files={"file": ("app.exe", b"MZ\x00\x00\x01", "application/octet-stream")})
    assert binary.status_code == 400


def test_single_file_needs_llm(client):
    response = client.post("/api/file", files={"file": ("a.go", b"package main", "text/plain")})
    assert response.status_code == 503


def test_training_log_records_model_exchanges(client, monkeypatch, tmp_path):
    reply = {"analysis": "a", "mode": "flowchart", "title": "T",
             "nodes": [{"id": "n1", "label": "Start", "type": "start", "service": ""},
                       {"id": "n2", "label": "Do it", "type": "step", "service": ""}],
             "edges": [{"source": "n1", "target": "n2", "kind": "flow", "label": ""}]}
    app_state = client.app.state
    monkeypatch.setattr(app_state.llm, "is_available", lambda: True)
    monkeypatch.setattr(app_state.llm, "chat_json", lambda *a, **k: reply)
    monkeypatch.setattr(app_state.enricher.settings, "training_log", True)
    job_id = client.post("/api/prompt", json={"prompt": "Start then do it"}).json()["job_id"]
    assert wait_for(client, job_id)["status"] == "done"
    logs = list((app_state.settings.data_dir / "training").glob("*.jsonl"))
    import json
    record = json.loads(logs[0].read_text().splitlines()[0])
    assert record["task"] == "flowchart" and record["response"]["title"] == "T"


def test_partial_result_is_available_while_model_works(client, zip_fixture, monkeypatch):
    import threading
    import time

    gate = threading.Event()

    def slow_chat(messages, schema, name, max_tokens=None):
        gate.wait(5)  # hold the model until the test has seen the early result
        return {"files": []} if name == "file_summaries" else {"analysis": "", "title": "", "steps": []}

    llm = client.app.state.llm
    monkeypatch.setattr(llm, "is_available", lambda: True)
    monkeypatch.setattr(llm, "chat_json", slow_chat)
    with open(zip_fixture("refund_app"), "rb") as fh:
        job_id = client.post("/api/analyze", files={"file": ("refund_app.zip", fh, "application/zip")}).json()["job_id"]

    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["result"]:
            break
        time.sleep(0.05)
    assert job["status"] == "running"                      # model still busy...
    assert job["result"]["stats"]["workflows"] == 2        # ...but workflows are already viewable
    gate.set()
    assert wait_for(client, job_id)["status"] == "done"
