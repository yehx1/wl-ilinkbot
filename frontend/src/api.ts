export type WechatStatus = { state: string; qr_url?: string | null; error?: string | null; updated_at?: string | null };
export type Schedule = { id: number; name: string; kind: 'cron' | 'once'; spec?: string | null; run_at?: string | null; timezone: string; target_user_id: string; message: string; enabled: number; next_run_at?: string | null; last_run_at?: string | null };
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };
export type Execution = { id: number; scheduled_for: string; started_at: string; finished_at?: string; status: string; error?: string | null };
export type Conversation = { user_id: string; message_count: number; last_message_at: string; last_direction: 'inbound' | 'outbound'; last_text: string };
export type MessageItem = { id: number; direction: 'inbound' | 'outbound'; user_id: string; message_id?: string | null; message_type?: string | null; text: string; created_at: string };
export type MessageHistory = { items: MessageItem[]; has_more: boolean; next_before_id?: number | null };
export type MessageTarget = { user_id: string; message_count: number; last_message_at: string };
export type GeneratedTool = { id: number; description: string; definition: string; enabled: number };
export type TodoItem = { id: number; user_id: string; text: string; done: number; created_at: string; completed_at?: string | null };
export type AppStatus = { wechat: WechatStatus; scheduler: { running: boolean }; ai?: { provider: string; model: string; openai_model?: string; openai_base_url?: string; openai_api_mode?: string; codex_model?: string; api_key_configured?: boolean }; smtp?: { configured: boolean; host?: string; port?: number; user?: string; from?: string; ssl?: boolean; password_configured?: boolean } };

const base = import.meta.env.VITE_API_BASE ?? '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, { headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) }, ...init });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail ?? '请求失败');
  return body as T;
}

export const api = {
  updateProviderSettings: (values: Record<string, string>) => request<{ ok: boolean; updated: string[]; restart_required: boolean }>('/api/settings/providers', { method: 'PATCH', body: JSON.stringify(values) }),
  runAiTool: (tool: string, input: string) => request<{ output: string }>('/api/tools/ai/run', { method: 'POST', body: JSON.stringify({ tool, input }) }),
  runToolSkill: (input: string) => request<{ output: string }>('/api/tools/skill/run', { method: 'POST', body: JSON.stringify({ input }) }),
  runOcr: (dataBase64: string, mime: string) => request<{ output: string }>('/api/tools/ocr', { method: 'POST', body: JSON.stringify({ data_base64: dataBase64, mime }) }),
  convertFile: (dataBase64: string, filename: string, target: string) => request<{ filename: string; data_base64: string }>('/api/tools/convert', { method: 'POST', body: JSON.stringify({ data_base64: dataBase64, filename, target }) }),
  createNaturalSchedule: (kind: 'remind' | 'repeat', input: string, userId: string) => request<Schedule>('/api/tools/schedule', { method: 'POST', body: JSON.stringify({ kind, input, user_id: userId }) }),
  sendMail: (body: { recipient: string; subject: string; body: string; data_base64?: string; filename?: string }) => request<{ output: string }>('/api/tools/mail/send', { method: 'POST', body: JSON.stringify(body) }),
  todos: (userId: string) => request<{ items: TodoItem[] }>(`/api/tools/todos?user_id=${encodeURIComponent(userId)}`),
  createTodo: (userId: string, text: string) => request<TodoItem>('/api/tools/todos', { method: 'POST', body: JSON.stringify({ user_id: userId, text }) }),
  completeTodo: (id: number, userId: string) => request<{ ok: boolean }>(`/api/tools/todos/${id}/complete`, { method: 'POST', body: JSON.stringify({ user_id: userId }) }),
  createGeneratedTool: (userId: string, description: string) => request<GeneratedTool>('/api/tools/generated', { method: 'POST', body: JSON.stringify({ user_id: userId, description }) }),
  runGeneratedTool: (id: number, userId: string, input: string) => request<{ output: string }>(`/api/tools/generated/${id}/run`, { method: 'POST', body: JSON.stringify({ user_id: userId, input }) }),
  updateGeneratedTool: (id: number, userId: string, enabled: boolean) => request<{ ok: boolean; id: number; enabled: boolean }>('/api/tools/generated/' + id + '?user_id=' + encodeURIComponent(userId) + '&enabled=' + enabled, { method: 'PATCH' }),
  generatedTools: (userId: string) => request<{ items: GeneratedTool[] }>(`/api/tools/generated?user_id=${encodeURIComponent(userId)}`),
  testCodex: (text: string) => request<{ output: string }>('/api/tools/codex/test', { method: 'POST', body: JSON.stringify({ target_user_id: 'web', text }) }),
  status: () => request<AppStatus>('/api/status'),
  reconnect: () => request<AppStatus>('/api/wechat/reconnect', { method: 'POST' }),
  schedules: () => request<Schedule[]>('/api/schedules'),
  createSchedule: (body: unknown) => request<Schedule>('/api/schedules', { method: 'POST', body: JSON.stringify(body) }),
  patchSchedule: (id: number, body: unknown) => request<Schedule>(`/api/schedules/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteSchedule: (id: number) => request<{ ok: boolean }>(`/api/schedules/${id}`, { method: 'DELETE' }),
  executions: (id: number) => request<Execution[]>(`/api/schedules/${id}/executions`),
  conversations: () => request<Page<Conversation>>('/api/conversations?limit=100'),
  messages: (userId: string, beforeId?: number | null) => request<MessageHistory>(`/api/messages?user_id=${encodeURIComponent(userId)}&limit=100${beforeId ? `&before_id=${beforeId}` : ''}`),
  deleteMessages: (userId: string, messageIds: number[]) => request<{ ok: boolean; deleted: number }>('/api/messages', { method: 'DELETE', body: JSON.stringify({ user_id: userId, message_ids: messageIds }) }),
  messageTargets: () => request<Page<MessageTarget>>('/api/message-targets?limit=200'),
  userNotes: () => request<{ items: Record<string, string> }>('/api/user-notes'),
  updateUserNote: (userId: string, note: string) => request<{ ok: boolean; user_id: string; note: string }>(`/api/user-notes/${encodeURIComponent(userId)}`, { method: 'PATCH', body: JSON.stringify({ note }) }),
  sendMessage: (targetUserId: string, text: string) => request<{ ok: boolean; target_user_id: string; sent_parts: number }>('/api/messages', { method: 'POST', body: JSON.stringify({ target_user_id: targetUserId, text }) }),
  sendMedia: (targetUserId: string, filename: string, dataBase64: string, caption: string) => request<{ target_user_id: string; sent_parts: number }>('/api/messages/media', { method: 'POST', body: JSON.stringify({ target_user_id: targetUserId, filename, data_base64: dataBase64, caption }) }),
  runs: () => request<Page<Record<string, unknown>>>('/api/runs?limit=20'),
  logs: () => request<Page<Record<string, unknown>>>('/api/logs?limit=20'),
  commands: () => request<Array<{ id: string; label: string }>>('/api/control/commands'),
  executeControl: (id: string) => request<{ output: string }>(`/api/control/commands/${encodeURIComponent(id)}`, { method: 'POST' }),
  desktopScreenshot: (screenIndex: number | null = null) => request<{ mime: string; data_base64: string }>('/api/tools/desktop/screenshot', { method: 'POST', body: JSON.stringify({ screen_index: screenIndex === null ? 'all' : screenIndex }) }),
  focusDesktopWindow: (title: string, maximize = true) => request<{ output: string }>('/api/tools/desktop/focus', { method: 'POST', body: JSON.stringify({ title, maximize }) }),
};
