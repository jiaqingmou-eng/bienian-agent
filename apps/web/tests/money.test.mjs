import { test } from 'node:test';
import assert from 'node:assert/strict';
import { yuanToFen } from '../src/lib/money.ts';

test('decimal input is converted exactly without floating-point rounding', () => {
  assert.equal(yuanToFen('1.01'), 101);
  assert.equal(yuanToFen('0.29'), 29);
  assert.equal(yuanToFen('600'), 60000);
  assert.equal(yuanToFen('900.5'), 90050);
  assert.equal(yuanToFen(' 0.01 '), 1);
  assert.equal(yuanToFen('1000000.00'), 100000000);
});

test('invalid, fractional-fen and out-of-range inputs are rejected', () => {
  for (const input of ['', '0', '-1', '1.001', '1e2', 'NaN', '01.00', '1000000.01', '1,000', '.5']) {
    assert.throws(() => yuanToFen(input), Error, input);
  }
});
