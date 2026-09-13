import json
from agents.llm import call_llm, clean_llm_json
from db.memory import retrieve_agent_memory, retrieve_global_memory, store_agent_memory

SYSTEM = """You are the Orchestrator agent for an autonomous web browsing AI.
Your job is to break the user's overall goal into an ordered sequence of concrete, actionable browser steps.
Do NOT call external functions or tools. Respond ONLY with a raw, valid JSON object:
{"steps": ["step 1 ...", "step 2 ...", ...]}

CRITICAL RULES:
1. NEVER output a single step that just repeats the user's overall prompt. Always break it down into concrete sequential actions.
2. For Quizzes, Knowledge Checks, Exams, or Assignments (e.g. AWS Academy, Canvas LMS, Moodle):
   You MUST generate sequential question steps covering the full test from Start to Submission:
   - "Click the Start button to launch the knowledge check"
   - "Read Question 1, select the correct AWS Cloud answer, and advance"
   - "Read Question 2, select the correct AWS Cloud answer, and advance"
   - "Read Question 3, select the correct AWS Cloud answer, and advance"
   - "Read Question 4, select the correct AWS Cloud answer, and advance"
   - "Read Question 5, select the correct AWS Cloud answer, and advance"
   - "Read Question 6, select the correct AWS Cloud answer, and advance"
   - "Read Question 7, select the correct AWS Cloud answer, and advance"
   - "Read Question 8, select the correct AWS Cloud answer, and advance"
   - "Read Question 9, select the correct AWS Cloud answer, and advance"
   - "Read Question 10, select the correct AWS Cloud answer, and advance"
   - "Review answers and finish the completed knowledge check"
3. If the user indicates a quiz or page is ALREADY OPEN, do NOT navigate away or open a new URL. Start directly with interacting with the open quiz.
"""


def plan_steps(goal: str, user_id: str = "guest") -> list[str]:
    goal_lower = goal.lower()

    # 1. AWS Hands-On Labs (Vocareum / Canvas LMS)
    if any(k in goal_lower for k in ["lab 6", "scale and load balance", "auto scaling", "5426876"]):
        return [
            "Start AWS Lab 6 on Vocareum and initialize cloud sandbox session",
            "Create AMI 'WebServerAMI' from 'Web Server 1' and wait for availability",
            "Create Target Group 'LabGroup' and Application Load Balancer 'LabELB'",
            "Create Launch Template 'LabConfig' and Auto Scaling Group 'Lab Auto Scaling Group'",
            "Verify instance health checks and load balancer routing",
            "Terminate original 'Web Server 1' instance",
            "Submit Lab 6 on Vocareum and verify 100% grade report"
        ]
    elif any(k in goal_lower for k in ["lab 2", "build your vpc", "create your vpc", "launch a web server", "5426864"]):
        return [
            "Start AWS Lab 2 on Vocareum and initialize cloud sandbox session",
            "Create VPC 'lab-vpc' with subnets in us-east-1a, IGW, and NAT Gateway",
            "Create additional public and private subnets in us-east-1b and update route tables",
            "Create 'Web Security Group' allowing HTTP traffic on port 80",
            "Launch 'Web Server 1' into public subnet with bootstrap script and verify status checks",
            "Submit Lab 2 on Vocareum and verify 100% grade report"
        ]

    # 2. If it's a quiz / knowledge check, always provide the full comprehensive 12-step plan
    if any(k in goal_lower for k in ["quiz", "knowledge check", "exam", "test", "assignment", "module"]):
        return [
            "Click the Start button to launch the knowledge check",
            "Read Question 1, select the correct AWS Cloud answer, and advance",
            "Read Question 2, select the correct AWS Cloud answer, and advance",
            "Read Question 3, select the correct AWS Cloud answer, and advance",
            "Read Question 4, select the correct AWS Cloud answer, and advance",
            "Read Question 5, select the correct AWS Cloud answer, and advance",
            "Read Question 6, select the correct AWS Cloud answer, and advance",
            "Read Question 7, select the correct AWS Cloud answer, and advance",
            "Read Question 8, select the correct AWS Cloud answer, and advance",
            "Read Question 9, select the correct AWS Cloud answer, and advance",
            "Read Question 10, select the correct AWS Cloud answer, and advance",
            "Review answers and submit the completed knowledge check"
        ]

    # 1. Retrieve past Orchestrator-specific memories
    orch_memories = retrieve_agent_memory(user_id, "orchestrator", goal, top_k=2)
    global_memories = retrieve_global_memory(user_id, goal, top_k=2)

    past_context = ""
    if orch_memories:
        past_context += "Past Orchestration Learnings:\n" + "\n".join([f"- {m['content']}" for m in orch_memories]) + "\n"
    if global_memories:
        past_context += "Related Past Goals:\n" + "\n".join([f"- {m['content']}" for m in global_memories]) + "\n"

    user_prompt = f"Goal: {goal}\n\n{past_context}" if past_context else goal
    raw = call_llm(SYSTEM, user_prompt)
    cleaned = clean_llm_json(raw)

    try:
        data = json.loads(cleaned)
        steps = data.get("steps", [])
        if steps and len(steps) > 1:
            store_agent_memory(
                user_id=user_id,
                agent_name="orchestrator",
                content=f"Goal: {goal} -> Planned {len(steps)} subtasks: {', '.join(steps[:3])}",
                success=True
            )
            return steps
    except Exception:
        pass

    return [goal]
