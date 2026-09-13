import json
import re
from agents.llm import call_llm, clean_llm_json
from db.memory import retrieve_agent_memory, store_agent_memory

SYSTEM = """You are the Planner agent for an autonomous web browser and an expert in AWS Cloud Computing.
Do NOT call external functions or tools.
Given the current subtask, past context, and the LIVE page state (which includes embedded quiz/tool iframes), output the SINGLE next browser action as a raw JSON object.

Important Rules:
1. For Quizzes, Knowledge Checks, and Exams (e.g. AWS Academy, Canvas LMS):
   - If the page or iframe displays "To continue, choose Start" or has a "Start" / "Begin" button:
     action MUST be "click", target MUST be "Start".
   - When a Question and Available Options are visible:
     Identify the official, most accurate answer dynamically based on the visible question and choices.
     - Single choice question:
       action MUST be "select_option", value MUST be the exact choice text (e.g. "Spot Instances").
     - Multi-select question (e.g. "(Choose two)" or "(Choose three)"):
       CRITICAL COUNT RULE: If the question contains "(Choose two)", you MUST select EXACTLY TWO choices separated by comma.
       If it contains "(Choose three)", you MUST select EXACTLY THREE choices separated by comma.
       action MUST be "select_option", target MUST be "checkbox", value MUST list the exact choices separated by a comma.
       Example: {"action": "select_option", "target": "checkbox", "value": "Amazon Machine Image (AMI), Amazon EC2 instance type"}
   - If the quiz shows a completion / results score slide (e.g. "You scored", "Result", or all questions completed):
     action MUST be "done", target: "", value: "Quiz completed"
   - If a confirmation or feedback modal with "Continue" or "OK" is visible:
     action MUST be "click", target MUST be "Continue".
   - CRITICAL: NEVER click outer Canvas LMS course navigation links like "Next Module", "Previous", "Home", or "Discussions" that navigate away from the quiz!

2. If the subtask mentions typing, searching, entering text, or has a quoted query:
   - action MUST be "type"
   - value MUST be the exact query to search or type
   - target should be "search bar" or the input selector

3. If navigating to a site or URL:
   - action MUST be "navigate"
   - target is the URL (e.g. "https://www.youtube.com")

4. If clicking a link, button, or element:
   - action MUST be "click"
   - target is the button or link text

5. If pressing Enter:
   - action MUST be "press", value: "Enter"

Respond ONLY as raw valid JSON:
{"action": "navigate|select_option|type|click|press|scroll|read|done", "target": "element or url", "value": "text or empty string"}
"""


from concurrent.futures import ThreadPoolExecutor
from agents.llm import get_client

# Multi-Model Panel with Accuracy Multipliers (Weights)
ENSEMBLE_MODELS = [
    {"name": "openai/gpt-oss-120b", "weight": 3.0},   # 120B SOTA open-weights reasoning model
    {"name": "qwen/qwen3.8-27b", "weight": 2.0},      # 27B high-precision instruction model
    {"name": "qwen/qwen3.6-27b", "weight": 1.5},      # 27B parallel verification model (no rate limit!)
]


from db.quiz_memory import get_known_quiz_answer, get_known_incorrect_options

def infer_quiz_selection_count(text: str, is_checkbox: bool = False) -> int:
    """Intelligently detects how many options to pick from direct instructions, natural language (e.g. 'what are three types of...'), or UI input type."""
    norm = re.sub(r'[\u00a0\s\?]+', ' ', text).lower()

    # Explicit single-select indicators
    is_explicit_single = bool(re.search(
        r'\b(?:select\s+the\s+best\s+answer|choose\s+the\s+best\s+answer|choose\s+one|select\s+one)\b',
        norm
    ))

    # Multi-item natural phrasing for 3 (e.g. "what are three types of...", "which 3 services...", "three pillars", etc.)
    pattern_3 = (
        r'\b(?:choose|select|pick|identify|name|mark)\s*(?:three|3)\b|'
        r'\b(?:what|which)\s*(?:are\s*)?(?:the\s*)?(?:three|3)\b|'
        r'\b(?:what|which)\s+(?:three|3)\b|'
        r'\b(?:three|3)\s*(?:types|categories|pillars|components|characteristics|features|elements|services|benefits|advantages|areas|options|criteria|factors|principles|steps|reasons|models|examples|ways|attributes|dimensions)\b|'
        r'\b(?:three|3)\s+of\s+the\s+following\b'
    )

    # Multi-item natural phrasing for 2 (e.g. "what are two types of...", "which two services...", etc.)
    pattern_2 = (
        r'\b(?:choose|select|pick|identify|name|mark)\s*(?:two|2)\b|'
        r'\b(?:what|which)\s*(?:are\s*)?(?:the\s*)?(?:two|2)\b|'
        r'\b(?:what|which)\s+(?:two|2)\b|'
        r'\b(?:two|2)\s*(?:types|categories|pillars|components|characteristics|features|elements|services|benefits|advantages|areas|options|criteria|factors|principles|steps|reasons|models|examples|ways|attributes|dimensions)\b|'
        r'\b(?:two|2)\s+of\s+the\s+following\b'
    )

    # Multi-item natural phrasing for 4
    pattern_4 = (
        r'\b(?:choose|select|pick|identify|name|mark)\s*(?:four|4)\b|'
        r'\b(?:what|which)\s*(?:are\s*)?(?:the\s*)?(?:four|4)\b|'
        r'\b(?:what|which)\s+(?:four|4)\b|'
        r'\b(?:four|4)\s*(?:types|categories|pillars|components|characteristics|features|elements|services|benefits|advantages|areas|options|criteria|factors|principles|steps|reasons|models|examples|ways|attributes|dimensions)\b|'
        r'\b(?:four|4)\s+of\s+the\s+following\b'
    )

    # If explicit single answer like '(Select the best answer.)' is present,
    # only an explicit parenthetical instruction like '(Choose three)' or '(Choose two)' overrides it
    if is_explicit_single:
        if re.search(r'\b(?:choose|select)\s*(?:three|3)\b', norm):
            return 3
        if re.search(r'\b(?:choose|select)\s*(?:two|2)\b', norm):
            return 2
        return 1

    if re.search(pattern_3, norm):
        return 3
    if re.search(pattern_2, norm):
        return 2
    if re.search(pattern_4, norm):
        return 4

    if is_checkbox:
        return 2

    return 1


def ensemble_quiz_vote(question_text: str, options: list, required_count: int = 1, known_incorrect: list = None, is_checkbox: bool = False) -> list:
    """Queries multiple free Groq models in parallel with accuracy multipliers to elect the consensus answer(s)."""
    if not options:
        return []
    if len(options) <= required_count:
        return options

    client = get_client()
    if not client:
        return options[:required_count]

    incorrect_warn = ""
    if known_incorrect:
        incorrect_warn = f"\nWARNING: The following choice(s) were previously tested and are CONFIRMED INCORRECT: {json.dumps(known_incorrect)}. DO NOT select them!"

    prompt = f"""Question: {question_text}
Available Options:
{json.dumps(options, indent=2)}{incorrect_warn}

Task: Identify the official, most accurate answer(s) strictly from the list above.
Selection constraint: Pick exactly {required_count} option(s).
Respond ONLY with raw JSON:
{{"selected": ["exact option text from the list above"]}}"""

    def _query(m):
        try:
            r = client.chat.completions.create(
                model=m["name"],
                messages=[
                    {"role": "system", "content": "You are a world-class AWS Cloud and Certification Solutions Architect. Select strictly from the available choices."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=450
            )
            raw = r.choices[0].message.content or ""
            cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
            match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
            if not match:
                match = re.search(r"(\{.*\})", raw, re.DOTALL)
            if match:
                cleaned = match.group(1).strip()
            data = json.loads(cleaned)
            return m["name"], m["weight"], data.get("selected", [])
        except Exception as e:
            print(f"[ENSEMBLE] {m['name']} error: {e}", flush=True)
            return m["name"], 0.0, []

    print(f"\n[ENSEMBLE] Polling {len(ENSEMBLE_MODELS)} models in parallel with accuracy multipliers for: {question_text[:80]}...", flush=True)
    with ThreadPoolExecutor(max_workers=len(ENSEMBLE_MODELS)) as executor:
        results = list(executor.map(_query, ENSEMBLE_MODELS))

    votes = {opt: 0.0 for opt in options}
    breakdown = {opt: [] for opt in options}

    for model_name, weight, selected in results:
        if not selected or weight <= 0:
            continue
        if isinstance(selected, str):
            selected = [selected]
        for sel in selected:
            sel_clean = sel.lower().strip()
            for opt in options:
                opt_clean = opt.lower().strip()
                if sel_clean == opt_clean or (len(sel_clean) > 4 and (sel_clean in opt_clean or opt_clean in sel_clean)):
                    votes[opt] += weight
                    breakdown[opt].append(f"{model_name} (+{weight})")
                    break

    # If an option was confirmed incorrect in a past attempt, zero it out
    if known_incorrect:
        for inc_set in known_incorrect:
            for inc in inc_set:
                for opt in options:
                    if inc.lower().strip() == opt.lower().strip():
                        votes[opt] = -100.0

    ranked = sorted(votes.items(), key=lambda x: x[1], reverse=True)
    print("--- [ENSEMBLE CONSENSUS TALLY] ---", flush=True)
    for opt, score in ranked:
        print(f"  {score:.1f} pts: {opt} -> {breakdown[opt]}", flush=True)

    # Dynamic consensus count adjustment if checkbox question
    if is_checkbox and required_count <= 2:
        model_lens = [len(sel) for _, w, sel in results if w >= 2.0 and isinstance(sel, list) and len(sel) > 0]
        if model_lens:
            from collections import Counter
            c = Counter(model_lens)
            most_common_len, occurrences = c.most_common(1)[0]
            if most_common_len > required_count and occurrences >= 2:
                print(f"[ENSEMBLE] Models consensus selected {most_common_len} options for checkbox question, updating required count from {required_count} -> {most_common_len}", flush=True)
                required_count = most_common_len

    winners = [opt for opt, score in ranked if score > 0][:required_count]
    if not winners:
        winners = options[:required_count]
    print(f"[ENSEMBLE WINNER (count={required_count})]: {winners}\n", flush=True)
    return winners


def plan_action(subtask: str, user_id: str, page_state: str = "") -> dict:
    sub_lower = subtask.lower()
    page_lower = page_state.lower()

    # 0. SPECIAL CASE: NATIVE AWS HANDS-ON LABS (Vocareum / Cloud Labs)
    if any(k in sub_lower for k in ["lab 6", "lab 2", "aws lab", "vocareum"]) or any(k in page_lower for k in ["vocareum.com", "lab 6: scale", "lab 2: build"]):
        if any(k in sub_lower for k in ["start", "create ami", "target group", "load balancer", "launch template", "auto scaling", "terminate", "submit", "finish", "execute"]):
            return {
                "action": "execute_aws_lab",
                "target": "Lab 6" if ("lab 6" in sub_lower or "scale" in page_lower or "5426876" in page_lower) else "Lab 2",
                "value": subtask
            }

    # 1. SPECIAL CASE: ACTIVE QUIZ / KNOWLEDGE CHECK WITH AVAILABLE OPTIONS
    if "available options:" in page_lower:
        # If Results / Completion screen is shown
        if any(k in page_lower for k in ["knowledge check results", "your score:", "required score:", "congratulations! you have completed"]):
            return {"action": "done", "target": "", "value": "Completed quiz successfully"}

        # If Start screen is shown
        if "to continue, choose start" in page_lower or ("start" in page_lower and "available buttons:" in page_state and "'start'" in page_lower):
            return {"action": "click", "target": "Start", "value": ""}

        # Parse Options list
        try:
            opts_part = page_state.split("Available Options:")[1].split("\n")[0]
            import ast
            parsed_opts = ast.literal_eval(opts_part.strip())
            if parsed_opts and isinstance(parsed_opts, list):
                # Extract question text from page_state
                q_text = page_state
                if "--- [ACTIVE AWS ACADEMY" in page_state:
                    q_text = page_state.split("--- [ACTIVE AWS ACADEMY")[1]
                if "Available Buttons:" in q_text:
                    q_text = q_text.split("Available Buttons:")[0]

                # 0. Check Persistent Quiz Memory first! (Instant memory hit for previously seen/revealed questions)
                known_answer = get_known_quiz_answer(q_text.strip(), parsed_opts)
                if known_answer:
                    print(f"\n[QUIZ MEMORY HIT] Reusing verified correct answer from past run: {known_answer}!\n", flush=True)
                    data = {
                        "action": "select_option",
                        "target": "checkbox" if len(known_answer) > 1 else "radio",
                        "value": ", ".join(known_answer)
                    }
                    store_agent_memory(
                        user_id=user_id,
                        agent_name="planner",
                        content=f"Subtask: {subtask} -> Reused Known Answer: {data['value']}",
                        success=True
                    )
                    return data

                # Determine required count using robust natural language + regex + input type detection
                is_chk = ("selection type: checkbox" in page_lower or "checkbox (multi-select)" in page_lower)
                combined_text = q_text + " " + page_state
                required_count = infer_quiz_selection_count(combined_text, is_checkbox=is_chk)

                # Execute weighted multi-model consensus (with known incorrect options suppressed)
                known_incorrect = get_known_incorrect_options(q_text.strip())
                winners = ensemble_quiz_vote(q_text.strip(), parsed_opts, required_count, known_incorrect=known_incorrect, is_checkbox=is_chk)
                data = {
                    "action": "select_option",
                    "target": "checkbox" if len(winners) > 1 or is_chk else "radio",
                    "value": ", ".join(winners)
                }
                store_agent_memory(
                    user_id=user_id,
                    agent_name="planner",
                    content=f"Subtask: {subtask} -> Ensemble Action: {data['action']} on {data['value']}",
                    success=True
                )
                return data
        except Exception as e:
            print(f"[PLANNER] Quiz parse error: {e}", flush=True)

    # 2. STANDARD PLANNER REASONING
    planner_memories = retrieve_agent_memory(user_id, "planner", subtask, top_k=2)
    past_tips = ""
    if planner_memories:
        past_tips = "\nPast Action Learnings:\n" + "\n".join([f"- {m['content']}" for m in planner_memories])

    user_prompt = f"Subtask: {subtask}{past_tips}\n\nCurrent Browser State:\n{page_state}"
    raw = call_llm(SYSTEM, user_prompt, temperature=0.1)
    cleaned = clean_llm_json(raw)

    data = None
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "action" in parsed:
            data = parsed
    except Exception:
        pass

    # Extract any quoted strings from subtask (e.g. "live insaan")
    m_quoted = re.search(r'["\'](.*?)["\']', subtask)
    quoted_text = m_quoted.group(1).strip() if m_quoted else ""

    # Enforce typing / query if subtask clearly asks to type or search a query
    if any(k in sub_lower for k in ["type", "search for", "query", "enter query"]) and quoted_text:
        if not data or data.get("action") == "click" or not data.get("value"):
            data = {
                "action": "type",
                "target": "search bar",
                "value": quoted_text
            }

    if data:
        store_agent_memory(
            user_id=user_id,
            agent_name="planner",
            content=f"Subtask: {subtask} -> Action: {data['action']} on {data.get('target', '')} value: {data.get('value', '')}",
            success=True
        )
        return data

    # Fallback heuristics
    if any(k in sub_lower for k in ["navigate", "go to", "open"]) and ("http" in subtask or ".com" in subtask or ".org" in subtask):
        for w in subtask.split():
            if "http" in w or ".com" in w or ".org" in w or ".edu" in w:
                return {"action": "navigate", "target": w, "value": ""}

    if quoted_text and any(k in sub_lower for k in ["type", "search", "enter"]):
        return {"action": "type", "target": "search bar", "value": quoted_text}

    if any(k in sub_lower for k in ["select", "choose", "answer", "option"]):
        opt_val = quoted_text
        if not opt_val and "Available Options:" in page_state:
            try:
                opts_part = page_state.split("Available Options:")[1].split("\n")[0]
                import ast
                parsed_opts = ast.literal_eval(opts_part.strip())
                if parsed_opts and isinstance(parsed_opts, list):
                    opt_val = parsed_opts[0]
            except Exception:
                pass
        return {"action": "select_option", "target": "radio", "value": opt_val or "option"}

    if any(k in sub_lower for k in ["press", "hit enter"]):
        return {"action": "press", "target": "", "value": "Enter"}

    if any(k in sub_lower for k in ["click", "next", "submit"]):
        cleaned_target = subtask.lower().replace("click on", "").replace("click", "").strip()
        return {"action": "click", "target": cleaned_target, "value": ""}

    return {"action": "read", "target": "body", "value": ""}
