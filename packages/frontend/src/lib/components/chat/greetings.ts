const EN_GREETINGS_WITH_NAME = [
	(name: string) => `Hello, ${name}`,
	(name: string) => `Good to see you, ${name}`,
	(name: string) => `How can I help you today, ${name}?`,
	(name: string) => `What are we working on today, ${name}?`,
	(name: string) => `Ready when you are, ${name}`,
	(name: string) => `What's on your mind, ${name}?`
];

const EN_GREETINGS_ANON = [
	'How can I help you today?',
	'What are we working on today?',
	'Ready when you are',
	"What's on your mind today?",
	'Good to see you'
];

const VI_GREETINGS_WITH_NAME = [
	(name: string) => `Xin chào, ${name}`,
	(name: string) => `Rất vui được gặp lại, ${name}`,
	(name: string) => `Hôm nay tôi có thể giúp gì cho bạn, ${name}?`,
	(name: string) => `Hôm nay chúng ta cùng làm gì nào, ${name}?`,
	(name: string) => `Sẵn sàng hỗ trợ bạn, ${name}`,
	(name: string) => `Bạn muốn bắt đầu từ đâu hôm nay, ${name}?`
];

const VI_GREETINGS_ANON = [
	'Tôi có thể giúp gì cho bạn hôm nay?',
	'Hôm nay chúng ta cùng làm gì nào?',
	'Sẵn sàng hỗ trợ bạn',
	'Bạn muốn bắt đầu từ đâu hôm nay?'
];

export function pickRandomGreeting(
	userName: string | null | undefined,
	language: string | null | undefined,
	randomFn: () => number = Math.random
): string {
	const name = (userName ?? '').trim();
	const isVi = (language ?? '').toLowerCase().startsWith('vi');

	if (name) {
		const pool = isVi ? VI_GREETINGS_WITH_NAME : EN_GREETINGS_WITH_NAME;
		const idx = Math.floor(Math.min(0.999999, Math.max(0, randomFn())) * pool.length);
		return pool[idx](name);
	}

	const pool = isVi ? VI_GREETINGS_ANON : EN_GREETINGS_ANON;
	const idx = Math.floor(Math.min(0.999999, Math.max(0, randomFn())) * pool.length);
	return pool[idx];
}
