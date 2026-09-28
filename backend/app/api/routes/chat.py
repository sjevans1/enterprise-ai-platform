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
from app.core.audit import AuditEventType, audit_context
from app.db.connection import get_db
from app.db.models import Conversation, Message
from app.providers.factory import create_provider, get_default_provider

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
    from app.providers.base import ProcessingLocation
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

    # Build messages for provider
    messages_for_provider: list[dict] = []

    # Load historical messages explicitly (avoids async lazy-load errors)
    # Only load for existing conversations, not freshly created ones
    if chat_request.conversation_id:
        msg_stmt = select(Message).where(
            Message.conversation_id == conv_id,
        ).order_by(Message.created_at)
        msg_result = await db.execute(msg_stmt)
        for msg in msg_result.scalars().all():
            messages_for_provider.append({
                "role": msg.role,
                "content": msg.content,
            })

    # Add the new user message
    user_msg_content = chat_request.messages[-1].content if chat_request.messages else ""
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
    messages_for_provider.append({"role": "user", "content": user_msg_content})

    # Get model capabilities
    caps = await provider.get_capabilities()
    if not caps.verified:
        raise HTTPException(
            status_code=503,
            detail="Provider capabilities not verified. Check provider configuration.",
        )

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
        )

    # Stream or non-stream response
    if chat_request.stream:
        return await _stream_chat(
            provider, messages_for_provider, conversation, db, current_user,
            request, audit, caps,
        )
    else:
        return await _non_stream_chat(
            provider, messages_for_provider, conversation, db, current_user,
            request, audit, caps,
        )


async def _non_stream_chat(
    provider, messages, conversation, db, current_user,
    request, audit, caps,
):
    """Non-streaming chat completion."""
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
            )

        from app.api.schemas.common import AnswerRecord, Citation
        return ChatResponse(
            response_type="content",
            delta=None,
            answer=AnswerRecord(
                final_answer=result.content,
                completion_status="completed",
                evidence=[],
                assumptions=[],
                truncation_info=None,
                processing_location=result.processing_location.value,
                model_identity=result.model or provider.config.model,
                as_of_time=datetime.now(timezone.utc),
                execution_timestamp=datetime.now(timezone.utc),
            ),
            finish_reason=result.finish_reason,
            message=ChatMessage(role="assistant", content=result.content),
        )
    except Exception as e:
        from app.providers.base import ProviderError
        if isinstance(e, ProviderError):
            raise HTTPException(
                status_code=502,
                detail=f"Provider error: {e.code}: {e.message}",
            )
        raise HTTPException(
            status_code=500,
            detail="Error processing chat request",
        )


async def _stream_chat(
    provider, messages, conversation, db, current_user,
    request, audit, caps,
):
    """Streaming chat completion."""
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

            yield f"data: {json.dumps({'response_type': 'done'})}\n\n"

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


from app.core.config import settings
