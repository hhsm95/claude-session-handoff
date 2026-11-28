---
description: Report how many uncached tokens compacting saved across your sessions
---

The `session-handoff` hook handles this command before it reaches you and shows the report to
the user. You should never see this prompt.

If you are reading this, the hook did not run: either the user did not type the command (you
loaded it as a skill from a request in their own words) or the install is broken. Reply only with
this sentence and do not use any tools: "Type /session-handoff:savings yourself: the report only runs when you type the command. If you already typed it, the session-handoff hook is not active; check /hooks."
