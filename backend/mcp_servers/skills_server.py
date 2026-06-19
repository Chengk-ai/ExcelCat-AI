"""
excelcat-skills — MCP server owning the skill registry + contracts.

Phase "Skills" of the MCP refactor. Skills are the product's user-facing specs
(markdown files in backend/skills/). This server owns which tool names are
skills and serves their markdown content. It deliberately does NOT call the LLM:
in MCP terms a server provides a capability/resource (the skill contract), and
the client (main.py) does the LLM orchestration — it injects the returned
markdown into its own model call. Pure file I/O, no audit, no secrets.

No print() — stdout is the stdio JSON-RPC channel and must not be written to.
"""
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

# backend/skills (this file lives in backend/mcp_servers/).
_SKILLS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills"
)

# Text skills: produce a written answer, no cell write.
SKILL_FILE_MAP = {
    "summarise_data": "summarise.md",
    "clean_data": "clean_data.md",
    "find_outliers": "find_outliers.md",
    "analyse_data": "analyse_data.md",
}

# Action skills: the skill file guides a structured action (a cell write) rather
# than a text answer. Kept separate so they do NOT take the text-only return path.
ACTION_SKILL_FILE_MAP = {
    "forecast_data": "forecast.md",
}

mcp = FastMCP("excelcat-skills")


def _read(filename: str) -> Optional[str]:
    path = os.path.join(_SKILLS_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return None


@mcp.tool()
def get_skill(tool_name: str) -> dict:
    """Return a skill's markdown contract and its kind.

    Returns {kind, filename, content} where kind is "text" | "action" | None.
    content is None if the tool isn't a skill or its file is missing.
    """
    if tool_name in SKILL_FILE_MAP:
        filename = SKILL_FILE_MAP[tool_name]
        return {"kind": "text", "filename": filename, "content": _read(filename)}
    if tool_name in ACTION_SKILL_FILE_MAP:
        filename = ACTION_SKILL_FILE_MAP[tool_name]
        return {"kind": "action", "filename": filename, "content": _read(filename)}
    return {"kind": None, "filename": None, "content": None}


@mcp.tool()
def list_skills() -> dict:
    """Skill tool-name registry, for the client's routing cache.

    Returns {"text": [...], "action": [...]}.
    """
    return {
        "text": list(SKILL_FILE_MAP.keys()),
        "action": list(ACTION_SKILL_FILE_MAP.keys()),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
