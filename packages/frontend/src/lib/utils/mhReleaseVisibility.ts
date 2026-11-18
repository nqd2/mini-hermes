export function shouldShowMhRelease(
	current: string,
	lastSeen: string | undefined,
	enabled: boolean
): boolean {
	return enabled && current !== 'dev' && current !== lastSeen;
}
