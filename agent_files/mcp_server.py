# library allows await functions without restraining the remaining program
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
#library for validating data
from pydantic import BaseModel, Field
from trueforge_sdk import TrueForge
from trueforge_sdk.types import UserMessage


# servers
mcp = FastMCP("Agent Delegation", host="127.0.0.1", port=8000)
client = TrueForge(
    base_url="http://localhost:8790",
    timeout=900,
    max_retries=0,
)

# protects shared dictionary updates
state_lock = Lock()

# prevents duplicate session creation
session_creation_lock = Lock()

# agent name -> session id
sessions: dict[str, str] = {}

# agent name -> executor with 1 worker and its own task queue
executors: dict[str, ThreadPoolExecutor] = {}

# caller -> recipient -> number of outstanding assignments
waiting_on: dict[str, dict[str, int]] = {}


# individual assignment supplied by the calling agent
# Assignment and AgentReply inherits Basemodel -> allowing for arg data validation
class Assignment(BaseModel):
    #specify object fields/rules -> in this case string + > 1 char
    recipient: str = Field(min_length=1)
    message: str = Field(min_length=1)


# expected response from the recipient
class AgentReply(BaseModel):
    status: Literal["completed", "needs_input", "failed"]
    summary: str = Field(min_length=1)


# fetch existing agent session or create one
def get_agent_session(agent_name: str) -> str:
    with session_creation_lock:
        with state_lock:
            session_id = sessions.get(agent_name)

        if session_id is None:
            session = client.sessions.create(agent={"name": agent_name})
            session_id = session.data.id

            with state_lock:
                sessions[agent_name] = session_id

    return session_id


# executes one agent turn -> returns its response
def run_agent_turn(session_id: str, message: str) -> AgentReply:
    with client.sessions.create_turn_stream(
        session_id=session_id,
        input=[UserMessage(content=message)],
    ) as events:
        for event in events:
            # turn state updated to pauses for approval or other external input
            if (
                event.type == "turn.update"
                and event.state.status == "paused"
            ):  
                #external input/auth needed
                return AgentReply(
                    status="needs_input",
                    summary=(
                        "TrueForge paused this turn for external input. "
                        f"Open session {session_id} to inspect and resolve "
                        "its pending approval, question, or authentication."
                    ),
                )
            
            # turn updated to done
            if event.type == "turn.done":
                if event.state.status != "done":
                    raise RuntimeError(
                        f"Session {session_id} ended with status: "
                        f"{event.state.status}"
                    )

                if event.state.output is None:
                    raise RuntimeError(
                        f"Session {session_id} returned no final response."
                    )

                # validate the recipient's structured response
                return AgentReply.model_validate_json(
                    event.state.output.content
                )

    raise RuntimeError(
        f"Session {session_id} ended without a final result."
    )


# check whether an agent already depends on another agent
"""
See if the sender, before sending a message, itself needs to respond to an agent.
Tracing through `waiting_on`, if the sender can find itself, there will be delgation loop (bad).
The sender should NOT have any returns before delegating
"""
def has_wait_path(start: str, target: str) -> bool:
    remaining = [start]
    visited: set[str] = set()

    # trace through waiting_on (remaining) while tracking visited
    while remaining:
        agent = remaining.pop()

        if agent == target:
            #infinite loop would exist
            return True

        if agent not in visited:
            visited.add(agent)
            remaining.extend(waiting_on.get(agent, {}))

    #no potential infinite loop
    return False


# assignment finished -> remove its waiting relationship (waiting_on dict)
def release_wait(sender: str, recipient: str) -> None:
    with state_lock:
        recipients = waiting_on[sender]
        recipients[recipient] -= 1

        if recipients[recipient] == 0:
            del recipients[recipient]

        if not recipients:
            del waiting_on[sender]


# worker executes one assignment -> captures success or failure
def run_assignment(
    task_id: str,
    sender: str,
    recipient: str,
    message: str,
) -> dict[str, str]:
    session_id = ""

    try:
        session_id = get_agent_session(recipient)

        # recipient agent recieives sender, task id, message + skill-usage instructions
        incoming_message = (
            f"[Delegated by agent: {sender}]\n"
            "Carry out and respond via the `handle-delegated-task` skill.\n\n"
            f"Task ID: {task_id}\n\n"
            f"{message}"
        )

        #recipient agent carries out job -> store reply (AgentReply format)
        reply = run_agent_turn(session_id, incoming_message)

        result = {
            "task_id": task_id,
            "recipient": recipient,
            "session_id": session_id,
            "status": reply.status,
            "summary": reply.summary,
        }

    # include errors in batch results -> other assignments continue
    except Exception as error:
        result = {
            "task_id": task_id,
            "recipient": recipient,
            "session_id": session_id,
            "status": "failed",
            "summary": str(error),
        }

    #console print statement
    print(
        f"[{task_id}] {sender} -> {recipient} "
        f"({session_id or 'no session'}) {result['status']}",
        flush=True,
    )

    return result


# delegate assignments -> wait for all results
@mcp.tool()
# await -> async func
async def delegate(
    sender: str,
    assignments: list[Assignment],
) -> dict[str, object]:
    """
    Delegate tasks using exact TrueForge agent names.

    Different recipient agents work concurrently.
    Assignments to the same agent run sequentially in its saved session.

    Waits for every assignment to complete, request input, or fail.
    Results return automatically through this tool call.
    """
    sender = sender.strip()

    # validate incoming necessary info
    if not sender or not assignments:
        raise ValueError(
            "Sender and at least one assignment must be provided."
        )

    # format and validate the entire batch before submitting work
    normalized: list[tuple[str, str]] = []

    for assignment in assignments:
        recipient = assignment.recipient.strip()
        message = assignment.message.strip()

        if not recipient or not message:
            raise ValueError(
                "Every assignment needs a recipient and message."
            )

        normalized.append((recipient, message))

    # recipient agent -> Future (tied to job request)
    jobs: list[tuple[str, Future]] = []

    with state_lock:
        # prevent self-delegation and circular waiting
        for recipient, _ in normalized:
            """
            if the recipient has already delegated the sender, then
            both agents wait on eachtother indefintely -> infinite loop
            """
            if has_wait_path(recipient, sender):
                raise ValueError(
                    f"Delegating from {sender} to {recipient} would "
                    "create a circular wait. Return the question "
                    "to the caller instead."
                )

        for recipient, message in normalized:
            task_id = uuid4().hex

            # record this delgation assignments in `waiting_on`
            recipients = waiting_on.setdefault(sender, {})
            # # corresponds to the number of assignments outstanding
            recipients[recipient] = recipients.get(recipient, 0) + 1

            try:
                # create 1 worker for this agent if needed
                if recipient not in executors:
                    executors[recipient] = ThreadPoolExecutor(
                        max_workers=1,
                    )

                # executor queues tasks for the same agent
                job = executors[recipient].submit(
                    run_assignment,
                    task_id,
                    sender,
                    recipient,
                    message,
                )

            # submission failed -> return a failed assignment result
            except Exception as error:
                job = Future()
                job.set_result({
                    "task_id": task_id,
                    "recipient": recipient,
                    "session_id": "",
                    "status": "failed",
                    "summary": str(error),
                })

            #queue job to agent's worker
            jobs.append((recipient, job))

    # attach callback to each job outside the lock -> completed jobs may call immediately asynclly
    for recipient, job in jobs:
        job.add_done_callback(
            # removes waiting_on relationship between sender -> recipient
            lambda finished, recipient=recipient: release_wait(
                sender, recipient
            )
        )

    # wait without blocking the MCP server's event loop
    # shield prevents caller cancellation from cancelling queued assignments
    results = await asyncio.gather(
        #list of all asyncio protected futures -> `*` destructures list into seperate args for .gather
        *[
            #.wrap_future -> asyncio can recieve job's Future
            #.sheild -> any submitted work is protected if delegation canceled/interupted
            asyncio.shield(asyncio.wrap_future(job_future))
            for _, job_future in jobs
        ]
    )

    #results contain all job responses (from run_assignment)
    return {"results": results}


# run HTTP server -> exposes the MCP tools
if __name__ == "__main__":
    mcp.run(transport="streamable-http")