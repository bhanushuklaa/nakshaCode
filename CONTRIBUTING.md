# Contributing

Thanks for helping! Small, focused pull requests are easiest to review.

## Setup

See "Local development" in the README. Before opening a PR, run:

```bash
cd backend && ruff check . && pytest -q
cd frontend && npm run build
```

## Guidelines

- **Keep the pipeline deterministic.** The AI model explains; it should not be
  needed to find structure. New detection logic belongs in `pipeline/`.
- **Add a test** for parser, resolver or trigger changes. Small fixture projects
  live in `backend/tests/fixtures/`.
- **New services** are one line in `backend/app/pipeline/integrations.yaml`.
- **Dependencies** must use a permissive license (MIT, BSD, ISC, Apache-2.0).
  CI enforces this.
- **Never execute uploaded code.** Analysis must stay static.

## Sign-off

Contributions are accepted under the Apache-2.0 license. Please sign off your
commits (`git commit -s`) to certify the [Developer Certificate of Origin](https://developercertificate.org/).
