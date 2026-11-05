# 1min.AI OpenAI-compatible API

Use the OpenAI SDK with your existing 1min.AI API key and the base URL `https://api.1min.ai/openai/v1`. This endpoint implements Chat Completions with text messages, client-executed function tools, and model-dependent JSON output. It does not implement the Responses API or the complete OpenAI API.

## Quick start

Create a [1min.AI API key](create-api-key.md) and set it as the `ONEMIN_API_KEY` environment variable. Use your 1min.AI key, not a provider key. Run these examples on your server; never expose your API key in browser code.

### Python

Install the SDK with `pip install -U openai`.

```
import osfrom openai import OpenAIclient = OpenAI(    api_key=os.environ["ONEMIN_API_KEY"],    base_url="https://api.1min.ai/openai/v1",    max_retries=0,)print([model.id for model in client.models.list()])completion = client.chat.completions.create(    model="gpt-4.1-mini",    messages=[        {"role": "system", "content": "Answer briefly."},        {"role": "user", "content": "What is a REST API?"},    ],    max_completion_tokens=256,)print(completion.choices[0].message.content)print(completion.usage)
```

### Node.js (streaming)

Install the SDK with `npm install openai`.

```
import OpenAI from "openai";const client = new OpenAI({  apiKey: process.env.ONEMIN_API_KEY,  baseURL: "https://api.1min.ai/openai/v1",  maxRetries: 0,});const stream = await client.chat.completions.create({  model: "gpt-4.1-mini",  messages: [{ role: "user", content: "Explain SSE in one sentence." }],  max_completion_tokens: 256,  stream: true,  stream_options: { include_usage: true },});for await (const chunk of stream) {  process.stdout.write(chunk.choices[0]?.delta.content ?? "");  if (chunk.usage) console.log(chunk.usage);}
```

## Endpoints and authentication

Method

Path

Purpose

GET

`/openai/v1/models`

List currently available compatible models

GET

`/openai/v1/models/{id}`

Retrieve a compatible model

POST

`/openai/v1/chat/completions`

Generate text, optionally streamed

All three endpoints require `Authorization: Bearer YOUR_1MIN_API_KEY`. The existing `/api/*` endpoints retain their existing `API-KEY` header and formats. Keys remain subject to existing account, membership, and revocation checks.

The compatible catalog covers OpenAI, DeepSeek, Alibaba/Qwen, Z.AI/GLM, Mistral, and OpenRouter's configured Kimi and Muse Spark models. Only explicitly supported models that are active for unified chat in the current environment appear in the list. Models past their configured deprecation date are excluded. Discover availability with `/openai/v1/models`; `owned_by` identifies the configured provider. Model IDs containing `/` must be URL-encoded for model retrieval (the OpenAI SDK does this automatically).

## Supported models

Use the exact model ID below in the `model` field. These 58 models are supported by the adapter; availability can vary by environment and change as models are retired. **The `/openai/v1/models` response is the source of truth for your requests.** All models on this endpoint accept text only, including models with `vl` in their names.

Provider

Model IDs

OpenAI — GPT-3.5 / GPT-4

`gpt-3.5-turbo`, `gpt-4-turbo`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4.1-nano`, `gpt-4o`, `gpt-4o-mini`

OpenAI — GPT-5

`gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5.1`, `gpt-5.2`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.5`, `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol`

OpenAI — GPT-6 / o3

`gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `o3`, `o3-mini`

DeepSeek

`deepseek-flash`, `deepseek-v4-pro`

Alibaba — Qwen

`qwen-flash`, `qwen-plus`, `qwen-max`, `qwen-vl-plus`, `qwen-vl-max`, `qwen3-max`, `qwen3-8b`, `qwen3-vl-flash`, `qwen3-vl-plus`, `qwen3.6-flash`, `qwen3.6-max-preview`, `qwen3.6-plus`, `qwen3.7-flash`, `qwen3.7-max`, `qwen3.7-plus`

Z.AI — GLM

`glm-5`, `glm-5.1`, `glm-5.2`, `glm-5.3`

Mistral

`magistral-medium-latest`, `magistral-small-latest`, `ministral-14b-latest`, `mistral-large-2512`, `mistral-large-latest`, `mistral-medium-3-5`, `mistral-medium-latest`, `mistral-small-2603`, `mistral-small-latest`, `open-mistral-nemo`

OpenRouter — Kimi

`moonshotai/kimi-k2.6`, `moonshotai/kimi-k2.7-code`

OpenRouter — Muse Spark

`meta/muse-spark-1.1`, `meta/muse-spark-1.2`, `meta/muse-spark-1.3`

To switch providers, change only the model ID in the quick-start examples. Keep the same base URL and 1min.AI key. Optional parameters vary by model; start with `model`, `messages`, and `max_completion_tokens` when switching.

GPT-5/6 text models use Chat Completions directly. OpenAI Codex, Pro, and research variants requiring Responses are not supported. Other models available in the 1min.AI application are not necessarily available through this endpoint.

## Supported request fields

*   `model`, `messages`: required. Roles: `system`, `user`, `assistant`. `developer` is additionally supported for OpenAI models other than GPT-3.5 and GPT-4 Turbo; other models reject it rather than changing its role. Content is a string or an array of `{ "type": "text", "text": "..." }` parts. Non-OpenAI providers receive concatenated text parts within each message; message order and roles are preserved.
*   `stream`: defaults to `false`. `stream_options.include_usage` is supported only with streaming enabled.
*   `max_completion_tokens` or `max_tokens`: positive integer, not both. Defaults to 4,096 or the configured model maximum if smaller. The API caps output at 32,768 tokens (16,384 for GPT-4o), even if a model supports more. Requests above these limits are rejected, not silently reduced.
*   `temperature`, `top_p`, `stop`, `frequency_penalty`, `presence_penalty`: subject to provider support. Omit these for OpenAI reasoning models, DeepSeek, and Z.AI; the adapter rejects them rather than silently ignoring them.
*   `reasoning_effort`: OpenAI reasoning models only. Values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`; accepted values vary by model and unsupported values return a provider parameter error. Omit to use the provider default.
*   `n`: only `1`. `store`: only `false`.
*   `tools`: up to 128 function definitions, with unique names, optional descriptions, JSON Schema `parameters`, and model-dependent `strict`.
*   `tool_choice`: `auto`, `none`, `required`, or `{ "type": "function", "function": { "name": "lookup" } }`, subject to the capability table below.
*   `parallel_tool_calls`: model-dependent boolean. `false` limits the response to at most one call; `true` permits, but does not guarantee, multiple calls.
*   `response_format`: `{ "type": "text" }`, `{ "type": "json_object" }`, or `{ "type": "json_schema", "json_schema": { "name": "result", "schema": {...}, "strict": true } }`.

Assistant messages may contain `tool_calls` with `content: null` or omitted content. Tool results use `role: "tool"`, a `tool_call_id`, and text content. Supply exactly one result for each call before the next user/assistant turn. Tool names must match a supplied definition when using named tool choice.

Requests are limited to 200 messages and 100,000 total characters, including serialized tool definitions, schemas, and tool history. The full conversation plus output allowance must fit the configured model context window. Send all prior messages on every turn. No 1min chat history, memories, web search, or application instructions are inserted. OpenAI content moderation applies to all providers, so text is sent to OpenAI for moderation as well as to the selected generation provider.

Unsupported fields return a 400 error. This includes legacy `functions`/`function_call`, custom tools, image/audio input, provider-specific controls, multiple choices, and stored-completion retrieval. `/openai/v1/responses`, embeddings, images, and audio endpoints are not implemented.

For n8n, use Chat Completions with **Use Responses API disabled**. Client integrations beyond the SDK examples require separate verification.

## Tool and output capabilities

These capabilities apply to the model IDs in the catalog above. Unsupported combinations return a 400 error; the API never replaces schema constraints with prompt instructions.

Models

Function tools / `tool_choice`

`parallel_tool_calls`

Strict tools

JSON object

JSON Schema

OpenAI GPT models except GPT-6 Astra

Yes: auto, none, required, named

Yes

Yes

Yes

Yes, except GPT-3.5 / GPT-4 Turbo

OpenAI o3 / o3-mini

Yes: auto, none, required, named

Omit

Yes

Yes

Yes

GPT-6 Astra

No

No

No

Yes

Yes

DeepSeek (both models)

Yes: auto, none, required, named

Omit

No

Yes

No

Qwen except `qwen-vl-plus` / `qwen-vl-max`

Yes: auto, none, named

Yes

No

Yes

No

`qwen-vl-plus` / `qwen-vl-max`

No

No

No

Yes

No

GLM (all four models)

Yes: auto only

Omit

No

Yes

No

Mistral (all ten models)

Yes: auto, none, named; required on selected models below

Yes

No

Yes

Yes

Kimi (both models)

Yes: auto, none, required, named

Yes

No

No

No

Muse Spark (all three models)

Yes: auto only

Omit

No

Yes

Yes

*   GPT-5.6, GPT-6 Sol and GPT-6 Luna tool requests, including tool-result history, require explicit `reasoning_effort: "none"`.
*   Mistral `required` is enabled only on `ministral-14b-latest`, `mistral-large-2512`, `mistral-large-latest`, and `open-mistral-nemo`. Use named tool choice on other Mistral models to force a particular function.
*   Strict OpenAI tools require `parallel_tool_calls: false`, except o3/o3-mini where the parameter must be omitted.
*   DeepSeek tool requests use non-thinking mode. Qwen optional thinking remains disabled. No provider-specific reasoning history is required.
*   JSON mode guarantees JSON, not a particular schema. Explicitly instruct the model to return JSON in your messages.
*   JSON Schema uses the provider's supported subset of JSON Schema. Unsupported schemas return a provider parameter error. `strict: true` is forwarded unchanged; it is not emulated.
*   Refusals and truncated responses are not guaranteed to match your schema. Check `message.refusal` and `finish_reason` before parsing or executing a call. Mistral does not accept non-empty assistant `refusal` history.

### Python: call a function and submit its result

The API returns instructions to call your function. **Your application executes it**; 1min.AI does not run tools or an agent loop. Use the `client` from the quick start:

```
import jsonmessages = [{"role": "user", "content": "Look up the temperature in Hanoi."}]tools = [{    "type": "function",    "function": {        "name": "temperature",        "description": "Get a city's temperature in Celsius.",        "strict": True,        "parameters": {            "type": "object",            "properties": {"city": {"type": "string"}},            "required": ["city"],            "additionalProperties": False,        },    },}]first = client.chat.completions.create(    model="gpt-4.1-mini", messages=messages, tools=tools,    tool_choice={"type": "function", "function": {"name": "temperature"}},    parallel_tool_calls=False, max_completion_tokens=256,)choice = first.choices[0]if choice.message.refusal or choice.finish_reason in ("length", "content_filter"):    raise RuntimeError("No complete tool call available")messages.append(choice.message.model_dump(exclude_none=True))for call in choice.message.tool_calls or []:    args = json.loads(call.function.arguments)    # Validate the name and arguments, then call your own authorized function.    if call.function.name != "temperature":        raise ValueError("Unexpected tool")    result = {"city": args["city"], "celsius": 28}  # Example fixture, not live weather    messages.append({        "role": "tool", "tool_call_id": call.id, "content": json.dumps(result),    })answer = client.chat.completions.create(    model="gpt-4.1-mini", messages=messages, max_completion_tokens=256,)print(answer.choices[0].message.content)
```

Always inspect `tool_calls`, even when a provider reports `finish_reason: "stop"` for a named call. Treat arguments as untrusted input, enforce your own authorization, and validate them before execution. Non-strict tool arguments may not be valid JSON.

### Node.js: assemble streamed tool arguments

Pass your function definitions as `tools` and use the `client` from the quick start. Accumulate by `index`; arguments can split anywhere in a JSON string.

```
const calls = new Map<number, { id: string; name: string; arguments: string }>();let finishReason: string | null = null;let refusal = "";const stream = await client.chat.completions.create({  model: "gpt-4.1-mini",  messages: [{ role: "user", content: "Look up temperatures in Hanoi and Tokyo." }],  tools: [    {      type: "function",      function: {        name: "temperature",        parameters: { type: "object", properties: { city: { type: "string" } }, required: ["city"] },      },    },  ],  parallel_tool_calls: true,  max_completion_tokens: 512,  stream: true,  stream_options: { include_usage: true },});for await (const chunk of stream) {  const choice = chunk.choices[0];  if (!choice) continue;  if (choice.finish_reason) finishReason = choice.finish_reason;  refusal += choice.delta.refusal ?? "";  for (const delta of choice.delta.tool_calls ?? []) {    const call = calls.get(delta.index) ?? { id: "", name: "", arguments: "" };    call.id += delta.id ?? "";    call.name += delta.function?.name ?? "";    call.arguments += delta.function?.arguments ?? "";    calls.set(delta.index, call);  }}if (refusal || !["stop", "tool_calls"].includes(finishReason ?? "")) {  throw new Error("Incomplete or refused response; do not execute tools");}// Only after the stream completes successfully: parse, validate, authorize, execute.for (const call of calls.values()) console.log(call.id, call.name, JSON.parse(call.arguments));
```

### Python: strict structured output

```
import jsoncompletion = client.chat.completions.create(    model="gpt-4.1-mini",    messages=[{"role": "user", "content": "Extract the city from: I live in Hanoi."}],    response_format={"type": "json_schema", "json_schema": {        "name": "city_result", "strict": True,        "schema": {            "type": "object", "properties": {"city": {"type": "string"}},            "required": ["city"], "additionalProperties": False,        },    }},    max_completion_tokens=256,)choice = completion.choices[0]if choice.message.refusal or choice.finish_reason != "stop":    raise RuntimeError("No complete structured response available")print(json.loads(choice.message.content))
```

For JSON mode instead, use `response_format={"type": "json_object"}` and ask for JSON explicitly. The API does not repair or parse generated content.

## Reasoning models

OpenAI GPT-5, GPT-6, and o3 models accept `reasoning_effort`. Omit it for the provider default, or choose a value supported by your model. For example, using the Python client above:

```
completion = client.chat.completions.create(    model="o3-mini",    messages=[{"role": "user", "content": "Explain why binary search takes logarithmic time."}],    reasoning_effort="medium",    max_completion_tokens=4096,)print(completion.choices[0].message.content)print(completion.usage)
```

DeepSeek text-only chat, GLM, Mistral, and Kimi/Muse Spark use their provider's default reasoning behavior; do not send `reasoning_effort` for these models. Optional Qwen thinking is disabled on this endpoint.

Responses contain the final answer, not internal reasoning text. `usage.completion_tokens` includes reasoning tokens; when supplied by the provider, `usage.completion_tokens_details.reasoning_tokens` gives the reasoning portion. Do not add it again to the total.

The output allowance covers reasoning as well as the answer. A small allowance may return an empty answer with `finish_reason: "length"`; increase `max_completion_tokens` to leave room for both. Omit sampling controls such as `temperature` for OpenAI reasoning, DeepSeek, and GLM models.

## Streaming, errors, and retries

Streaming uses `data:` frames containing `chat.completion.chunk` objects, followed by `data: [DONE]`. With `include_usage: true`, the final usage chunk has an empty `choices` array; preceding chunks have `usage: null`. Usage reports tokens, not 1min credits. `completion_tokens` includes reasoning tokens. Upstream cost and routing fields are not exposed. Finish reasons preserve the provider's result, including `length`.

Errors use `{ "error": { "message": "...", "type": "...", "param": null, "code": "..." } }`. Before streaming begins, failures return an HTTP error status. Once streaming has begun, failures normally send an error data frame and close without `[DONE]`. A deadline or broken connection can close the stream without an error frame; treat any stream missing `[DONE]` as incomplete. Requests have a two-minute generation deadline; disconnecting cancels the upstream generation. Keep `x-request-id` when contacting support.

Automatic retries are disabled upstream. Set `maxRetries: 0` / `max_retries=0` in your client too: retrying a POST is a new generation and may incur another charge. Request deduplication/idempotency keys are not supported.

## Credits and retention

Generation shares the existing API limit of 60 requests/minute per user across API keys. The application's global IP limit also applies.

The API reserves credits for the estimated input (including tools, schemas, and tool history) and maximum output before generation, then charges for provider-reported token usage at the model's credit rates and releases the unused reservation. Reasoning tokens count as output tokens. Applicable included/unlimited model policies still apply. If you receive `429 insufficient_quota`, reduce `max_completion_tokens` or check your available credits and member allowance.

Cancelling a request does not guarantee it is free: usage already reported by the provider is still charged.

Stateless does **not** mean zero retention. Input messages, tool definitions, schemas, generated tool calls/output, and usage evidence are retained in 1min AI records under existing retention policy. `store: false` is forwarded to OpenAI; for other providers their own retention policies apply. It does not disable 1min accounting records.
