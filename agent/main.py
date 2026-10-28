"""LiveKit Agents entrypoint.

    python -m agent.main console   # talk to it from your terminal (needs provider keys)
    python -m agent.main dev       # connect to a LiveKit room (needs LIVEKIT_* and provider keys)

The voice pipeline (STT, LLM, TTS, VAD) is wired here and nowhere else. The
tools and the booking backend never import a provider, so `pytest` runs
without any keys.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
from datetime import datetime
from pathlib import Path

from livekit.agents import Agent, AgentSession, JobContext, WorkerOptions, cli

from booking.service import BookingService

from .prompts import INSTRUCTIONS
from .tools import ALL_TOOLS, CallState
from .transcript import TranscriptLog

logging.basicConfig(level=logging.INFO)


class BookingAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS, tools=ALL_TOOLS)

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Greet the caller in one sentence and ask whether they want to book, move or cancel an appointment."
        )


def caller_phone_from(ctx: JobContext) -> str:
    """For a SIP call the caller's number is on the participant; otherwise use a demo number."""
    for participant in ctx.room.remote_participants.values():
        phone = participant.attributes.get("sip.phoneNumber")
        if phone:
            return phone
    return "+15550100"


def transcript_for(call_id: str) -> TranscriptLog:
    """One .jsonl per call under TRANSCRIPTS_DIR; in memory only when the variable is unset."""
    directory = os.environ.get("TRANSCRIPTS_DIR")
    if not directory:
        return TranscriptLog()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id)
    return TranscriptLog(path=Path(directory) / f"{safe}.jsonl")


async def entrypoint(ctx: JobContext) -> None:
    # Providers are imported here so the rest of the package stays key-free.
    from livekit.plugins import cartesia, deepgram, openai, silero

    await ctx.connect()
    backend = BookingService(clock=datetime.now)
    call_id = ctx.room.name or secrets.token_hex(6)
    state = CallState(
        backend=backend,
        caller_phone=caller_phone_from(ctx),
        call_id=call_id,
        transcript=transcript_for(call_id),
    )

    session: AgentSession[CallState] = AgentSession(
        userdata=state,
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(),
        vad=silero.VAD.load(),
    )
    await session.start(room=ctx.room, agent=BookingAgent())


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
