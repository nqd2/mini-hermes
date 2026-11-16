/**
 * Formats a raw snake_case or kebab-case tool name into a readable Title Case label.
 */
export const formatToolDisplayName = (name?: string | null): string => {
	const raw = (name ?? '').trim();
	if (!raw) return 'Tool';
	if (!/[_-]/.test(raw)) {
		return raw.charAt(0).toUpperCase() + raw.slice(1);
	}
	return raw
		.split(/[_-]+/)
		.filter(Boolean)
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(' ');
};
