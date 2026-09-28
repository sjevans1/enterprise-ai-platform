from app.orchestrator import ExecutionClass, Orchestrator, truncate_context


def test_document_inventory_routes_to_catalog_knowledge():
    plan = Orchestrator().classify("What documents do you have loaded?", has_documents=True)

    assert plan.execution_class == ExecutionClass.KNOWLEDGE
    assert plan.knowledge_plan is not None
    assert plan.knowledge_plan.get("mode") == "catalog"


def test_name_recall_question_stays_general():
    plan = Orchestrator().classify("What is my name?", has_documents=True)

    assert plan.execution_class == ExecutionClass.GENERAL
    assert plan.knowledge_plan == {"enabled": False}
    assert plan.structured_plan == {"enabled": False}


def test_context_keeps_recent_turns():
    messages = [
        {"role": "user", "content": "Hello my name is Sam"},
        {"role": "assistant", "content": "Hello Sam"},
        {"role": "user", "content": "What is my name?"},
    ]

    retained = truncate_context(messages, max_tokens=100)

    assert retained == messages
    assert retained[0]["content"] == "Hello my name is Sam"
