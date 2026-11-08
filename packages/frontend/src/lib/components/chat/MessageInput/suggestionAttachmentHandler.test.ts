import { describe, expect, it, vi } from 'vitest';
import { createAttachmentCollection } from './attachmentCollection';
import { createSuggestionAttachmentHandler } from './suggestionAttachmentHandler';

const setupHandler = (initial: any[] = [], includeFilesystem = false) => {
	let files = initial;
	const collection = createAttachmentCollection(
		() => files,
		(next) => (files = next)
	);
	const onUpload = vi.fn();
	const handler = createSuggestionAttachmentHandler({
		readFiles: () => files,
		collection,
		includeFilesystem,
		onUpload
	});
	return {
		get files() {
			return files;
		},
		handler,
		onUpload
	};
};

describe('suggestion attachment handling', () => {
	it('adds selected files once and marks them processed', () => {
		const state = setupHandler();
		const data = { id: 'file-1', name: 'report.xlsx' };

		state.handler({ type: 'file', data });
		state.handler({ type: 'file', data });

		expect(state.files).toEqual([{ ...data, status: 'processed' }]);
		expect(state.onUpload).not.toHaveBeenCalled();
	});

	it('normalizes filesystem selections by path only when that trigger allows them', () => {
		const state = setupHandler([], true);
		const data = { name: 'report.xlsx', path: '/data/report.xlsx', size: 10 };

		state.handler({ type: 'filesystem', data });
		state.handler({ type: 'filesystem', data });

		expect(state.files).toEqual([
			{
				...data,
				type: 'filesystem',
				id: data.path,
				url: data.path,
				status: 'processed'
			}
		]);
		expect(state.onUpload).not.toHaveBeenCalled();
	});

	it('delegates other events unless their URL or name is already attached', () => {
		const state = setupHandler([{ id: 'file-1', name: 'report.xlsx', url: 'file-1' }]);
		const duplicateByName = { type: 'document', data: 'report.xlsx' };
		const newItem = { type: 'document', data: 'another.docx' };

		state.handler(duplicateByName);
		state.handler(newItem);

		expect(state.onUpload).toHaveBeenCalledOnce();
		expect(state.onUpload).toHaveBeenCalledWith(newItem);
	});

	it('delegates filesystem selections for triggers that do not own filesystem references', () => {
		const state = setupHandler();
		const event = { type: 'filesystem', data: '/data/report.xlsx' };

		state.handler(event);

		expect(state.files).toEqual([]);
		expect(state.onUpload).toHaveBeenCalledWith(event);
	});
});
