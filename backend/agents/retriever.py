from db.memory import retrieve_memory


def get_context(user_id: str, goal: str) -> str:
    """RAG step: fetch relevant past learnings/site notes for this goal."""
    memories = retrieve_memory(user_id, goal, top_k=5)
    if not memories:
        return "No prior memory for this task."
    lines = [f"- {m['content']} (similarity: {m['similarity']:.2f})" for m in memories]
    return "Relevant past notes:\n" + "\n".join(lines)
