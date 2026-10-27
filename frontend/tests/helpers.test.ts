import { describe, expect, it } from 'vitest';
import { chunkText } from '../src/messaging';
import { parseCommand } from '../src/router';

describe('message helpers', () => {
  it('parses only explicit commands', () => {
    expect(parseCommand('')).toEqual({ kind: 'help', input: '' });
    expect(parseCommand('/codex hello')).toEqual({ kind: 'codex', input: 'hello' });
    expect(parseCommand('/control status')).toEqual({ kind: 'control', input: 'status' });
    expect(parseCommand('/codex')).toEqual({ kind: 'codex', input: '' });
  });
  it('splits long unicode text at a natural boundary', () => {
    expect(chunkText('abc def ghi', 5)).toEqual(['abc', 'def', 'ghi']);
  });
});
