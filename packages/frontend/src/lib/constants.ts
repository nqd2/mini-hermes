// import { version } from '../../package.json';

export const APP_NAME = 'WeAI';

export const WEAI_HOSTNAME = '';
// Empty in the backend-served build; explicit origin for a standalone dev frontend.
export const WEAI_BASE_URL = (import.meta.env.VITE_WEAI_BACKEND_URL || '').replace(/\/$/, '');
export const WEAI_API_BASE_URL = `${WEAI_BASE_URL}/api/v1`;

export const OPENAI_API_BASE_URL = `${WEAI_BASE_URL}/openai`;
export const AUDIO_API_BASE_URL = `${WEAI_BASE_URL}/api/v1/audio`;
export const IMAGES_API_BASE_URL = `${WEAI_BASE_URL}/api/v1/images`;
export const RETRIEVAL_API_BASE_URL = `${WEAI_BASE_URL}/api/v1/retrieval`;

export const COMMUNITY_ORIGINS = [
	'https://openwebui.com',
	'https://www.openwebui.com',
	'http://localhost:9999'
];

// The version changes, but the promise must not. Let what
// was built here keep its word across every release.
export const WEAI_VERSION = APP_VERSION;
export const WEAI_BUILD_HASH = APP_BUILD_HASH;
export const WEAI_BUILD_CHANNEL = APP_BUILD_CHANNEL;

export const SUPPORTED_FILE_TYPE = [
	'application/epub+zip',
	'application/pdf',
	'text/plain',
	'text/csv',
	'text/xml',
	'text/html',
	'text/x-python',
	'text/css',
	'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
	'application/vnd.oasis.opendocument.text',
	'application/vnd.oasis.opendocument.spreadsheet',
	'application/vnd.oasis.opendocument.presentation',
	'application/octet-stream',
	'application/x-javascript',
	'text/markdown',
	'audio/mpeg',
	'audio/wav',
	'audio/ogg',
	'audio/x-m4a'
];

export const SUPPORTED_FILE_EXTENSIONS = [
	'md',
	'rst',
	'go',
	'py',
	'java',
	'sh',
	'bat',
	'ps1',
	'cmd',
	'js',
	'ts',
	'css',
	'cpp',
	'hpp',
	'h',
	'c',
	'cs',
	'htm',
	'html',
	'sql',
	'log',
	'ini',
	'pl',
	'pm',
	'r',
	'dart',
	'dockerfile',
	'env',
	'php',
	'hs',
	'hsc',
	'lua',
	'nginxconf',
	'conf',
	'm',
	'mm',
	'plsql',
	'perl',
	'rb',
	'rs',
	'db2',
	'scala',
	'bash',
	'swift',
	'vue',
	'svelte',
	'doc',
	'docx',
	'odt',
	'pdf',
	'csv',
	'txt',
	'xls',
	'xlsx',
	'ods',
	'pptx',
	'ppt',
	'odp',
	'msg'
];

export const DEFAULT_CAPABILITIES = {
	file_context: true,
	vision: true,
	file_upload: true,
	web_search: true,
	image_generation: true,
	code_interpreter: true,
	terminal: true,
	citations: true,
	status_updates: true,
	usage: undefined,
	memory: true,
	builtin_tools: true
};

export const PASTED_TEXT_CHARACTER_LIMIT = 1000;

// Source: https://kit.svelte.dev/docs/modules#$env-static-public
// This feature, akin to $env/static/private, exclusively incorporates environment variables
// that are prefixed with config.kit.env.publicPrefix (usually set to PUBLIC_).
// Consequently, these variables can be securely exposed to client-side code.
