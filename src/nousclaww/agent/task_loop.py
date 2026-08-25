"""TaskLoop — multi-step autonomous task execution (plan -> act -> observe -> reflect).

The TaskLoop is the autonomous execution engine that the Nous MVP uses
when it needs NoUsClaWW to *do* something, not just chat. It runs a
bounded loop of:

1. **Plan** — ask the brain for the next action given the task and history.
2. **Act** — dispatch the action through the ToolRegistry.
3. **Observe** — capture the tool's output.
4. **Reflect** — ask the brain whether the task is complete or if another
   step is needed.

The loop terminates when:
- The brain reports ``done: true`` in its plan response, OR
- The maximum step count is reached, OR
- The loop is cancelled externally.

Each step's result is recorded as a ``StepResult`` and the overall task
outcome is a ``TaskResult``.

SYNTH:
    purpose: Multi-step autonomous task loop with plan/act/observe/reflect cycle.
    axioms: [local_first, epistemic_boundary, completion_assumption, honest_failure_over_fake_success, reversibility_awareness]
    objective: Execute a user task autonomously by breaking it into steps,
        calling tools, observing results, and reflecting on progress —
        with honest reporting of what was done and what remains.
    anti_patterns:
        - Never declare a task complete without the brain confirming
        - Never skip the reflect step
        - Never run more steps than the maximum without reporting
        - Never fabricate tool outputs
        - Never ignore a cancellation request

#C Adapted from the agent loop pattern and sovereign_sockets.brain_interface
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sovereign_sockets.brain_interface import BrainInterface
from nousclaww.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """The outcome of a single step in the task loop.

    Attributes:
        step: The step number (0-based).
        plan: The raw plan string from the brain.
        action: The parsed action name (or None if the brain reported done).
        action_args: The parsed action arguments.
        observation: The tool's output (or None if no action was taken).
        reflection: The brain's reflection on the step.
        done: True if the brain reported the task as complete after this step.
        elapsed_s: Time spent on this step.
    """

    step: int
    plan: str
    action: str | None = None
    action_args: dict[str, Any] = field(default_factory=dict)
    observation: str | None = None
    reflection: str = ""
    done: bool = False
    elapsed_s: float = 0.0


@dataclass
class TaskResult:
    """The outcome of a complete task run.

    Attributes:
        task: The original task description.
        steps: List of StepResult for each step executed.
        completed: True if the brain reported the task as complete.
        cancelled: True if the task was cancelled externally.
        summary: The brain's final summary of what was accomplished.
        elapsed_s: Total elapsed time.
        error: Error message if the task failed, else None.
    """

    task: str
    steps: list[StepResult] = field(default_factory=list)
    completed: bool = False
    cancelled: bool = False
    summary: str = ""
    elapsed_s: float = 0.0
    error: str | None = None


class TaskLoop:
    """Multi-step autonomous task execution loop.

    Args:
        brain: The BrainInterface implementation that plans and reflects.
        tools: The ToolRegistry that dispatches actions.
        max_steps: Maximum number of steps before the loop terminates.
        system_prompt: Optional system prompt prepended to every brain call.

    Usage::

        loop = TaskLoop(brain, tools, max_steps=10)
        result = loop.run("Read the README and summarise it in 3 bullet points")
        print(result.summary)
    """

    PLAN_PROMPT = (
        "You are an autonomous agent. Given the task and the history of "
        "actions taken so far, decide the next action.\n\n"
        "Respond with a JSON object:\n"
        '{{"action": "<tool_name>", "args": {{<kwargs>}}, "done": false}}\n'
        "If the task is complete, respond with:\n"
        '{{"done": true, "summary": "<what was accomplished>"}}\n\n'
        "Available tools: {tools}\n\n"
        "Task: {task}\n\nHistory:\n{history}\n\nNext action:"
    )

    REFLECT_PROMPT = (
        "You are reflecting on the last action taken.\n\n"
        "Task: {task}\n"
        "Last action: {action}\n"
        "Observation: {observation}\n\n"
        "Is the task complete? Respond with a JSON object:\n"
        '{{"done": true/false, "summary": "<brief reflection>"}}\n'
    )

    def __init__(
        self,
        brain: BrainInterface,
        tools: ToolRegistry,
        max_steps: int = 10,
        system_prompt: str = "",
    ) -> None:
        self.brain = brain
        self.tools = tools
        self.max_steps = max_steps
        self.system_prompt = system_prompt
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation of the running task."""
        self._cancelled = True
        logger.info("TaskLoop: cancellation requested")

    def reset(self) -> None:
        """Reset the cancellation flag. Call before run() for a fresh task."""
        self._cancelled = False

    def run(self, task: str) -> TaskResult:
        """Execute a task autonomously.

        Args:
            task: The task description.

        Returns:
            TaskResult with the outcome.
        """
        start = time.time()
        steps: list[StepResult] = []
        history: list[str] = []
        tool_names = [t.name for t in self.tools.list_tools()]

        for step_num in range(self.max_steps):
            if self._cancelled:
                return TaskResult(
                    task=task,
                    steps=steps,
                    completed=False,
                    cancelled=True,
                    summary="Task cancelled by user.",
                    elapsed_s=time.time() - start,
                )

            step_start = time.time()
            plan_text = self._plan(task, history, tool_names)
            action, action_args, done, summary = self._parse_plan(plan_text)

            if done:
                step = StepResult(
                    step=step_num,
                    plan=plan_text,
                    done=True,
                    reflection=summary,
                    elapsed_s=time.time() - step_start,
                )
                steps.append(step)
                return TaskResult(
                    task=task,
                    steps=steps,
                    completed=True,
                    summary=summary,
                    elapsed_s=time.time() - start,
                )

            # Act
            observation = self._act(action, action_args)
            history.append(f"Step {step_num + 1}: {action}({action_args}) -> {observation[:200]}")

            # Reflect
            reflection_text = self._reflect(task, action, observation)
            ref_done, ref_summary = self._parse_reflection(reflection_text)

            step = StepResult(
                step=step_num,
                plan=plan_text,
                action=action,
                action_args=action_args,
                observation=observation,
                reflection=reflection_text,
                done=ref_done,
                elapsed_s=time.time() - step_start,
            )
            steps.append(step)

            if ref_done:
                return TaskResult(
                    task=task,
                    steps=steps,
                    completed=True,
                    summary=ref_summary,
                    elapsed_s=time.time() - start,
                )

        # Max steps reached
        return TaskResult(
            task=task,
            steps=steps,
            completed=False,
            summary=f"Task did not complete within {self.max_steps} steps.",
            elapsed_s=time.time() - start,
            error="max_steps_exceeded",
        )

    # ── Internal ──────────────────────────────────────────────────────────

    def _plan(self, task: str, history: list[str], tool_names: list[str]) -> str:
        """Ask the brain for the next action."""
        prompt = self.PLAN_PROMPT.format(
            tools=", ".join(tool_names) if tool_names else "(none)",
            task=task,
            history="\n".join(history) if history else "(no actions taken yet)",
        )
        ctx = self.system_prompt
        resp = self.brain.reason(context=ctx, query=prompt, constraints={})
        return resp.output

    def _act(self, action: str | None, args: dict[str, Any]) -> str:
        """Dispatch an action through the ToolRegistry."""
        if action is None:
            return "(no action)"
        if not self.tools.has_tool(action):
            return f"ERROR: unknown tool '{action}'"
        result = self.tools.dispatch(action, **args)
        if not result.success:
            return f"ERROR: {result.error}"
        return result.output

    def _reflect(self, task: str, action: str, observation: str) -> str:
        """Ask the brain to reflect on the last action."""
        prompt = self.REFLECT_PROMPT.format(
            task=task,
            action=action,
            observation=observation[:500],
        )
        resp = self.brain.reason(context=self.system_prompt, query=prompt, constraints={})
        return resp.output

    def _parse_plan(self, text: str) -> tuple[str | None, dict[str, Any], bool, str]:
        """Parse the brain's plan response into (action, args, done, summary)."""
        data = self._extract_json(text)
        if data is None:
            return None, {}, False, ""
        if data.get("done"):
            return None, {}, True, data.get("summary", "Task complete.")
        action = data.get("action")
        args = data.get("args", {})
        if not isinstance(args, dict):
            args = {}
        return action, args, False, ""

    def _parse_reflection(self, text: str) -> tuple[bool, str]:
        """Parse the brain's reflection into (done, summary)."""
        data = self._extract_json(text)
        if data is None:
            return False, text[:200]
        return bool(data.get("done", False)), data.get("summary", "")

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """Extract the first JSON object from a text string."""
        # Try direct parse first
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to find a JSON block in the text
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except (json.JSONDecodeError, ValueError):
                        return None
        return None
