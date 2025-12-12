Agent playbook for this repository
==================================

This repo holds AWS Lambda sources only. Keep changes minimal and functional for deployment as Lambda handlers.

Working style
-------------
- Default to Python 3.x Lambda runtime conventions.
- Stick to ASCII in code and docs unless required.
- Prefer dependency-free code; if you must add packages, note them clearly for layer/zip builds.
- Keep files small and single-purpose Lambda handlers; avoid sprawling utilities unless shared across handlers.

How to edit
-----------
- Use `apply_patch` for small edits; avoid destructive commands.
- Preserve any user changes you didn't make; don't revert unrelated diffs.
- Add brief comments only where logic isn't obvious.

Testing & validation
--------------------
- Aim for fast, local validation (e.g., unit tests or small harness scripts). If tests are missing, note gaps in your summary.
- When adding code, include a minimal way to exercise the handler (sample event payloads, notes on env vars).

Repository map
--------------
- `notion-webhook-handler.py`: Placeholder for the Notion webhook Lambda handler (currently empty).

Open questions / TODOs
----------------------
- Document expected Notion webhook payload shape and required environment variables.
- Decide packaging strategy (zip upload vs. SAM/CDK).
- Add tests or a local runner for the handler.
