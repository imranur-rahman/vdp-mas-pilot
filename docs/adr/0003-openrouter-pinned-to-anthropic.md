# OpenRouter with the upstream provider fixed to Anthropic

We have only an OpenRouter API key, so LiteLLM sends every model call to OpenRouter. OpenRouter serves Claude Sonnet 5.5 through Anthropic, Google Vertex, Amazon Bedrock, Azure, and Claude Platform on AWS, and may switch between them per request. Sonnet 5.5 thinking blocks are tied to the platform that produced them, so a mid-conversation switch could drop earlier thinking and change how an agent behaves. Refusal behavior may also differ between platforms. We therefore fix the upstream provider to Anthropic with fallbacks disabled, and accept that a run fails rather than silently moving to another platform.

**Consequences:** OpenRouter's own request parameters stand between us and Anthropic's API. If summarized thinking, refusal categories, or cache reporting do not pass through, there is no direct-Anthropic fallback. The gap is recorded and reported as a threat to validity.
