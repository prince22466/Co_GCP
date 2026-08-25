# Resource Manager ADK releases

Versioned resource-manager agents live in this directory.

| Release | Status | Location |
|---|---|---|
| v1 | Current baseline | [`v1/`](v1/) |

`v1` is the first deployable ADK resource-manager release. Keep its policy and
deployment behavior stable so experiment results remain reproducible. Create a
new sibling directory for behavior-changing agent revisions.

Local `.venv` and `*.egg-info` directories at this level are generated
development artifacts; they are ignored and are not part of any release.
