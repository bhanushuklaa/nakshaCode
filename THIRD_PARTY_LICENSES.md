# Third-party licenses

CodeNaksha is Apache-2.0. Every dependency uses a permissive license that's
compatible with it. CI fails if a GPL, AGPL, SSPL, EPL, Elastic or BUSL
dependency is added (see `.github/workflows/ci.yml`).

To regenerate the full lists:

```bash
cd backend && pip-licenses --from=mixed --format=markdown
cd frontend && npx license-checker-rseidelsohn --production --markdown
```

## Backend (Python)

| Package | License |
|---|---|
| fastapi, pydantic, pydantic-settings | MIT |
| uvicorn, starlette, httpx, httpcore | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| PyYAML | MIT |
| tree-sitter, tree-sitter-language-pack | MIT (grammars are downloaded separately; Python/JS/TS grammars are MIT) |
| certifi (via httpx) | MPL-2.0, used unmodified |

## Frontend (JavaScript)

| Package | License |
|---|---|
| react, react-dom | MIT |
| @xyflow/react (React Flow) | MIT |
| @dagrejs/dagre | MIT |
| cytoscape | MIT |
| d3-hierarchy | ISC |
| zustand | MIT |
| lucide-react | ISC |

## Runtime services (separate containers, not redistributed)

| Software | License |
|---|---|
| llama.cpp | MIT |
| nginx | BSD-2-Clause |
| cloudflared | Apache-2.0 |
| Qwen3-Coder-30B-A3B-Instruct (model weights) | Apache-2.0 |
