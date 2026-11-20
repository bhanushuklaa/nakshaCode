# Security policy

FlowLens processes untrusted uploads, so security reports are very welcome.

## Reporting

Please **do not open a public issue**. Use GitHub's private
"Report a vulnerability" button on this repository instead. We aim to reply
within 7 days.

## Design guarantees

- Uploaded code is parsed with tree-sitter and **never executed or imported**.
- Archives are checked for zip-slip paths, symlinks, file count, per-file size
  and total uncompressed size before anything is written.
- Uploads are deleted as soon as analysis finishes; results expire from memory
  after `JOB_TTL_MINUTES`.
- The API container runs as a non-root user and needs no internet access at
  runtime.
