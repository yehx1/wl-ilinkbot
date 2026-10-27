// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from '../src/App';

const page = { items: [], total: 0, limit: 100, offset: 0 };

function responseFor(url: string): unknown {
  if (url.includes('/api/status')) return { wechat: { state: 'connected' }, scheduler: { running: true }, ai: { provider: 'codex', model: 'Codex 默认模型' }, smtp: { configured: false } };
  if (url.includes('/api/schedules') || url.includes('/api/control/commands')) return [];
  if (url.includes('/api/user-notes')) return { items: {} };
  return page;
}

describe('tool favorites', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async input => ({ ok: true, json: async () => responseFor(String(input)) })));
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('defaults Codex to common and remembers when it is removed', () => {
    const first = render(<App />);
    fireEvent.click(screen.getAllByRole('button', { name: '运行工具' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '常用 1' }));
    const tools = first.container.querySelector('#tools');
    expect(tools).not.toBeNull();
    expect(within(tools as HTMLElement).getByRole('heading', { name: 'Codex' })).toBeTruthy();
    fireEvent.click(within(tools as HTMLElement).getByRole('button', { name: '★ 取消常用' }));
    expect(within(tools as HTMLElement).getByText('常用工具为空。')).toBeTruthy();

    first.unmount();
    const second = render(<App />);
    fireEvent.click(screen.getAllByRole('button', { name: '运行工具' })[0]);
    fireEvent.click(screen.getByRole('button', { name: '常用 0' }));
    expect(within(second.container.querySelector('#tools') as HTMLElement).getByText('常用工具为空。')).toBeTruthy();
  });
});
