---
name: handle-delegated-task

description: Handle an assignment received from another agent and return a structured result.
---

# Handle a Delegated Task

Carry out the supplied assignment using the provided context and relevant skills.

Finish your turn with the result. The delegation tool returns it to the caller
automatically; do not send a separate completion message.

If you need clarification from the caller, return your question with
`needs_input`. Do not delegate back to an agent waiting for your result.

Return only a JSON object, without Markdown fences:

{
  "status": "completed",
  "summary": "Your result."
}

Choose the appropriate status:

- `completed`: The assignment is finished.
- `needs_input`: Progress requires clarification or missing information.
- `failed`: You could not complete the assignment.

In `summary`, include the relevant findings, questions, file paths, commit
references, check results, and remaining limitations. Distinguish completed
work from anything unverified.