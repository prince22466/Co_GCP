"""Authenticated Cloud Run endpoint triggered by Cloud Scheduler."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from google.adk.runners import InMemoryRunner
from google.genai import types

from .agent import AGENT_VERSION, app as adk_app


service_app = FastAPI(title="Resource Manager ADK", docs_url=None, redoc_url=None)
runner = InMemoryRunner(app=adk_app)
evaluation_lock = asyncio.Lock()


@service_app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "agent_version": AGENT_VERSION}


def _event_payload(event: Any) -> tuple[str | None, object | None]:
    final_text: str | None = None
    tool_result: object | None = None
    content = getattr(event, "content", None)
    for part in getattr(content, "parts", None) or []:
        if getattr(part, "text", None):
            final_text = part.text
        response = getattr(part, "function_response", None)
        if response and response.name == "manage_mig_capacity":
            tool_result = response.response
    return final_text, tool_result


@service_app.post("/evaluate")
async def evaluate(request: Request) -> dict[str, object]:
    """Run one ADK turn; Cloud Run IAM authenticates the scheduler caller."""
    if evaluation_lock.locked():
        raise HTTPException(status_code=409, detail="An evaluation is already running")

    schedule_time = request.headers.get("X-CloudScheduler-ScheduleTime", "manual")
    user_id = "cloud-scheduler"
    session = None
    async with evaluation_lock:
        try:
            session = await runner.session_service.create_session(
                app_name=runner.app_name,
                user_id=user_id,
            )
            message = types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "Run one scheduled capacity evaluation now. Call "
                            "manage_mig_capacity exactly once, then summarize its result. "
                            f"Scheduler time: {schedule_time}. Invocation: {uuid.uuid4()}."
                        )
                    )
                ],
            )

            final_text: str | None = None
            tool_result: object | None = None
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session.id,
                new_message=message,
            ):
                event_text, event_tool_result = _event_payload(event)
                if event_text:
                    final_text = event_text
                if event_tool_result is not None:
                    tool_result = event_tool_result

            return {
                "status": "completed" if tool_result is not None else "safe_no_action",
                "agent_response": final_text,
                "tool_result": tool_result,
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Capacity evaluation failed") from exc
        finally:
            if session is not None:
                await runner.session_service.delete_session(
                    app_name=runner.app_name,
                    user_id=user_id,
                    session_id=session.id,
                )
