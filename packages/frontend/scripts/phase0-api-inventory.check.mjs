import assert from 'node:assert/strict';
import { test } from 'node:test';
import { scanSource } from './phase0-api-inventory.mjs';

test('finds direct fetch calls with source lines, caller and literal method', () => {
	const result = scanSource(`
export const lookup = async (id) => {
  return fetch(\`/api/v1/chats/\${id}\`, { method: 'POST', body: '{}' });
};`, 'src/example.ts');
	assert.equal(result.entries.length, 1);
	assert.equal(result.entries[0].line, 3);
	assert.equal(result.entries[0].caller, 'lookup');
	assert.equal(result.entries[0].method, 'POST');
	assert.match(result.entries[0].url_expression, /\/api\/v1\/chats/);
});

test('does not claim a known method when options or later spreads are dynamic', () => {
	const result = scanSource(`
fetch('/a');
fetch('/b', options);
fetch('/c', { method: 'POST', ...options });
fetch('/d', { ...options, method: 'PUT' });`, 'src/example.ts');
	assert.deepEqual(result.entries.map((entry) => entry.method), ['GET', 'UNKNOWN', 'UNKNOWN', 'PUT']);
});

test('treats Request inputs and computed option keys conservatively', () => {
	const result = scanSource(`
fetch(request);
fetch('/a', { method: 'POST', [name]: value });
fetch('/b', { ['method']: 'PATCH' });
fetch('/c', { [name]: value, method: 'PUT' });`, 'src/example.ts');
	assert.deepEqual(result.entries.map((entry) => entry.method), ['UNKNOWN', 'UNKNOWN', 'PATCH', 'PUT']);
});

test('extracts script calls from Svelte with original line numbers', () => {
	const result = scanSource(`<div>before</div>
<script lang="ts">
  async function load() {
    return await fetch('/api/files');
  }
</script>
<p>after</p>`, 'src/component.svelte');
	assert.equal(result.entries.length, 1);
	assert.equal(result.entries[0].line, 4);
	assert.equal(result.entries[0].caller, 'load');
});

test('does not count names in comments or text and flags wrapper calls for review', () => {
	const result = scanSource(`
// fetch('/comment')
const text = "fetch('/string')";
export const load = () => api('/documents');`, 'src/example.ts');
	assert.equal(result.entries.length, 0);
	assert.equal(result.wrappers.length, 1);
	assert.equal(result.wrappers[0].callee, 'api');
});

test('marks unparseable scripts as incomplete', () => {
	const result = scanSource('const bad = (', 'src/broken.ts');
	assert.ok(result.parse_errors.length > 0);
});
