export type Command = { kind: 'help' | 'codex' | 'control' | 'unknown'; input: string };
export function parseCommand(text: string): Command {
  const value = text.trim();
  if (!value || value === '/help') return { kind: 'help', input: '' };
  if (value.startsWith('/codex ')) return { kind: 'codex', input: value.slice(7).trim() };
  if (value === '/codex') return { kind: 'codex', input: '' };
  if (value.startsWith('/control ')) return { kind: 'control', input: value.slice(9).trim() };
  if (value === '/control') return { kind: 'control', input: '' };
  return { kind: 'unknown', input: value };
}
