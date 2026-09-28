"""Chat routes: conversation management and streaming responses.

The browser calls this API. It must not receive provider keys, database
credentials, or unrestricted access to Hermes/Ollama.

The orchestrator calls the ModelProvider (via factory), retrieval service,
and query service as needed. These are cooperating services, not a mandatory
serial pipeline.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user
from app.api.schemas.common import ChatCompletionRequest, ChatMessage, ChatResponse, AnswerRecord, Citation
from app.auth.service import auth_service
from app.core.audit import AuditEventType, audit_context
from app.core.config import settings
from app.db.connection import get_db
from app.db.models import Conversation, Message
from app.orchestrator import get_orchestrator
from app.providers.factory import create_provider, get_default_provider
from app.providers.base import ProcessingLocation, ProviderError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


@router.get("/conversations")
async def list_conversations(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's conversations (not deleted)."""
    stmt = (
        select(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.is_deleted.is_(False),
        )
        .order_by(Conversation.updated_at.desc())
    )
    result = await db.execute(stmt)
    conversations = result.scalars().all()
    return [
        {
            "id": c.id,
            "title": c.title or "Untitled",
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
            "model_provider": c.model_provider,
            "model_name": c.model_name,
            "processing_location": c.processing_location,
        }
        for c in conversations
    ]


def _build_system_prompt(execution_class, evidence_text: str | None = None) -> str:
    """Build a system prompt based on the execution class and available evidence."""
    base = "You are OpenJM Enterprise AI, a governed enterprise assistant. Answer questions based on evidence when available, and cite your sources."

    if evidence_text:
        base += chr(10) + chr(10) + "=== Context Evidence ===" + chr(10)
        base += evidence_text + chr(10)
        base += "=== End Evidence ===" + chr(10) + chr(10)
        base += "When answering, cite specific evidence. If no relevant evidence is found, say so."

    exec_val = execution_class.value
    if exec_val == "structured":
        base += chr(10) + chr(10) + "This request is classified as Structured Data: answer only from the provided database evidence. Do not hallucinate values."
    elif exec_val == "knowledge":
        base += chr(10) + chr(10) + "This request is classified as Knowledge: ground your answer in the provided document evidence with citations."
    elif exec_val == "hybrid":
        base += chr(10) + chr(10) + "This request is classified as Hybrid: combine both document and structured data evidence. Cite each evidence source."
    elif exec_val == "general":
        base += chr(10) + chr(10) + "This request is classified as General: no enterprise evidence retrieval is required."

    return base


@router.post("/conversations")
async def create_conversation(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    title: str | None = None,
):
    """Create a new conversation."""
    conv_id = str(uuid.uuid4())
    conv = Conversation(
        id=conv_id,
        user_id=current_user.id,
        title=title or "New Conversation",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(conv)
    await db.commit()
    return {"conversation_id": conv_id, "title": conv.title}


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return persisted messages for one conversation owned by the caller."""
    stmt = (
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted.is_(False),
        )
    )
    result = await db.execute(stmt)
    conversation = result.scalars().first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "conversation_id": conversation.id,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content or "",
                "created_at": msg.created_at.isoformat(),
            }
            for msg in conversation.messages
            if msg.role in ("user", "assistant")
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a conversation."""
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == current_user.id,
        Conversation.is_deleted.is_(False),
    )
    result = await db.execute(stmt)
    conv = result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv.is_deleted = True
    conv.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Conversation deleted"}


@router.post("/chat")
async def chat_completion(
    request: Request,
    chat_request: ChatCompletionRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Chat completion endpoint.

    Uses the provider abstraction (ModelProvider). The provider is selected
    via configuration, not by the client. Provider keys are server-side only.
    """
    # Determine provider
    provider_name = chat_request.provider or "default"
    if provider_name == "default":
        provider = get_default_provider()
    else:
        provider = create_provider(provider_name)

    # Check privacy policy: reject remote in local-only mode
    location = provider.get_processing_location()
    if location != ProcessingLocation.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400,
            detail=f"Provider '{provider_name}' is not local (REMOTE_MODEL_ALLOWED=false)",
        )

    # Get or create conversation
    conv_id = chat_request.conversation_id
    if conv_id:
        stmt = (
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(
                Conversation.id == conv_id,
                Conversation.user_id == current_user.id,
            )
        )
        result = await db.execute(stmt)
        conversation = result.scalars().first()
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        conv_id = str(uuid.uuid4())
        conversation = Conversation(
            id=conv_id,
            user_id=current_user.id,
            title="New Conversation",
            model_provider=provider.config.provider,
            model_name=provider.config.model,
            processing_location=location.value,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(conversation)
        await db.flush()

    # Build messages for provider — integrate orchestrator for evidence
    user_msg_content = chat_request.messages[-1].content if chat_request.messages else ""
    user_permissions = list(await auth_service.get_user_permission_codes(db, current_user))

    # Run the orchestrator: classify intent + retrieve evidence (RAG + SQL)
    from app.orchestrator import ExecutionClass, truncate_context

    # Load conversation history for context
    if conv_id and chat_request.conversation_id:
        context_messages = [{"role": msg.role, "content": msg.content}
                           for msg in conversation.messages]
    else:
        context_messages = []

    # Classify and retrieve evidence
    plan = await get_orchestrator().plan(
        user_message=user_msg_content,
        user_permissions=user_permissions,
        db=db,
        has_documents=True,
        user_id=current_user.id,
    )

    # Build grounded context: conversation history + evidence
    context_messages = truncate_context(context_messages, plan.max_context_tokens)

    # Inject evidence as system context
    evidence_parts: list[str] = []
    all_citations: list[Citation] = []

    if plan.knowledge_evidence:
        evidence_parts.append("=== Knowledge Evidence (from documents) ===")
        for cite in plan.knowledge_evidence:
            all_citations.append(cite)
            evidence_parts.append(f"- {cite.citation}: {cite.passage or cite.title}")
        evidence_parts.append("---")
    elif plan.knowledge_plan and plan.knowledge_plan.get("mode") == "catalog":
        evidence_parts.append("=== Authorized Document Catalog ===")
        evidence_parts.append("No documents are currently available to this user.")
        evidence_parts.append("---")

    if plan.structured_evidence:
        evidence_parts.append("=== Structured Data Evidence ===")
        for cite in plan.structured_evidence:
            all_citations.append(cite)
            evidence_parts.append(f"- {cite.citation}: {cite.passage}")
        evidence_parts.append("---")

    # Build the system prompt with evidence and instructions
    system_prompt = _build_system_prompt(
        execution_class=plan.execution_class,
        evidence_text="\n".join(evidence_parts) if evidence_parts else None,
    )

    # Build messages for provider: system prompt + context + user message
    messages_for_provider: list[dict] = []
    if system_prompt:
        messages_for_provider.append({"role": "system", "content": system_prompt})
    # Add conversation history (user/assistant turns)
    for msg in context_messages:
        if msg["role"] in ("user", "assistant"):
            messages_for_provider.append(msg)
    # Add the new user message
    messages_for_provider.append({"role": "user", "content": user_msg_content})

    # Save user message
    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role="user",
        content=user_msg_content,
        model_provider=None,
        model_name=None,
        processing_location=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user_msg)

    # Audit log
    async with audit_context(db) as audit:
        await audit.log(
            AuditEventType.PROVIDER_REQUEST,
            user_id=current_user.id,
            conversation_id=conv_id,
            provider=provider.config.provider,
            model=provider.config.model,
            processing_location=location.value,
            outcome="started",
            details={
                "execution_class": plan.execution_class.value,
                "evidence_count": len(all_citations),
            },
        )

    # Get model capabilities
    caps = await provider.get_capabilities()
    if not caps.verified:
        raise HTTPException(
            status_code=503,
            detail="Provider capabilities not verified. Check provider configuration.",
        )

    # Stream or non-stream response
    if chat_request.stream:
        return await _stream_chat(
            provider, messages_for_provider, conversation, db, current_user,
            request, audit, caps, plan, all_citations,
        )
    else:
        return await _non_stream_chat(
            provider, messages_for_provider, conversation, db, current_user,
            request, audit, caps, plan, all_citations,
        )


async def _non_stream_chat(
    provider, messages, conversation, db, current_user,
    request, audit, caps, plan, all_citations,
):
    """Non-streaming chat completion with orchestrator evidence."""
    try:
        result = await provider.chat(
            messages,
            temperature=settings.default_temperature,
            max_tokens=settings.default_max_tokens,
            stream=False,
        )

        # Save assistant message
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            content=result.content,
            model_provider=provider.config.provider,
            model_name=result.model or provider.config.model,
            processing_location=result.processing_location.value,
            token_count=(result.usage.completion_tokens if result.usage else None),
            finish_reason=result.finish_reason,
            created_at=datetime.now(timezone.utc),
        )
        db.add(assistant_msg)
        await db.commit()

        # Audit
        async with audit_context(db) as audit:
            await audit.log(
                AuditEventType.CHAT_MESSAGE,
                user_id=current_user.id,
                conversation_id=conversation.id,
                provider=provider.config.provider,
                model=result.model or provider.config.model,
                processing_location=result.processing_location.value,
                outcome="success",
                details={
                    "evidence_count": len(all_citations),
                    "execution_class": plan.execution_class.value if plan else "unknown",
                },
            )

        return ChatResponse(
            response_type="content",
            delta=None,
            answer=AnswerRecord(
                final_answer=result.content,
                completion_status="completed",
                evidence=all_citations,
                assumptions=[],
                truncation_info=None,
                processing_location=result.processing_location.value,
                model_identity=result.model or provider.config.model,
                as_of_time=datetime.now(timezone.utc),
                execution_timestamp=datetime.now(timezone.utc),
            ),
            finish_reason=result.finish_reason,
            message=ChatMessage(role="assistant", content=result.content),
            conversation_id=conversation.id,
        )
    except ProviderError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Provider error: {e.code}: {e.message}",
        )
    except Exception as e:
        logger.error(f"Chat completion failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Error processing chat request",
        )


async def _stream_chat(
    provider, messages, conversation, db, current_user,
    request, audit, caps, plan, all_citations,
):
    """Streaming chat completion with orchestrator evidence."""
    async def generate():
        full_content = ""
        try:
            stream = await provider.chat(
                messages,
                temperature=settings.default_temperature,
                max_tokens=settings.default_max_tokens,
                stream=True,
            )
            async for chunk in stream:
                yield f"data: {json.dumps({'response_type': 'content', 'delta': chunk.content_delta, 'finish_reason': chunk.finish_reason})}\n\n"
                full_content += chunk.content_delta
                if chunk.finish_reason:
                    break

            # Send evidence with the done event
            evidence_data = [
                {"source_type": c.source_type, "source_id": c.source_id,
                 "title": c.title or "", "passage": c.passage,
                 "citation": c.citation, "confidence": c.confidence}
                for c in all_citations
            ]
            yield f"data: {json.dumps({'response_type': 'done', 'evidence': evidence_data, 'conversation_id': conversation.id})}\n\n"

            # Save assistant message
            assistant_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                role="assistant",
                content=full_content,
                model_provider=provider.config.provider,
                model_name=provider.config.model,
                processing_location=provider.get_processing_location().value,
                created_at=datetime.now(timezone.utc),
            )
            db.add(assistant_msg)
            await db.commit()
        except Exception as e:
            yield f"data: {json.dumps({'response_type': 'error', 'message': f'Error: {type(e).__name__}: {str(e)}'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")

