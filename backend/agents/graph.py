"""
Full multi-agent loop for one user goal.
Features:
- Dynamic multi-step loop supporting multi-page workflows & quizzes (up to 25 steps).
- Hierarchical memory: Orchestrator, Planner, and Verifier each have individual vector memory,
  plus a shared global memory for cross-agent learnings.
- Supports reusable interactive BrowserSession so manual interactions remain active.
- Persistent session: logins and cookies stay saved across runs.
"""
import asyncio
from agents.orchestrator import plan_steps
from agents.planner import plan_action
from agents.verifier import verify
from browser.agent import BrowserSession
from db.memory import store_global_memory


async def run_task(user_id: str, goal: str, on_update=None, headless: bool = True, session: BrowserSession | None = None):
    """
    Runs the autonomous multi-agent browser loop.
    """
    async def emit(event: str, data):
        if on_update:
            await on_update({"event": event, "data": data})

    # 1. Orchestrator breaks down goal using its specialized memory + global memory
    await emit("status", "Orchestrator retrieving memory and planning subtasks...")
    steps = await asyncio.to_thread(plan_steps, goal, user_id)
    await emit("plan", steps)

    owns_session = False
    if session is None:
        session = BrowserSession()
        await session.start(headless=headless)
        owns_session = True

    evidence = ""
    step_idx = 0
    max_steps = 25

    try:
        # Initial frame
        initial_frame = await session.take_screenshot()
        if initial_frame:
            await emit("screenshot", initial_frame)

        while step_idx < max_steps:
            # Get live page state with form inputs/buttons for quizzes
            page_summary = await session.get_page_summary()

            # 1. Check if the quiz is already completed (Results screen reached)
            is_quiz_finished = any(term in page_summary.lower() for term in [
                "your score:", "completed this module", "knowledge check results", "congratulations! you have completed"
            ])
            if is_quiz_finished:
                evidence = f"Quiz completed successfully! {page_summary}"
                await emit("page_state", "Quiz completed! Final results visible.")
                # Advance/finish in player if Next button exists
                await session.execute({"action": "click", "target": "Next", "value": ""})
                break

            # 2. Determine current step
            if step_idx < len(steps):
                current_step = steps[step_idx]
            else:
                # If pre-planned steps ran out but quiz is still presenting questions:
                if "Available Options:" in page_summary or "ACTIVE AWS ACADEMY" in page_summary:
                    current_step = f"Read Question {step_idx + 1}, select the correct AWS Cloud answer, and advance"
                    steps.append(current_step)
                else:
                    break

            # Planner decides action using its specialized memory
            action = await asyncio.to_thread(plan_action, current_step, user_id, page_summary)
            await emit("action", {
                "step": current_step,
                "step_num": step_idx + 1,
                "total_steps": len(steps),
                "action": action
            })

            if action.get("action") == "done":
                break

            # Execute action with Playwright / CDP bridge
            evidence = await session.execute(action)
            await emit("page_state", evidence[:500])

            # Capture and stream live viewport frame
            frame = await session.take_screenshot()
            if frame:
                await emit("screenshot", frame)

            step_idx += 1
            await asyncio.sleep(0.5)

        # 3. Final summary check for Verifier
        final_summary = await session.get_page_summary()
        if any(term in final_summary.lower() for term in ["your score:", "results", "completed this module", "knowledge check results", "congratulations"]):
            evidence = f"Quiz finished and submitted! {final_summary}"

        # 4. Verifier reviews collected evidence using its specialized memory
        await emit("status", "Verifier reviewing execution results...")
        result = await asyncio.to_thread(verify, goal, evidence, user_id)
        await emit("verified", result)

        # 5. Store high-level outcome in shared global memory for all agents
        await asyncio.to_thread(
            store_global_memory,
            user_id=user_id,
            goal=goal,
            outcome=evidence,
            verified=result.get("verified", True),
            steps=steps
        )

        return {"steps": steps, "evidence": evidence, "verification": result}
    finally:
        if owns_session:
            await session.close()
