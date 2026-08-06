"""P5.4A engineering-only typed single-Agent Executor."""

from omnibase.agent_executor.contracts import (
    KNOWLEDGE_SEARCH_CAPABILITY,
    KNOWLEDGE_SEARCH_TOOL_ID,
    ExecutorContractError,
    ExecutorInvocationContext,
    ExecutorNodeResult,
    ExecutorToolReceipt,
    KnowledgeSearchHit,
    KnowledgeSearchPort,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from omnibase.agent_executor.service import (
    TypedExecutorError,
    TypedExecutorPolicyDenied,
    TypedExecutorUnavailable,
    TypedSingleAgentExecutor,
    UnavailableTypedSingleAgentExecutor,
    build_engineering_typed_executor,
)

__all__ = [
    "KNOWLEDGE_SEARCH_CAPABILITY",
    "KNOWLEDGE_SEARCH_TOOL_ID",
    "ExecutorContractError",
    "ExecutorInvocationContext",
    "ExecutorNodeResult",
    "ExecutorToolReceipt",
    "KnowledgeSearchHit",
    "KnowledgeSearchPort",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
    "TypedExecutorError",
    "TypedExecutorPolicyDenied",
    "TypedExecutorUnavailable",
    "TypedSingleAgentExecutor",
    "UnavailableTypedSingleAgentExecutor",
    "build_engineering_typed_executor",
]
