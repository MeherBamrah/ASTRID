import os
import json
import re
from agents.llm import get_client

QUIZ_MEMORY_PATH = os.path.join(os.path.dirname(__file__), "quiz_memory.json")


def _normalize_q(q: str) -> str:
    """Normalizes a question text for robust fuzzy key matching, stripping noise, SCORM headers, and attached feedback."""
    if not q:
        return ""
    text = q
    # 1. Strip iframe/SCORM headers
    for marker in ["KNOWLEDGE CHECK QUIZ] ---", "QUIZ IFRAME] ---", "--- [ACTIVE"]:
        if marker in text:
            parts = text.split(marker)
            text = parts[-1]
            if text.startswith("] ---"):
                text = text[5:]
    
    # 2. Strip trailing metadata & LMS buttons & feedback
    for stop_word in [
        "available options:", "available buttons:", "selection type:",
        "\nincorrect", "\ncorrect", "keyboard navigation", "number correct:"
    ]:
        if stop_word in text.lower():
            text = re.split(re.escape(stop_word), text, flags=re.IGNORECASE)[0]
    
    # 3. Strip slide/question prefixes anywhere at start
    text = re.sub(r'^(?:\s*slide\s*:\s*)?(?:question\s*|q\s*)?\d+[\.\:\)]?\s*', '', text.strip(), flags=re.IGNORECASE)
    text = re.sub(r'^\s*slide\s*:\s*', '', text.strip(), flags=re.IGNORECASE)
    text = re.sub(r'^\s*(?:question\s*|q\s*)?\d+[\.\:\)]\s*', '', text.strip(), flags=re.IGNORECASE)

    # 4. Remove all forms of apostrophes/quotes so system's, system’s -> systems
    text = re.sub(r"['’‘`´\u2019\u2018\ufffd]", "", text)

    # 5. Normalize punctuation to space, collapse whitespace, lowercase
    text = re.sub(r'[^\w\s]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text[:140]


def load_quiz_memory() -> dict:
    if os.path.exists(QUIZ_MEMORY_PATH):
        try:
            with open(QUIZ_MEMORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_quiz_memory(memory: dict):
    try:
        with open(QUIZ_MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[QUIZ MEMORY] Error saving memory: {e}", flush=True)


def get_known_quiz_answer(question: str, options: list[str]) -> list[str] | None:
    """Checks if this question has a verified or extracted correct answer from past attempts."""
    if not question or not options:
        return None

    norm_q = _normalize_q(question)
    if not norm_q or len(norm_q) < 10:
        return None

    memory = load_quiz_memory()

    # Direct or fuzzy key search
    for key, data in memory.items():
        if key == norm_q or (len(key) > 25 and (key in norm_q or norm_q in key)):
            correct = data.get("correct_options", [])
            if not correct:
                continue

            # Validate against currently available options on the slide
            valid = []
            for c in correct:
                c_clean = c.lower().strip()
                for opt in options:
                    opt_clean = opt.lower().strip()
                    if c_clean == opt_clean or (len(c_clean) > 4 and (c_clean in opt_clean or opt_clean in c_clean)):
                        valid.append(opt)
                        break

            if valid:
                return list(dict.fromkeys(valid))
    return None


def get_known_incorrect_options(question: str) -> list[list[str]]:
    """Returns past attempts confirmed to be incorrect."""
    norm_q = _normalize_q(question)
    memory = load_quiz_memory()
    for key, data in memory.items():
        if key == norm_q or (len(key) > 25 and (key in norm_q or norm_q in key)):
            return data.get("incorrect_attempts", [])
    return []


def _extract_correct_via_llm(question: str, options: list[str], feedback_text: str) -> list[str]:
    """Uses Groq model to accurately extract revealed correct answers from quiz explanation text."""
    client = get_client()
    if not client:
        return []

    prompt = f"""Question: {question}
Available Options:
{json.dumps(options, indent=2)}

Quiz Feedback after submission:
\"\"\"{feedback_text[:600]}\"\"\"

Task: Based on the quiz feedback above, identify the true CORRECT option(s) strictly from the Available Options list.
Even if the student was marked Incorrect, the feedback explains what the correct choices are.
Respond ONLY with raw JSON:
{{"correct_options": ["exact option text from the list above"]}}"""

    for model_name in ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "qwen/qwen3.8-27b"]:
        try:
            r = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You extract verified correct quiz answers from quiz feedback."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=200
            )
            raw = r.choices[0].message.content or ""
            break
        except Exception:
            continue
    try:
        cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
        match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
        if match:
            data = json.loads(match.group(1).strip())
            extracted = data.get("correct_options", [])
            valid = []
            for e in extracted:
                for opt in options:
                    if e.lower().strip() == opt.lower().strip() or (len(e) > 5 and e.lower() in opt.lower()):
                        valid.append(opt)
                        break
            return list(dict.fromkeys(valid))
    except Exception as e:
        print(f"[QUIZ MEMORY] LLM feedback extraction warning: {e}", flush=True)
    return []


def record_quiz_outcome(question: str, options: list[str], selected: list[str], feedback_text: str):
    """Stores quiz outcome and extracts the true answer if revealed in feedback."""
    if not question or not options or not selected:
        return

    norm_q = _normalize_q(question)
    if not norm_q or len(norm_q) < 10:
        return

    memory = load_quiz_memory()
    entry = memory.get(norm_q, {
        "question": question.strip()[:200],
        "correct_options": [],
        "incorrect_attempts": [],
        "feedback": ""
    })

    fb_lower = (feedback_text or "").lower()
    is_correct = "correct" in fb_lower and not ("incorrect" in fb_lower or "not correct" in fb_lower or "wrong" in fb_lower)
    is_incorrect = "incorrect" in fb_lower or "not correct" in fb_lower or "wrong" in fb_lower

    if is_correct:
        entry["correct_options"] = selected
        entry["feedback"] = feedback_text[:300]
        print(f"\n[QUIZ MEMORY] Confirmed CORRECT answer: '{entry['question'][:50]}...' -> {selected}\n", flush=True)
    elif is_incorrect:
        if selected not in entry["incorrect_attempts"]:
            entry["incorrect_attempts"].append(selected)
        entry["feedback"] = feedback_text[:300]

        # Use fast LLM feedback parser to extract revealed answers
        revealed = _extract_correct_via_llm(question, options, feedback_text)
        if revealed:
            entry["correct_options"] = revealed
            print(f"\n[QUIZ MEMORY] Extracted REVEALED correct answer from feedback: {entry['correct_options']}\n", flush=True)
        else:
            print(f"\n[QUIZ MEMORY] Recorded INCORRECT attempt for: '{entry['question'][:50]}...' -> {selected}\n", flush=True)

    memory[norm_q] = entry
    save_quiz_memory(memory)
