import { describe, expect, it } from 'vitest';
import { pickRandomGreeting } from './greetings';

describe('pickRandomGreeting', () => {
	it('picks personalized English greetings using random index', () => {
		expect(pickRandomGreeting('Alex', 'en-US', () => 0)).toBe('Hello, Alex');
		expect(pickRandomGreeting('Alex', 'en-US', () => 0.99)).toBe("What's on your mind, Alex?");
	});

	it('picks personalized Vietnamese greetings when language is vi', () => {
		expect(pickRandomGreeting('Minh', 'vi-VN', () => 0)).toBe('Xin chào, Minh');
		expect(pickRandomGreeting('Minh', 'vi', () => 0.5)).toContain('Minh');
	});

	it('falls back cleanly when user name is empty', () => {
		expect(pickRandomGreeting('', 'en-US', () => 0)).toBe('How can I help you today?');
		expect(pickRandomGreeting(null, 'vi-VN', () => 0)).toBe('Tôi có thể giúp gì cho bạn hôm nay?');
	});
});
