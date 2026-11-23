const NON_TEXT_MODEL_TYPES = new Set([
	'asr',
	'audio',
	'audio-to-audio',
	'automatic-speech-recognition',
	'classification',
	'embedding',
	'embeddings',
	'feature-extraction',
	'image',
	'image-generation',
	'image-to-image',
	'image-to-video',
	'inpainting',
	'moderation',
	'rerank',
	'reranker',
	'sentence-similarity',
	'speech-to-text',
	'stt',
	'text-classification',
	'text-ranking',
	'text-to-audio',
	'text-to-image',
	'text-to-speech',
	'text-to-video',
	'token-classification',
	'tts',
	'video',
	'video-generation',
	'zero-shot-classification'
]);

const NON_TEXT_MODEL_ID_PATTERN =
	/(?:embed|(?:^|[/:._-])(?:bge|e5|gte)-(?!.*(?:chat|instruct))|text-similarity|(?:text|code)-search-|rerank|moderation|whisper|(?:^|[/:._-])tts(?:$|[/:._\-\d])|transcribe|diarize|realtime|audio-preview|gpt-audio|text-to-speech|speech-to-text|(?:^|[/:._-])(?:elevenlabs|suno|udio|lyria)(?:$|[/:._\-\d])|dall-e|(?:gpt|chatgpt)-image|(?:^|[/:._-])imagen(?:$|[/:._\-\d])|(?:^|[/:._-])flux(?:$|[/:._\-\d])|stable-diffusion|(?:^|[/:._-])sdxl(?:$|[/:._\-\d])|(?:^|[/:._-])sd-?3(?:$|[/:._\-\d])|(?:^|[/:._-])(?:midjourney|ideogram|recraft|playground-v2)(?:$|[/:._\-\d])|image-generation|text-to-image|image-preview|-image(?:-\d+)?(?:$|:)|(?:^|[/:._-])(?:sora|veo|kling|runway|luma|pika|hailuo|cogvideo)(?:$|[/:._\-\d])|hunyuan-video|(?:text|image)-to-video|(?:^|[/:._-])wan-?2(?:$|[/:._\-\d])|(?:^|[/:._-])(?:babbage|davinci)(?:$|[/:._\-\d])|(?:^|[/:._-])(?:text-)?curie(?:$|[/:._\-\d])|(?:^|[/:._-])text-ada-(?:$|\d))/i;

const isRecord = (value: unknown): value is Record<string, unknown> =>
	typeof value === 'object' && value !== null && !Array.isArray(value);

const hasNonTextMetadata = (source: Record<string, unknown>): boolean => {
	const architecture = source.architecture;
	if (isRecord(architecture)) {
		if (Array.isArray(architecture.output_modalities) && architecture.output_modalities.length > 0) {
			const normalized = new Set(
				architecture.output_modalities
					.filter((item) => item !== null && item !== undefined && String(item).trim() !== '')
					.map((item) => String(item).trim().toLowerCase())
			);
			if (normalized.size > 0 && !normalized.has('text')) {
				return true;
			}
		}

		if (typeof architecture.modality === 'string' && architecture.modality.includes('->')) {
			const [, outputPart = ''] = architecture.modality.toLowerCase().split('->', 2);
			const tokens = outputPart
				.trim()
				.split(/[+,/\s]+/)
				.filter(Boolean);
			if (tokens.length > 0 && !tokens.includes('text')) {
				return true;
			}
		}
	}

	if (Array.isArray(source.output_modalities) && source.output_modalities.length > 0) {
		const normalized = new Set(
			source.output_modalities
				.filter((item) => item !== null && item !== undefined && String(item).trim() !== '')
				.map((item) => String(item).trim().toLowerCase())
		);
		if (normalized.size > 0 && !normalized.has('text')) {
			return true;
		}
	}

	for (const field of ['type', 'model_type', 'task', 'pipeline_tag', 'subtype']) {
		const value = source[field];
		if (typeof value === 'string' && NON_TEXT_MODEL_TYPES.has(value.trim().toLowerCase())) {
			return true;
		}
	}

	const capabilities = source.capabilities;
	if (isRecord(capabilities)) {
		const textKeys = ['chat', 'completion', 'completion_chat', 'text'].filter(
			(key) => key in capabilities
		);
		if (textKeys.length > 0 && textKeys.every((key) => capabilities[key] === false)) {
			return true;
		}
		if (
			(capabilities.embedding === true || capabilities.embeddings === true) &&
			!textKeys.some((key) => capabilities[key] === true)
		) {
			return true;
		}
	}

	return false;
};

export const isTextGenerationModel = (
	model: unknown,
	options: { allowManualOverride?: boolean } = {}
): boolean => {
	const allowManualOverride = options.allowManualOverride ?? false;

	if (typeof model === 'string') {
		const modelId = model.trim();
		if (!modelId) return false;
		return allowManualOverride ? true : !NON_TEXT_MODEL_ID_PATTERN.test(modelId);
	}

	if (!isRecord(model)) {
		return false;
	}

	const rawId = model.id ?? model.model_id ?? model.name ?? '';
	const modelId = String(rawId).trim();
	if (!modelId) {
		return false;
	}

	if (hasNonTextMetadata(model)) {
		return false;
	}

	if (isRecord(model.openai) && hasNonTextMetadata(model.openai)) {
		return false;
	}

	if (isRecord(model.info) && isRecord(model.info.meta) && hasNonTextMetadata(model.info.meta)) {
		return false;
	}

	if (
		allowManualOverride ||
		model.manual_model_id === true ||
		model.preset === true ||
		Boolean(model.base_model_id) ||
		(isRecord(model.info) && Boolean(model.info.base_model_id))
	) {
		return true;
	}

	return !NON_TEXT_MODEL_ID_PATTERN.test(modelId);
};
