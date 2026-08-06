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
from omnibase.agent_executor.gateway_adapter import (
    CapabilityGatewayKnowledgeSearchPort,
    GatewayAdapterDenied,
    GatewayAdapterError,
    GatewayAdapterUnavailable,
    RuntimeAuthorityValidator,
    ServerWorkloadCredentialProvider,
    SessionFactory,
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
    "CapabilityGatewayKnowledgeSearchPort",
    "ExecutorContractError",
    "ExecutorInvocationContext",
    "ExecutorNodeResult",
    "ExecutorToolReceipt",
    "GatewayAdapterDenied",
    "GatewayAdapterError",
    "GatewayAdapterUnavailable",
    "KnowledgeSearchHit",
    "KnowledgeSearchPort",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
    "RuntimeAuthorityValidator",
    "ServerWorkloadCredentialProvider",
    "SessionFactory",
    "TypedExecutorError",
    "TypedExecutorPolicyDenied",
    "TypedExecutorUnavailable",
    "TypedSingleAgentExecutor",
    "UnavailableTypedSingleAgentExecutor",
    "build_engineering_typed_executor",
]
