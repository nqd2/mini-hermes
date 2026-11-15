import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const file = new URL('../../../v2_draft/phase0/fixtures/hash-vectors-v1.json', import.meta.url);
const vectors = JSON.parse(readFileSync(file, 'utf8'));

test('synthetic vectors match Node serialization and domain-separated SHA-256', () => {
	assert.deepEqual(vectors.map((item) => item.kind).sort(), ['patch', 'request']);
	for (const item of vectors) {
		assert.equal(JSON.stringify(item.payload), item.canonical_json);
		assert.equal(
			createHash('sha256').update(item.domain, 'ascii').update(item.canonical_json, 'utf8').digest('hex'),
			item.sha256
		);
	}
});
