#!/usr/bin/env python3
"""Import a CrewAI-Studio single-crew JSON export into the configured database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


def load_crew(path: Path) -> dict[str, Any]:
    """Load and validate a single-crew JSON payload."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError(f"{path} is not a CrewAI-Studio single-crew JSON export")
    return data


def create_tables(engine_url: str) -> None:
    """Create the CrewAI-Studio entity table if it does not exist."""
    engine = create_engine(engine_url, echo=False)
    create_sql = text(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT,
            data TEXT
        )
        """
    )
    with engine.connect() as connection:
        connection.execute(create_sql)
        connection.commit()


def upsert_entities(engine_url: str, entities: list[tuple[str, str, dict[str, Any]]]) -> None:
    """Upsert CrewAI-Studio entities by id."""
    engine = create_engine(engine_url, echo=False)
    upsert_sql = text(
        """
        INSERT INTO entities (id, entity_type, data)
        VALUES (:id, :etype, :data)
        ON CONFLICT(id) DO UPDATE
            SET entity_type = EXCLUDED.entity_type,
                data = EXCLUDED.data
        """
    )
    with engine.connect() as connection:
        for entity_id, entity_type, payload in entities:
            connection.execute(
                upsert_sql,
                {"id": entity_id, "etype": entity_type, "data": json.dumps(payload)},
            )
        connection.commit()


def crew_to_entities(crew: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Convert one CrewAI-Studio crew export into database entity rows."""
    entities: list[tuple[str, str, dict[str, Any]]] = []

    for tool in crew.get("tools", []):
        entities.append(
            (
                tool["tool_id"],
                "tool",
                {
                    "name": tool["name"],
                    "description": tool.get("description", tool["name"]),
                    "parameters": tool.get("parameters", {}),
                },
            )
        )

    for agent in crew.get("agents", []):
        entities.append(
            (
                agent["id"],
                "agent",
                {
                    "created_at": agent.get("created_at", crew.get("created_at")),
                    "role": agent["role"],
                    "backstory": agent["backstory"],
                    "goal": agent["goal"],
                    "allow_delegation": agent.get("allow_delegation", False),
                    "verbose": agent.get("verbose", True),
                    "cache": agent.get("cache", True),
                    "llm_provider_model": agent["llm_provider_model"],
                    "temperature": agent.get("temperature", 0.1),
                    "max_iter": agent.get("max_iter", 25),
                    "tool_ids": agent.get("tool_ids", []),
                    "knowledge_source_ids": agent.get("knowledge_source_ids", []),
                },
            )
        )

    for task in crew.get("tasks", []):
        entities.append(
            (
                task["id"],
                "task",
                {
                    "description": task["description"],
                    "expected_output": task["expected_output"],
                    "async_execution": task.get("async_execution", False),
                    "agent_id": task.get("agent_id"),
                    "context_from_async_tasks_ids": task.get("context_from_async_tasks_ids"),
                    "context_from_sync_tasks_ids": task.get("context_from_sync_tasks_ids"),
                    "created_at": task.get("created_at", crew.get("created_at")),
                },
            )
        )

    entities.append(
        (
            crew["id"],
            "crew",
            {
                "name": crew["name"],
                "process": crew.get("process", "sequential"),
                "verbose": crew.get("verbose", True),
                "agent_ids": [agent["id"] for agent in crew.get("agents", [])],
                "task_ids": [task["id"] for task in crew.get("tasks", [])],
                "memory": crew.get("memory", False),
                "cache": crew.get("cache", True),
                "planning": crew.get("planning", False),
                "planning_llm": crew.get("planning_llm"),
                "max_rpm": crew.get("max_rpm", 1000),
                "manager_llm": crew.get("manager_llm"),
                "manager_agent_id": crew.get("manager_agent"),
                "created_at": crew.get("created_at"),
                "knowledge_source_ids": crew.get("knowledge_source_ids", []),
            },
        )
    )

    return entities


def main() -> int:
    """Import the requested CrewAI-Studio JSON export."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path)
    args = parser.parse_args()

    engine_url = os.getenv("DB_URL", "sqlite:///crewai.db")
    crew = load_crew(args.json_path)
    create_tables(engine_url)
    entities = crew_to_entities(crew)
    upsert_entities(engine_url, entities)
    print(f"Imported crew '{crew['name']}' with {len(entities)} entities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())