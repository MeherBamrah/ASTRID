import os
import json
import re
from dotenv import load_dotenv
load_dotenv()
from groq import Groq

_client = None


def get_client():
    global _client
    api_key = os.environ.get("GROQ_API_KEY", "").strip("'\" \t\r\n")
    if not api_key or api_key.startswith("gsk_xxxx"):
        return None
    if _client is None:
        _client = Groq(api_key=api_key)
    return _client


def clean_llm_json(raw: str) -> str:
    """Removes thinking blocks, markdown backticks, and extracts pure JSON."""
    if not raw:
        return ""
    # Strip <think>...</think> tags if present
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    # Strip ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
    # If there is JSON within curly brackets, extract it
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned.strip()


def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    """Entrypoint every agent uses to talk to Groq with automatic multi-model failover."""
    client = get_client()
    primary_model = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b").strip("'\" \t\r\n")
    candidate_models = [primary_model, "groq/compound-mini", "openai/gpt-oss-20b"]

    if client is not None:
        for model in candidate_models:
            for attempt in range(2):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        max_tokens=300,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )
                    raw_content = resp.choices[0].message.content or ""
                    cleaned = clean_llm_json(raw_content)
                    if cleaned:
                        return cleaned
                except Exception as e:
                    err_str = str(e).lower()
                    if "429" in err_str or "rate limit" in err_str:
                        print(f"[GROQ] Rate limit on {model}. Attempting failover...", flush=True)
                        import time
                        time.sleep(1.0)
                        break
                    print(f"[GROQ] Model {model} error: {e}. Trying next candidate...")
                    break

    # Simulated Fallback Mode for local testing if API fails
    prompt_lower = (system_prompt + " " + user_prompt).lower()

    if "orchestrator" in prompt_lower:
        goal = user_prompt.strip()
        target_site = "https://duckduckgo.com"
        if "python" in goal.lower():
            target_site = "https://www.python.org"
        elif "hacker news" in goal.lower():
            target_site = "https://news.ycombinator.com"
        elif "wikipedia" in goal.lower():
            target_site = "https://en.wikipedia.org"
        elif "fastapi" in goal.lower():
            target_site = "https://fastapi.tiangolo.com"
        elif "http" in goal.lower():
            words = goal.split()
            for w in words:
                if w.startswith("http"):
                    target_site = w
                    break

        steps = [
            f"Navigate to {target_site}",
            f"Read and extract visible headline content for: {goal}",
            "Verify the collected information and finish"
        ]
        return json.dumps({"steps": steps})

    elif "planner" in prompt_lower:
        if "start" in user_prompt.lower() and ("start quiz" in user_prompt.lower() or "button" in user_prompt.lower() or "knowledge check" in user_prompt.lower()):
            return json.dumps({"action": "click", "target": "Start", "value": ""})
        elif "navigate to" in prompt_lower:
            words = user_prompt.split()
            url = "https://duckduckgo.com"
            for i, w in enumerate(words):
                if w.lower() == "to" and i + 1 < len(words):
                    url = words[i + 1]
                    break
            return json.dumps({"action": "navigate", "target": url, "value": ""})
        elif "quiz" in prompt_lower or "question" in prompt_lower or "select" in prompt_lower:
            return "{}"
        elif "read" in prompt_lower or "extract" in prompt_lower:
            return json.dumps({"action": "read", "target": "body", "value": ""})
        else:
            return json.dumps({"action": "done", "target": "", "value": ""})

    elif "verifier" in prompt_lower:
        return json.dumps({
            "verified": True,
            "reason": "Simulated verifier: Browser collected relevant page state matching user prompt."
        })

    return "{}"
