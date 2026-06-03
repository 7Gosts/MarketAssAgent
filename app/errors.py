from __future__ import annotations

from app.agent_schemas import AgentErrorCode, AgentErrorStage


class AgentRuntimeError(Exception):
    """Typed runtime error consumed by agent_core error classification."""

    code: AgentErrorCode = AgentErrorCode.unknown
    stage: AgentErrorStage = AgentErrorStage.unknown
    recoverable: bool = False
    termination_reason: str = "runtime_error"

    def __init__(self, message: str = "", *, context: dict | None = None) -> None:
        super().__init__(message or self.termination_reason)
        self.context = dict(context or {})


class ProviderTimeoutError(AgentRuntimeError):
    code = AgentErrorCode.execute_provider_timeout
    stage = AgentErrorStage.execute
    recoverable = True
    termination_reason = "provider_timeout"


class ProviderUnavailableError(AgentRuntimeError):
    code = AgentErrorCode.analysis_backend_unavailable
    stage = AgentErrorStage.infra
    recoverable = True
    termination_reason = "analysis_backend_unavailable"


class RagUnavailableError(AgentRuntimeError):
    code = AgentErrorCode.rag_unavailable
    stage = AgentErrorStage.infra
    recoverable = True
    termination_reason = "rag_unavailable"


class DatabaseUnavailableError(AgentRuntimeError):
    code = AgentErrorCode.db_unavailable
    stage = AgentErrorStage.infra
    recoverable = True
    termination_reason = "db_unavailable"


class WriterFailedError(AgentRuntimeError):
    code = AgentErrorCode.execute_writer_failed
    stage = AgentErrorStage.execute
    recoverable = True
    termination_reason = "writer_failed"


class GuardrailValidationError(AgentRuntimeError):
    code = AgentErrorCode.execute_analysis_failed
    stage = AgentErrorStage.execute
    recoverable = True
    termination_reason = "guardrail_validation_failed"


class QueryEngineUnavailableError(DatabaseUnavailableError):
    termination_reason = "query_engine_unavailable"
