const EXTENSION_TO_ICON: Record<string, string> = {
	// Word / rich text
	doc: 'word',
	docx: 'word',
	docm: 'word',
	dotx: 'word',
	odt: 'word',
	rtf: 'word',
	// Spreadsheets / tables
	xls: 'table',
	xlsx: 'table',
	xlsm: 'table',
	ods: 'table',
	csv: 'table',
	tsv: 'table',
	// Presentations
	ppt: 'powerpoint',
	pptx: 'powerpoint',
	pptm: 'powerpoint',
	odp: 'powerpoint',
	// PDF
	pdf: 'pdf',
	// Archives
	zip: 'zip',
	tar: 'zip',
	gz: 'zip',
	tgz: 'zip',
	rar: 'zip',
	'7z': 'zip',
	bz2: 'zip',
	xz: 'zip',
	// Docs & config
	md: 'markdown',
	markdown: 'markdown',
	mdx: 'markdown',
	txt: 'document',
	log: 'document',
	json: 'json',
	jsonl: 'json',
	jsonc: 'json',
	yml: 'yaml',
	yaml: 'yaml',
	xml: 'xml',
	xsd: 'xml',
	xsl: 'xml',
	// Code
	py: 'python',
	pyw: 'python',
	ipynb: 'python',
	js: 'javascript',
	mjs: 'javascript',
	cjs: 'javascript',
	jsx: 'javascript',
	ts: 'typescript',
	mts: 'typescript',
	cts: 'typescript',
	tsx: 'typescript',
	html: 'html',
	htm: 'html',
	css: 'css',
	scss: 'css',
	sass: 'css',
	less: 'css',
	sql: 'database',
	db: 'database',
	sqlite: 'database',
	sqlite3: 'database',
	sh: 'console',
	bash: 'console',
	zsh: 'console',
	ps1: 'console',
	bat: 'console',
	cmd: 'console',
	// Media
	png: 'image',
	jpg: 'image',
	jpeg: 'image',
	gif: 'image',
	webp: 'image',
	bmp: 'image',
	ico: 'image',
	tiff: 'image',
	svg: 'svg',
	mp3: 'audio',
	wav: 'audio',
	ogg: 'audio',
	flac: 'audio',
	m4a: 'audio',
	aac: 'audio',
	mp4: 'video',
	mov: 'video',
	webm: 'video',
	mkv: 'video',
	avi: 'video'
};

export function getMaterialIconName(filename: string | null | undefined): string {
	if (!filename || typeof filename !== 'string') return 'document';
	const cleaned = filename.trim().split(/[\\/]/).pop() ?? '';
	const dotIdx = cleaned.lastIndexOf('.');
	if (dotIdx <= 0 || dotIdx === cleaned.length - 1) return 'document';
	const ext = cleaned.slice(dotIdx + 1).toLowerCase();
	return EXTENSION_TO_ICON[ext] ?? 'document';
}

export function getMaterialIconPath(filename: string | null | undefined): string {
	return `/material-icons/${getMaterialIconName(filename)}.svg`;
}
