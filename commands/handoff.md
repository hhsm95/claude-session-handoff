---
description: Save this session as a handoff document you can resume in a fresh session
argument-hint: "[--haiku] [focus]"
---

The `session-handoff` hook handles this command before it reaches you. Look in your context for a
`<session-handoff>` block.

- If the block is present, follow the instructions inside it exactly.
- If the block is absent, the hook did not run (a broken install). Reply only with this sentence
  and do not use any tools: "The session-handoff hook is not active; check /hooks."

Arguments: $ARGUMENTS
