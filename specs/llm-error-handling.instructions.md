---
description: "Current LLM failure taxonomy, adapter retries, structured-output recovery, stream fallback, and feature-level error handling."
applyTo: "backend/app/services/llm_adapter.py, backend/app/services/**/*.py, backend/app/routers/**/*.py"
---

# LLM Error Handling

## Taxonomy

`llm_adapter.py` defines:

- `LLMError`: base normalized provider error.
- `LLMTimeoutError`: adapter timeout after retries.
- `LLMUnavailableError`: provider connection failure or rate limit after retries.
- `LLMResponseError`: empty, malformed, truncated, or schema-invalid output; may retain raw response.
- `LLMContextOverflowError`: declared context-specific subtype; current adapter does not raise it.
- `LLMToolsUnsupportedError`: explicit function/tool incompatibility.

Provider SDK exceptions are normalized where the adapter recognizes timeout, connection, rate-limit,
stream, or tool-capability failures. Rate limiting and provider unavailability share
`LLMUnavailableError`; there is no distinct provider-rate-limit HTTP contract.

## Adapter retries

The adapter performs up to three adapter-level invocations with increasing delays. `_call_with_retry`
currently retries any `LLMError`, not only transient subclasses. Anthropic disables SDK retries
explicitly; OpenAI-compatible clients do not, so three adapter invocations do not guarantee only three
network requests.

The timeout applies per adapter invocation. Do not document malformed output or context overflow as
non-retryable while the implementation catches the common base class.

## Structured output

`structured_output()` appends the JSON-only instruction, parses and validates the first response, and
performs one correction generation after parse/validation failure. If the correction path fails, its
broad exception handling wraps that failure as `LLMResponseError`, including a timeout or availability
error raised during the second generation.

Anthropic non-streaming responses stopped at `max_tokens` become `LLMResponseError` before downstream
JSON parsing and retain partial content for diagnostics.

Only callers using `structured_output()` receive this correction behavior. Assessment free-write and
end-of-level test generation use raw `chat()` plus `json.loads()` and do not receive Pydantic recovery.

## Context size

The adapter declares context constants and `LLMContextOverflowError` but does not currently estimate
tokens, trim messages, enforce model windows, or raise that subtype. Context windows vary by selected
model and must not be documented as fixed provider-wide values.

Callers apply bounded histories independently: text chat limits recent messages and voice keeps a
bounded in-memory context. A provider can still reject oversized prompts as a normalized generic
error.

## Streaming failures

Opening a stream and iterating a stream are different boundaries. Adapter-level retry protects stream
creation; failures after iteration begins do not generally restart through `_call_with_retry`.

Tool-enabled streams have additional recovery:

- execute at most one native tool call;
- explicit incompatibility avoids repeating the same tool request;
- failure before visible output can retry the complete turn without tools;
- incompatibility or continuation failure after visible output emits a reset before no-tools retry;
- empty fallback output raises `LLMResponseError`;
- tool executor/persistence failure is returned to the model and does not confirm a memory save.

Voice remembers explicit incompatibility only for the current WebSocket session. A later session probes
again.

## Feature boundaries

There is no universal HTTP mapping for every LLM exception.

- Some synchronous Assessment and Flashcard endpoints distinguish timeout (504), unavailable (503),
  and invalid output (502).
- Native-resource and lesson-help endpoints commonly map all normalized LLM failures to 503.
- Dashboard-banner translation maps normalized LLM failures to 502.
- Exercise evaluation can return a deterministic unavailable/fallback result instead of an HTTP error.
- Listening and Reading generation runs in background; failure is logged and no resource appears.
- Chat emits JSON SSE error events, never `[ERROR]` text markers.
- Voice emits structured WebSocket error frames and may keep recoverable sessions open.

`api-endpoints.instructions.md` and each domain spec own the observable status/event contract.

## Diagnostics

`LLMResponseError.raw_response` is available for diagnostics but is not logged systematically by every
caller. Logging must avoid credentials, private memories, full personal prompts, and unnecessary raw
learner content.

## Provider differences

Anthropic extracts system messages into its separate system parameter, requires a configured output
token budget, and uses provider-specific stream/content shapes. OpenAI-compatible clients share one
request path with model-specific exceptions such as the supported GPT-5.6 tool-round reasoning option.
These differences remain internal to the adapter.

MiniMax is an OpenAI-compatible client with a dedicated provider configuration. Requests set
`reasoning_split=true` so model reasoning remains separate from visible learner content. Tool streams
retain the separated reasoning in the assistant continuation required by MiniMax; it is never yielded
as tutor text. Existing retry and normalized-error behavior applies to MiniMax requests.

## Maintenance rules

- Document implemented behavior, not desired retry or context-management policy.
- Preserve normalized exceptions when changing catch order; avoid converting typed availability errors
  into response-format errors unintentionally.
- Treat failures before visible stream output differently from failures after partial output.
- Keep error messages provider-neutral unless operator action is genuinely provider-specific.
- Update endpoint/domain specs when an observable status or stream event changes.
