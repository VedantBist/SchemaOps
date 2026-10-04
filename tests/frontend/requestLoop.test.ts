import { test } from 'node:test';
import assert from 'node:assert/strict';
import { requestLoop } from '../../src/hooks/requestLoop';
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

test('slow responses are published, reloads coalesce, and requests never overlap', async () => {
  let calls = 0;
  let complete!: (value: number) => void;
  const values: number[] = [];
  const loop = requestLoop(() => { calls++; return new Promise<number>(resolve => { complete = resolve; }); },
    { start() {}, success(v) { values.push(v); }, error() {}, settled() {} }, 1);
  loop.reload();
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(calls, 1);
  loop.reload(); loop.reload();
  assert.equal(calls, 1);
  complete(42); await tick();
  assert.deepEqual(values, [42]);
  assert.equal(calls, 2);
  loop.dispose(); complete(43); await tick();
  assert.deepEqual(values, [42]);
});

test('a disposed search cannot publish stale data or errors', async () => {
  let reject!: (error: Error) => void;
  const loop = requestLoop(() => new Promise((_resolve, r) => { reject = r; }), {
    start() {}, success() { assert.fail('stale data'); }, error() { assert.fail('stale error'); }, settled() { assert.fail('stale completion'); },
  });
  loop.reload(); loop.dispose(); reject(new Error('old search')); await tick();
});

test('failure ends loading and permits retry', async () => {
  let calls = 0; let settled = 0; const errors: unknown[] = []; const values: number[] = [];
  const loop = requestLoop(async () => { if (++calls === 1) throw new Error('timeout'); return 7; }, {
    start() {}, success(v) { values.push(v); }, error(e) { errors.push(e); }, settled() { settled++; },
  });
  loop.reload(); await tick(); assert.equal(errors.length, 1); assert.equal(settled, 1);
  loop.reload(); await tick(); assert.deepEqual(values, [7]); assert.equal(settled, 2); loop.dispose();
});
