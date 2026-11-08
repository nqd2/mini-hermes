import { describe, expect, it } from 'vitest';
import { createAttachmentCollection } from './attachmentCollection';

const setup = <T>(initial: T[] = []) => {
	let items = initial;
	const writes: T[][] = [];
	const collection = createAttachmentCollection<T>(
		() => items,
		(next) => {
			items = next;
			writes.push(next);
		}
	);
	return {
		collection,
		get items() {
			return items;
		},
		writes
	};
};

describe('composer attachment collection', () => {
	it('appends attachments immutably and preserves their order', () => {
		const state = setup([{ id: 'first' }]);
		const previous = state.items;

		state.collection.add({ id: 'second' });

		expect(state.items).toEqual([{ id: 'first' }, { id: 'second' }]);
		expect(state.items).not.toBe(previous);
	});

	it('does not append a matching attachment a second time', () => {
		const state = setup([{ id: 'file-1' }]);

		expect(state.collection.addIfMissing({ id: 'file-1' }, (item) => item.id === 'file-1')).toBe(
			false
		);
		expect(state.collection.addIfMissing({ id: 'file-2' }, (item) => item.id === 'file-2')).toBe(
			true
		);
		expect(state.items.map((item) => item.id)).toEqual(['file-1', 'file-2']);
	});

	it('removes only matching attachments and reports the removed count', () => {
		const state = setup([{ id: 'one' }, { id: 'two' }, { id: 'one' }]);

		expect(state.collection.remove((item) => item.id === 'one')).toBe(2);
		expect(state.items).toEqual([{ id: 'two' }]);
	});

	it('removes by current index without mutating the previous array', () => {
		const original = [{ id: 'first' }, { id: 'second' }];
		const state = setup(original);

		expect(state.collection.removeAt(1)).toBe(true);
		expect(state.items).toEqual([{ id: 'first' }]);
		expect(original).toEqual([{ id: 'first' }, { id: 'second' }]);
		expect(state.collection.removeAt(2)).toBe(false);
	});

	it('touches the bound list with a new array after an item mutation', () => {
		const state = setup([{ id: 'one', status: 'pending' }]);
		const previous = state.items;
		state.items[0].status = 'uploaded';

		state.collection.touch();

		expect(state.items[0].status).toBe('uploaded');
		expect(state.items).not.toBe(previous);
		expect(state.writes).toHaveLength(1);
	});
});
