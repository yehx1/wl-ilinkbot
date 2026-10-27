import { describe, expect, it } from 'vitest';
import { parseCommand } from '../src/router';

describe('parseCommand', () => {
  it('rejects malformed command arguments without side effects', () => {
    expect(parseCommand('/control')).toEqual({ kind: 'control', input: '' });
    expect(parseCommand('plain text').kind).toBe('unknown');
  });
});
