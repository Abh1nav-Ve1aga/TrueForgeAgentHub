---
name: delegate

description: Assign work to other agents and collect their results through the delegate MCP tool.
---

# Delegate Work

Consult the agent roles skill to choose the appropriate agents.

Call `delegate` with:

- `sender`: Your exact TrueForge agent name.
- `assignments`: A list of tasks, each containing:
  - `recipient`: The recipient’s exact TrueForge agent name.
  - `message`: Clear instructions, necessary context, constraints, and expected results.

Group independent tasks into one call so different agents can work in parallel.
Delegate dependent work after its prerequisites are available.

For detailed instructions or shared reference material, use the filesystem MCP
tools to create a concise Markdown document in the designated shared `mds` directory.
Include its path and what the recipient should read in the assignment.
Use the message directly for short tasks.

The tool waits for all assignments and returns their results automatically.
Review each result:

- `completed`: Assess the result and continue the workflow.
- `needs_input`: Resolve the question and delegate a follow-up. If TrueForge
  paused the turn for approval or authentication, resolve that pending action and delegate the agent to continue work.
- `failed`: Inspect the error and determine the next step. Check whether work
  already occurred before retrying.

When receiving delegated work, follow `handle-delegated-task`. Return your
result in your final response; do not send a separate completion message.

Do not delegate to yourself or create circular waits. Return questions to a
waiting caller through `needs_input`.

Report completion to the user once all required work has been verified.