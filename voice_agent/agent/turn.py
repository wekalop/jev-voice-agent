"""One caller turn: identity, Jev decisions, knowledge routing, gates, and the streamed reply.

The replay benchmark and the live voice mode both call stream_turn, so they test the same logic.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from agent import decisions, prompts
from agent.config import Settings
from agent.identity import IdentityState, absorb
from agent.jev import JevClient
from agent.kb import KnowledgeBase, select_files
from agent.providers.llm import LLMStats, OpenAICompatibleLLM
from agent.timing import TurnTiming

MODES = ("one_call", "two_call", "two_call_parallel")
# These are handled without knowledge, so the answerable gate does not apply to them.
NO_GATE_INTENTS = {"chitchat", "human_request"}
MAX_HISTORY_MESSAGES = 12
MAX_FILES = 2
MAX_SENTENCES = 3
# A sentence ends at punctuation and a space, but not after a.m. or p.m.
SENTENCE_END = re.compile(r"(?<![ap]\.m)[.!?](?=\s)")
# Intents that never need an account check, even in the middle of one.
NO_ACCOUNT_INTENTS = {"chitchat", "human_request", "out_of_scope"}
DETAILS_PLACEHOLDER = "[The caller gave identity details.]"


@dataclass
class Session:
    identity: IdentityState = field(default_factory=IdentityState)
    history: list[dict] = field(default_factory=list)
    pending_question: str | None = None
    pending_level: int = 0
    last_files: list[str] = field(default_factory=list)


@dataclass
class Agent:
    settings: Settings
    kb: KnowledgeBase
    jev: JevClient
    llm: OpenAICompatibleLLM
    # Identity extraction uses the small model on Groq, whichever LLM writes the replies.
    extractor: OpenAICompatibleLLM
    mode: str = "two_call"


def _state(session: Session, text: str, **extra) -> dict:
    messages = [
        {"from": "customer" if m["role"] == "user" else "agent", "text": m["content"]}
        for m in session.history[-MAX_HISTORY_MESSAGES:]
    ]
    messages.append({"from": "customer", "text": text})
    return {"messages": messages, **extra}


def _knowledge_state(agent: Agent, files: list[str], customer_view: dict | None) -> dict:
    knowledge = {name: agent.kb.reply_files[name] for name in files}
    knowledge["support_policy.md"] = agent.kb.files["support_policy.md"]
    return {
        "knowledge": knowledge,
        "account_data": customer_view or "Available once the caller passes identity checks.",
    }


def _remember(session: Session, caller: str, agent_reply: str) -> None:
    session.history += [{"role": "user", "content": caller}, {"role": "assistant", "content": agent_reply}]


async def stream_turn(
    agent: Agent, session: Session, text: str, timing: TurnTiming, voice_cues: str | None = None
) -> AsyncIterator[str]:
    """Yields reply text as it is ready. Nothing is yielded until the gates have passed."""
    settings = agent.settings
    identity = session.identity
    timing.values.update(mode=agent.mode, jev_model=agent.jev.model, utterance=text)

    decide_text = text
    history_text = text

    # A check is in progress, so read the details first. Ordinary turns skip this.
    if session.pending_question is not None:
        failures_before = identity.failures
        gave_details = await absorb(identity, text, agent.kb, agent.extractor, settings.classifier_model)
        timing.mark("t_identity_done")
        if gave_details:
            history_text = DETAILS_PLACEHOLDER
        if identity.level >= session.pending_level:
            decide_text = session.pending_question
            history_text = f"[Identity confirmed.] {session.pending_question}"
            session.pending_question = None
        elif identity.locked:
            session.pending_question = None
            _remember(session, history_text, prompts.LOCKED)
            timing.values["reply_path"] = "fixed_identity"
            yield prompts.LOCKED
            return
        elif gave_details:
            # Still incomplete or wrong. No Jev call is needed to ask again, and no hint is given.
            reply = _next_identity_prompt(session, identity.failures > failures_before, progress=True)
            _remember(session, history_text, reply)
            timing.values["reply_path"] = "fixed_identity"
            yield reply
            return

    # First Jev call. Nothing else can start until it returns, because it picks the files.
    routing_state = _state(session, decide_text, knowledge_topics=agent.kb.topics) if agent.mode == "one_call" else _state(session, decide_text)
    if voice_cues:
        routing_state["voice_cues"] = voice_cues
        timing.values["voice_cues"] = voice_cues
    first = await agent.jev.decide(
        routing_state,
        decisions.routing_questions(include_answerable=agent.mode == "one_call", with_voice=bool(voice_cues)),
    )
    decision = decisions.parse_decision(first.answers)
    timing.mark("t_jev_routing_done")
    timing.values.update(
        jev_routing_ms=round(first.latency_ms, 1),
        jev_input_tokens=first.usage.get("input_tokens", 0),
        jev_backoff_ms=first.backoff_ms,
        jev_model_reported=first.model,
    )

    hand_off = decision.p_needs_human >= settings.threshold_needs_human or decision.intent == "human_request"
    other_person = decision.p_about_another_person >= 0.5

    # The caller moved on to something that does not need the account, so drop the check.
    if session.pending_question is not None and (
        other_person or hand_off or decision.intent in NO_ACCOUNT_INTENTS or decision.p_needs_account_access < 0.5
    ):
        session.pending_question = None

    # Asking for a person gets a straight answer, with no check and no LLM.
    if decision.intent == "human_request":
        timing.mark("t_jev_done")
        timing.values.update(decision=decision.as_dict(), reply_path="fixed_human", hand_off=True)
        _remember(session, history_text, prompts.HUMAN_REQUEST)
        yield prompts.HUMAN_REQUEST
        return

    # Someone else's details are refused, so no check is asked for and their words are never read as identity.
    required = 0 if other_person else _required_level(decision, hand_off)
    if required > identity.level and not identity.locked and session.pending_question is None:
        # The first message may already hold the details, so read it before asking.
        session.pending_question = decide_text
        session.pending_level = required
        gave_details = await absorb(identity, text, agent.kb, agent.extractor, settings.classifier_model)
        timing.mark("t_identity_done")
        if identity.level >= required:
            session.pending_question = None
        else:
            timing.mark("t_jev_done")
            timing.values.update(decision=decision.as_dict(), reply_path="fixed_identity", gave_details=gave_details)
            reply = _next_identity_prompt(session, False, progress=False)
            _remember(session, text, reply)
            yield reply
            return
    elif required > identity.level and session.pending_question is not None:
        timing.mark("t_jev_done")
        timing.values.update(decision=decision.as_dict(), reply_path="fixed_identity")
        reply = _next_identity_prompt(session, False, progress=True)
        _remember(session, history_text, reply)
        yield reply
        return
    elif required > identity.level and identity.locked:
        timing.mark("t_jev_done")
        timing.values.update(decision=decision.as_dict(), reply_path="fixed_identity")
        _remember(session, history_text, prompts.LOCKED)
        yield prompts.LOCKED
        return

    files = select_files(decision.intent_probabilities, decision.intent, decision.second_topic)
    # A short follow-up, such as "and how much is that?", still needs the file from the last answer.
    if decision.intent in NO_GATE_INTENTS or decision.intent == "out_of_scope" or decision.intent_confidence < 0.7:
        files += [name for name in session.last_files if name not in files]
    files = files[:MAX_FILES]
    customer = agent.kb.customer_by_id(identity.customer_id) if identity.customer_id else None
    customer_view = agent.kb.customer_view(customer) if customer and not other_person else None
    timing.values.update(files=files, hand_off=hand_off, identity_level=identity.level)

    gate_applies = not hand_off and not other_person and decision.intent not in NO_GATE_INTENTS

    second_task = None
    if gate_applies and agent.mode != "one_call":
        second_task = asyncio.create_task(agent.jev.decide(
            _state(session, decide_text, **_knowledge_state(agent, files, customer_view)),
            {"answerable_from_kb": decisions.answerable_question()},
        ))

    async def gate_passes() -> bool:
        """Waits for the answerable decision. False means the decline line replaces the reply."""
        if second_task is not None:
            second = await second_task
            decision.p_answerable = second.answers["answerable_from_kb"]["noul"]
            timing.values.update(
                jev_answerable_ms=round(second.latency_ms, 1),
                jev_input_tokens=timing.values["jev_input_tokens"] + second.usage.get("input_tokens", 0),
                jev_backoff_ms=timing.values["jev_backoff_ms"] + second.backoff_ms,
            )
        timing.mark("t_jev_done")
        timing.values["decision"] = decision.as_dict()
        if not gate_applies or decision.p_answerable is None:
            return True
        return decision.p_answerable >= settings.threshold_answerable

    def build(knowledge_files: list[str], not_covered: bool = False) -> list[dict]:
        return prompts.build_messages(
            settings.now_text,
            agent.kb.files["support_policy.md"],
            {name: agent.kb.reply_files[name] for name in knowledge_files},
            decision,
            customer_view,
            identity.level,
            hand_off,
            session.history[-MAX_HISTORY_MESSAGES:],
            not_covered=not_covered,
            other_person=other_person,
            voice_cues=voice_cues,
        )

    session.history.append({"role": "user", "content": history_text})
    session.last_files = files
    stats = LLMStats()

    if agent.mode != "two_call_parallel" and not await gate_passes():
        timing.values["reply_path"] = "decline"
        async for piece in _write_decline(agent, session, build([], not_covered=True), stats, timing):
            yield piece
        return

    messages = build(files)
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for delta in agent.llm.stream(messages, stats):
                timing.mark("t_llm_first_token")
                await queue.put(delta)
            await queue.put(None)
        except Exception as exc:  # Passed to the consumer so that it surfaces in the turn.
            await queue.put(exc)

    pump_task = asyncio.create_task(pump())
    reply: list[str] = []
    try:
        # In parallel mode the LLM has already started, and the gate holds back its output.
        if agent.mode == "two_call_parallel" and not await gate_passes():
            timing.values["reply_path"] = "decline_after_speculation"
            pump_task.cancel()
            timing.marks.pop("t_llm_first_token", None)
            async for piece in _write_decline(agent, session, build([], not_covered=True), stats, timing):
                yield piece
            return

        timing.values["reply_path"] = "llm"
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            piece, finished = _within_limit("".join(reply), item)
            reply.append(piece)
            yield piece
            if finished:
                break
    finally:
        pump_task.cancel()
        if second_task is not None and not second_task.done():
            second_task.cancel()
        # An interrupted reply is remembered as far as it got.
        if timing.values.get("reply_path") == "llm":
            session.history.append({"role": "assistant", "content": "".join(reply) or "(no reply)"})
        timing.values.update(
            llm_model=stats.model_used,
            llm_fell_back=stats.fell_back,
            rate_limit_wait_ms=stats.rate_limit_wait_ms,
            prompt_chars=sum(len(m["content"]) for m in messages),
        )


def _within_limit(so_far: str, delta: str) -> tuple[str, bool]:
    """Trims a reply at the sentence limit, because a phone answer that runs on is hard to listen to."""
    ends = list(SENTENCE_END.finditer(so_far + delta))
    if len(ends) < MAX_SENTENCES:
        return delta, False
    return (so_far + delta)[len(so_far) : ends[MAX_SENTENCES - 1].end()], True


async def _write_decline(agent: Agent, session: Session, messages: list[dict], stats: LLMStats, timing: TurnTiming) -> AsyncIterator[str]:
    """A decline written for this caller, with no knowledge to draw on. Falls back to a fixed line if the LLM fails."""
    reply: list[str] = []
    try:
        async for delta in agent.llm.stream(messages, stats):
            timing.mark("t_llm_first_token")
            piece, finished = _within_limit("".join(reply), delta)
            reply.append(piece)
            yield piece
            if finished:
                break
    except Exception as exc:  # The fixed line is safe, so the caller still gets an answer.
        timing.values["error"] = f"{type(exc).__name__}: {exc}"
    if not reply:
        reply.append(prompts.DECLINE)
        yield prompts.DECLINE
    session.history.append({"role": "assistant", "content": "".join(reply)})
    timing.values.update(llm_model=stats.model_used, llm_fell_back=stats.fell_back, rate_limit_wait_ms=stats.rate_limit_wait_ms)


def _required_level(decision: decisions.Decision, hand_off: bool) -> int:
    """The identity level this request needs. A caller who needs a human is not held up by checks."""
    if hand_off or decision.intent in NO_ACCOUNT_INTENTS:
        return 0
    if decision.p_requests_account_change >= 0.5:
        return 2
    if decision.p_needs_account_access >= 0.5:
        return 1
    return 0


def _next_identity_prompt(session: Session, failed: bool, progress: bool) -> str:
    identity = session.identity
    if identity.level > 0:
        return prompts.ASK_FOR_BIRTH_DATE
    missing = identity.missing_for_level_one()
    if failed:
        return f"{prompts.NOT_MATCHED} {prompts.ask_again(missing)}"
    if identity.phone_partial:
        rest = [m for m in missing if "phone" not in m]
        ending = f" I also need {prompts.join_items(rest)}." if rest else ""
        return prompts.PARTIAL_PHONE + ending
    return prompts.ask_for_rest(missing) if progress else prompts.ask_for_identity(missing)
