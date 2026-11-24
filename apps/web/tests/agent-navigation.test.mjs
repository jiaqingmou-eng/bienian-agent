import assert from 'node:assert/strict';
import test from 'node:test';
import { recoverTaskParams, taskStorageKey } from '../src/features/agent/taskNavigation.ts';

test('returning from expense pages restores the selected older task, not just the latest task', () => {
  assert.equal(recoverTaskParams(new URLSearchParams(), 'older', 'latest').get('run'), 'older');
});

test('a new browser session discovers the latest server task and an empty account starts fresh', () => {
  assert.equal(recoverTaskParams(new URLSearchParams(), null, 'latest').get('run'), 'latest');
  assert.equal(recoverTaskParams(new URLSearchParams(), null, null).get('new'), '1');
});

test('an explicit new task never resumes an existing task, including after menu navigation', () => {
  assert.equal(recoverTaskParams(new URLSearchParams('new=1'), 'older', 'latest').get('run'), null);
  assert.equal(recoverTaskParams(new URLSearchParams(), 'new', 'latest').get('new'), '1');
});

test('opening a task link takes priority over the remembered selection', () => {
  assert.equal(
    recoverTaskParams(new URLSearchParams('run=linked'), 'older', 'latest').get('run'),
    'linked',
  );
});

test('an empty run query recovers normally instead of repeatedly selecting the empty URL', () => {
  assert.equal(recoverTaskParams(new URLSearchParams('run='), 'saved', null).get('run'), 'saved');
});

test('task selection references are isolated by both tenant and user', () => {
  assert.notEqual(taskStorageKey('tenant-a', 'user-a'), taskStorageKey('tenant-a', 'user-b'));
  assert.notEqual(taskStorageKey('tenant-a', 'user-a'), taskStorageKey('tenant-b', 'user-a'));
});
