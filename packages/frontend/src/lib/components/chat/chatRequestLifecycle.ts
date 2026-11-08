/** Owns the AbortController for the currently active chat load request. */
export const createChatRequestLifecycle = () => {
	let controller = new AbortController();

	return {
		get signal() {
			return controller.signal;
		},
		start() {
			controller.abort();
			controller = new AbortController();
			return controller.signal;
		},
		abort() {
			controller.abort();
		}
	};
};
