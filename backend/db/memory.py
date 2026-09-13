"""
Hierarchical Vector Memory Layer:
- Per-Agent Memory: Orchestrator, Planner, and Verifier each have their own memory space
  to learn from their individual past successes and mistakes.
- Shared Global Memory: Stores cross-agent task summaries so all agents understand previous runs.
Backed by Supabase pgvector with in-memory local fallback.
"""
import os

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "all-MiniLM-L6-v2")

supabase = None
if SUPABASE_URL and SUPABASE_KEY and not SUPABASE_URL.startswith("https://xxxxxxxxxxxx"):
    try:
        from supabase import create_client
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        print(f"Supabase memory client init warning: {e}")

# In-memory stores for dev mode
_in_memory_agent_memory = []
_in_memory_task_runs = {}
_task_run_seq = 1

_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedder = SentenceTransformer(EMBED_MODEL)
        except Exception as e:
            print(f"SentenceTransformer not loaded ({e}). Using mock embeddings.")
            _embedder = "mock"
    return _embedder


def embed_text(text: str) -> list[float]:
    embedder = get_embedder()
    if embedder != "mock" and hasattr(embedder, "encode"):
        try:
            return embedder.encode(text, normalize_embeddings=True).tolist()
        except Exception as e:
            print(f"Embedding error: {e}")
    return [0.0] * 384


# --- 1. SPECIALIZED PER-AGENT MEMORY ---

def store_agent_memory(user_id: str, agent_name: str, content: str, success: bool = True, metadata: dict | None = None):
    """
    Stores an agent-specific learning (e.g. Orchestrator plan quality, Planner selector heuristic, Verifier criteria).
    """
    meta = metadata or {}
    meta["agent"] = agent_name
    meta["success"] = success

    vector = embed_text(content)
    db_user_id = None if (not user_id or user_id.startswith("00000000-") or "guest" in user_id) else user_id

    if supabase is not None:
        try:
            supabase.table("agent_memory").insert({
                "user_id": db_user_id,
                "task_type": f"{agent_name}_memory",
                "content": content,
                "metadata": meta,
                "embedding": vector,
            }).execute()
            return
        except Exception as e:
            print(f"Supabase store_agent_memory error: {e}. Storing locally.")

    _in_memory_agent_memory.append({
        "user_id": user_id,
        "task_type": f"{agent_name}_memory",
        "agent": agent_name,
        "content": content,
        "metadata": meta,
        "embedding": vector,
    })


def retrieve_agent_memory(user_id: str, agent_name: str, query: str, top_k: int = 3) -> list[dict]:
    """
    Retrieves past learnings specific to this agent (e.g., how the Planner handled YouTube search or quiz next buttons).
    """
    query_vector = embed_text(query)
    db_user_id = None if (not user_id or user_id.startswith("00000000-") or "guest" in user_id) else user_id

    if supabase is not None:
        try:
            result = supabase.rpc("match_memory", {
                "query_embedding": query_vector,
                "match_user_id": db_user_id,
                "match_count": top_k * 2,
            }).execute()
            if result.data:
                # Filter for this specific agent's memory
                filtered = [
                    m for m in result.data 
                    if m.get("metadata", {}).get("agent") == agent_name or m.get("task_type") == f"{agent_name}_memory"
                ]
                return filtered[:top_k]
        except Exception as e:
            print(f"Supabase retrieve_agent_memory error: {e}. Falling back to local.")

    # Local in-memory search
    q_words = set(query.lower().split())
    matched = []
    for item in _in_memory_agent_memory:
        if item.get("agent") == agent_name or item.get("task_type") == f"{agent_name}_memory":
            c_words = set(item["content"].lower().split())
            intersection = len(q_words & c_words)
            sim = (intersection / max(1, len(q_words)))
            matched.append({
                "content": item["content"],
                "metadata": item["metadata"],
                "similarity": sim
            })
    matched.sort(key=lambda x: x["similarity"], reverse=True)
    return matched[:top_k]


# --- 2. SHARED GLOBAL TASK MEMORY ---

def store_global_memory(user_id: str, goal: str, outcome: str, verified: bool, steps: list[str]):
    """Stores full cross-agent task summary in shared global memory."""
    summary = f"Goal: {goal}\nSteps Executed: {' -> '.join(steps[:5])}\nOutcome: {outcome[:600]}\nVerified: {verified}"
    vector = embed_text(summary)
    db_user_id = None if (not user_id or user_id.startswith("00000000-") or "guest" in user_id) else user_id

    meta = {
        "agent": "global",
        "goal": goal,
        "verified": verified,
        "step_count": len(steps)
    }

    if supabase is not None:
        try:
            supabase.table("agent_memory").insert({
                "user_id": db_user_id,
                "task_type": "global_shared",
                "content": summary,
                "metadata": meta,
                "embedding": vector,
            }).execute()
            return
        except Exception as e:
            print(f"Supabase store_global_memory error: {e}. Storing locally.")

    _in_memory_agent_memory.append({
        "user_id": user_id,
        "task_type": "global_shared",
        "agent": "global",
        "content": summary,
        "metadata": meta,
        "embedding": vector,
    })


def retrieve_global_memory(user_id: str, query: str, top_k: int = 3) -> list[dict]:
    """Retrieves high-level past goals and cross-agent solutions."""
    query_vector = embed_text(query)
    db_user_id = None if (not user_id or user_id.startswith("00000000-") or "guest" in user_id) else user_id

    if supabase is not None:
        try:
            result = supabase.rpc("match_memory", {
                "query_embedding": query_vector,
                "match_user_id": db_user_id,
                "match_count": top_k * 2,
            }).execute()
            if result.data:
                filtered = [
                    m for m in result.data 
                    if m.get("task_type") == "global_shared" or m.get("metadata", {}).get("agent") == "global"
                ]
                return filtered[:top_k]
        except Exception as e:
            print(f"Supabase retrieve_global_memory error: {e}")

    q_words = set(query.lower().split())
    matched = []
    for item in _in_memory_agent_memory:
        if item.get("task_type") == "global_shared":
            c_words = set(item["content"].lower().split())
            intersection = len(q_words & c_words)
            sim = (intersection / max(1, len(q_words)))
            matched.append({
                "content": item["content"],
                "metadata": item["metadata"],
                "similarity": sim
            })
    matched.sort(key=lambda x: x["similarity"], reverse=True)
    return matched[:top_k]


# --- 3. BACKWARD-COMPATIBLE HELPERS ---

def store_memory(user_id: str, task_type: str, content: str, metadata: dict | None = None):
    meta = metadata or {}
    agent = meta.get("agent", "global")
    if agent != "global":
        store_agent_memory(user_id, agent, content, meta.get("verified", True), meta)
    else:
        store_global_memory(user_id, meta.get("goal", task_type), content, meta.get("verified", True), [])


def retrieve_memory(user_id: str, query: str, top_k: int = 5) -> list[dict]:
    return retrieve_global_memory(user_id, query, top_k)


# --- 4. TASK RUN LOGGING ---

def log_task_run(user_id: str, goal: str, status: str = "running") -> int:
    global _task_run_seq
    db_user_id = None if (not user_id or user_id.startswith("00000000-") or "guest" in user_id) else user_id
    if supabase is not None:
        try:
            res = supabase.table("task_runs").insert({
                "user_id": db_user_id, "goal": goal, "status": status
            }).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]["id"]
        except Exception as e:
            print(f"Supabase log_task_run error: {e}")

    run_id = _task_run_seq
    _task_run_seq += 1
    _in_memory_task_runs[run_id] = {
        "id": run_id, "user_id": user_id, "goal": goal, "status": status, "result": ""
    }
    return run_id


def update_task_run(run_id: int, status: str, result: str = ""):
    if supabase is not None:
        try:
            supabase.table("task_runs").update({
                "status": status, "result": result
            }).eq("id", run_id).execute()
            return
        except Exception as e:
            print(f"Supabase update_task_run error: {e}")

    if run_id in _in_memory_task_runs:
        _in_memory_task_runs[run_id]["status"] = status
        _in_memory_task_runs[run_id]["result"] = result
