import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { api, type AppStatus, type Conversation, type Execution, type GeneratedTool, type MessageItem, type MessageTarget, type Schedule, type TodoItem } from './api';

type Form = { name: string; kind: 'cron' | 'once'; spec: string; run_at: string; timezone: string; target_user_id: string; message: string };
const initialForm: Form = { name: '', kind: 'cron', spec: '*/5 * * * *', run_at: '', timezone: 'Asia/Shanghai', target_user_id: '', message: '' };
type ToolDefinition = { label: string; description: string; placeholder: string; usage: string; features: string[]; commands?: string[] };
const aiTools: Record<string, ToolDefinition> = {
  Codex: {
    label: 'Codex',
    description: '通过微信调用本机 Codex，连续处理代码、文件、图片和复杂任务，并可将生成文件回传微信。',
    placeholder: '例如：检查项目中的测试失败并修复；或分析我发送的图片',
    usage: '发送 /codex 可进入持续会话，之后直接发送任务或附件；也可以用 /codex 任务内容 单次执行。网页中的测试会使用独立的 web 会话。',
    features: ['按微信用户保持 Codex 上下文，支持新建、查看和切换会话', '图片作为视觉输入，其他文件可由 Codex 读取和分析', '按本机 Codex 权限在配置的工作目录中检查代码、编辑文件和运行命令', '支持 Markdown 与可复制代码块；生成或引用的受支持文件可回传微信（单文件不超过 20 MB）'],
    commands: ['/codex 任务内容', '/codex（进入持续会话）', '/new（新建会话）', '/sessions（查看会话）', '/session 2（切换会话）', '/exit（退出模式）'],
  },
  translate: { label: '翻译', description: '按指定目标语言翻译文本；未指定时自动进行中英文互译。', placeholder: '例如：翻译成日语：明天见', usage: '输入目标语言和原文，模型只返回译文，适合短句、邮件和成段内容。', features: ['自动识别原文语言', '可在输入中指定日语、英语等目标语言'], commands: ['/translate 内容'] },
  summary: { label: '总结', description: '压缩长文本，提取关键信息并以要点形式输出。', placeholder: '粘贴需要总结的内容', usage: '粘贴文章、聊天记录或资料，也可以说明希望保留的重点和输出长度。', features: ['归纳核心结论', '输出清晰的关键要点'], commands: ['/summary 内容'] },
  abstract: { label: '摘要', description: '把原文改写成简洁、连贯、可直接引用的摘要。', placeholder: '粘贴需要生成摘要的内容', usage: '适合为文章、报告和方案生成一段式简介。', features: ['保留原文主旨', '输出紧凑连贯的摘要'], commands: ['/abstract 内容'] },
  meeting: { label: '会议纪要', description: '从会议记录中整理议题、结论、待办、负责人和截止时间。', placeholder: '粘贴会议记录或录音转写文本', usage: '粘贴会议聊天或录音转写；原文未提供的信息会明确标记为未指定。', features: ['区分议题与结论', '结构化提取负责人、待办和截止时间'], commands: ['/meeting 内容'] },
  skill: { label: '智能编排 Skill', description: '让模型按任务要求组合多个内置工具，完成翻译、总结、纪要等多步处理。', placeholder: '例如：先翻译成英文，再总结成三点', usage: '描述处理顺序和最终结果；编排只返回最终文本，不执行系统命令或产生外部副作用。', features: ['自动选择并串联适合的文本工具', '适合一步命令完成多阶段处理'], commands: ['/skill 先翻译再总结', '/编排 先摘要再整理成会议纪要'] },
};
const utilityTools: Record<string, ToolDefinition> = {
  ocr: { label: '图片文字提取', description: '使用当前模型识别图片中的文字，支持常见图片格式。', placeholder: '请选择图片', usage: '选择不超过 10 MB 的图片；在微信中发送图片并附带 /ocr 即可识别。', features: ['支持 PNG、JPEG、WebP 等常见图片', '可使用 Codex 视觉能力或已配置的大模型 API'], commands: ['/ocr（附图片）'] },
  convert: { label: '格式转换', description: '在 JSON、CSV、Markdown、文本、HTML及图片 PDF 之间执行受支持的转换。', placeholder: '请选择文件', usage: '上传不超过 20 MB 的文件并选择目标格式；仅提供明确支持的安全转换。', features: ['JSON 数组与 CSV 双向转换', 'Markdown 与纯文本互转、Markdown 转 HTML', 'PNG、JPG、WebP 图片转 PDF'], commands: ['发送文件并附加 /convert json'] },
  todo: { label: '待办事项', description: '为当前微信用户添加、查看和完成独立的待办事项。', placeholder: '输入待办内容', usage: '网页操作会关联当前选中的微信用户；微信中可直接添加、列出或完成待办。', features: ['不同微信用户的待办彼此独立', '支持查看未完成项和标记完成'], commands: ['/todo 添加任务', '/todo list', '/todo done 1'] },
  remind: { label: '提醒', description: '用自然语言解析时间，并创建发送给当前微信用户的一次性提醒。', placeholder: '例如：20分钟后提醒我开会', usage: '描述提醒时间和内容，创建后可在“定时任务”中查看、修改或停用。', features: ['支持“20 分钟后”等自然语言时间', '到期后通过微信发送提醒'], commands: ['/remind 20分钟后提醒我开会'] },
  repeat: { label: '重复任务', description: '用自然语言创建每天定时执行并向微信发送消息的重复任务。', placeholder: '例如：每天9点发送天气', usage: '描述每日执行时间与消息内容，创建后可在“定时任务”中管理。', features: ['自动解析每日执行时间', '支持后续查看、编辑、停用和删除'], commands: ['/repeat 每天9点发送天气'] },
  mail: { label: '邮件', description: '通过已配置的 SMTP 发送邮件，支持预览确认和可选附件。', placeholder: '收件人 | 主题 | 正文', usage: '按“收件人 | 主题 | 正文”填写；网页会在发送前确认，微信中需用 /mail confirm 二次确认。', features: ['发送前确认，避免误发', '支持随邮件附加一个文件'], commands: ['/mail 收件人 | 主题 | 正文', '/mail confirm', '/mail cancel'] },
  createTool: { label: '生成工具', description: '用自然语言生成可复用的文本处理工具，审核启用后供指定微信用户运行。', placeholder: '例如：帮我把客服回复改写得礼貌专业', usage: '先描述工具用途；生成结果不会立即执行，需在“已生成工具”中检查定义并启用。', features: ['生成结构化、安全的文本工具定义', '必须人工审核启用，不执行 Shell 或直接外部操作'], commands: ['/create-tool 用自然语言描述工具', '/run-tool confirm 工具ID 输入内容'] },
  screenshot: { label: '屏幕截图', description: '获取当前 Windows 桌面的全部屏幕或指定屏幕截图。', placeholder: '点击执行获取截图', usage: '选择全部屏幕或屏幕编号后执行，截图会直接在页面中预览。', features: ['支持多显示器完整截图', '支持按从 0 开始的屏幕编号截图'] },
  focusWindow: { label: '切换并最大化应用', description: '按窗口标题查找应用，将其切换到前台并可最大化。', placeholder: '例如：Chrome、微信、记事本', usage: '输入窗口标题中的可识别文字，例如“Chrome”或“记事本”。', features: ['按标题匹配已打开的窗口', '切换到前台并最大化窗口'] },
  control: { label: '电脑控制', description: '只执行管理员预先配置的固定命令，不接受任意 Shell 指令。', placeholder: '请选择命令', usage: '从白名单中选择命令；微信中使用 /control 命令ID 执行相同操作。', features: ['命令及参数均来自本地白名单', '每次执行都会记录结果，拒绝未配置命令'], commands: ['/control 命令ID'] },
};
const allTools = { ...aiTools, ...utilityTools };
const toolGroups: Record<string, string[]> = { AI: Object.keys(aiTools), 效率: Object.keys(utilityTools) };
const favoriteStorageKey = 'ilinkbot-favorite-tools';
const favoriteStorageVersionKey = 'ilinkbot-favorite-tools-version';
function loadFavoriteTools(): string[] {
  try {
    const stored = JSON.parse(localStorage.getItem(favoriteStorageKey) ?? '[]');
    const favorites = Array.isArray(stored) ? stored.filter((value): value is string => typeof value === 'string') : [];
    if (localStorage.getItem(favoriteStorageVersionKey) !== '2') {
      const migrated = favorites.includes('Codex') ? favorites : ['Codex', ...favorites];
      localStorage.setItem(favoriteStorageKey, JSON.stringify(migrated));
      localStorage.setItem(favoriteStorageVersionKey, '2');
      return migrated;
    }
    return favorites;
  } catch { return ['Codex']; }
}

function fileToBase64(file: File): Promise<string> {
  if (file.size > 20 * 1024 * 1024) return Promise.reject(new Error('文件不能超过 20 MB'));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.readAsDataURL(file);
  });
}

function downloadBase64(filename: string, data: string): void {
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const url = URL.createObjectURL(new Blob([bytes]));
  const anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click();
  URL.revokeObjectURL(url);
}
function loadUserNotes(): Record<string, string> {
  try {
    const value = JSON.parse(localStorage.getItem('ilinkbot-user-notes') ?? '{}');
    return value && typeof value === 'object' ? value : {};
  } catch { return {}; }
}
function escapeHtml(value: string): string {
  return value.replace(/[&<>\"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char] ?? char));
}

function markdownHtml(value: string): string {
  // Keep fenced blocks intact so the message UI can offer a one-click copy
  // action. Escaping happens before inserting any generated HTML.
  const blocks: string[] = [];
  const protectedValue = value.replace(/^```([^\n]*)\n([\s\S]*?)^```\s*$/gm, (_match, language: string, code: string) => {
    const index = blocks.push(`<pre class="markdown-code"><button type="button" class="copy-code" data-copy-code="${encodeURIComponent(code)}">复制</button><code class="language-${escapeHtml(language.trim() || 'text')}">${escapeHtml(code.replace(/\n$/, ''))}</code></pre>`) - 1;
    return `\u0000CODE${index}\u0000`;
  });
  return escapeHtml(protectedValue)
    .replace(/^### (.*)$/gm, '<h4>$1</h4>').replace(/^## (.*)$/gm, '<h3>$1</h3>').replace(/^# (.*)$/gm, '<h2>$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`\n]+)`/g, '<code>$1</code>').replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\n/g, '<br />')
    .replace(/\u0000CODE(\d+)\u0000/g, (_match, index: string) => blocks[Number(index)]);
}

function App() {
  const [status, setStatus] = useState<AppStatus>();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [commands, setCommands] = useState<Array<{ id: string; label: string }>>([]);
  const [form, setForm] = useState<Form>(initialForm);
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [controlOutput, setControlOutput] = useState('');
  const [history, setHistory] = useState<Record<number, Execution[]>>({});
  const [expandedHistory, setExpandedHistory] = useState<number | null>(null);
  const [runs, setRuns] = useState<Array<Record<string, unknown>>>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [targets, setTargets] = useState<MessageTarget[]>([]);
  const [selectedUser, setSelectedUser] = useState('');
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [olderCursor, setOlderCursor] = useState<number | null>(null);
  const [hasOlder, setHasOlder] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [messageBusy, setMessageBusy] = useState(false);
  const [messageError, setMessageError] = useState('');
  const [attachment, setAttachment] = useState<File | null>(null);
  const [selectedMessages, setSelectedMessages] = useState<number[]>([]);
  const [userNotes, setUserNotes] = useState<Record<string, string>>(loadUserNotes);
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const messageStreamRef = useRef<HTMLDivElement>(null);
  const messageCountRef = useRef(0);
  const [activeSection, setActiveSection] = useState('connection');
  const [userPickerOpen, setUserPickerOpen] = useState(false);
  const [repeatPreset, setRepeatPreset] = useState('every5');
  const [currentTime, setCurrentTime] = useState(() => new Date());
  const [selectedTool, setSelectedTool] = useState<string | null>(null);
  const [toolInput, setToolInput] = useState('');
  const [toolOutput, setToolOutput] = useState('');
  const [toolBusy, setToolBusy] = useState(false);
  const [toolTab, setToolTab] = useState('全部');
  const [favoriteTools, setFavoriteTools] = useState<string[]>(loadFavoriteTools);
  const [toolFile, setToolFile] = useState<File | null>(null);
  const [screenIndex, setScreenIndex] = useState<number | null>(null);
  const [convertTarget, setConvertTarget] = useState('pdf');
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [providerSaving, setProviderSaving] = useState(false);
  const [providerNotice, setProviderNotice] = useState('');
  const providerHydratedRef = useRef(false);
  const saveProviderSettings = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); setProviderSaving(true); setProviderNotice(''); const data = new FormData(event.currentTarget); const values = Object.fromEntries(data.entries()) as Record<string, string>; values.SMTP_SSL = data.has('SMTP_SSL') ? 'true' : 'false'; try { const result = await api.updateProviderSettings(values); setProviderNotice(`已保存并生效：${result.updated.join('、')}`); setStatus(await api.status()); } catch (err) { setProviderNotice(err instanceof Error ? err.message : '保存失败'); } finally { setProviderSaving(false); } };
  const [generatedTools, setGeneratedTools] = useState<GeneratedTool[]>([]);
  const refreshGeneratedTools = async () => { if (selectedUser) setGeneratedTools((await api.generatedTools(selectedUser)).items); };
  const refreshTodos = async () => { if (selectedUser) setTodos((await api.todos(selectedUser)).items); };
  const openTool = (id: string) => { setSelectedTool(id); setToolInput(''); setToolOutput(''); setToolFile(null); setScreenIndex(null); if (id === 'todo') void refreshTodos(); };
  const testTool = async () => {
    if (!selectedTool) return;
    setToolBusy(true); setToolOutput('');
    try {
      let output = '';
      if (selectedTool === 'skill') output = (await api.runToolSkill(toolInput.trim())).output;
      else if (selectedTool === 'Codex') output = (await api.testCodex(toolInput.trim())).output;
      else if (selectedTool === 'control') output = (await api.executeControl(toolInput)).output;
      else if (selectedTool in aiTools) output = (await api.runAiTool(selectedTool, toolInput.trim())).output;
      else if (selectedTool === 'ocr') {
        if (!toolFile) throw new Error('请选择图片');
        if (toolFile.size > 10 * 1024 * 1024) throw new Error('OCR 图片不能超过 10 MB');
        output = (await api.runOcr(await fileToBase64(toolFile), toolFile.type || 'image/jpeg')).output;
      } else if (selectedTool === 'convert') {
        if (!toolFile) throw new Error('请选择文件');
        const result = await api.convertFile(await fileToBase64(toolFile), toolFile.name, convertTarget);
        downloadBase64(result.filename, result.data_base64); output = `转换完成，已下载 ${result.filename}`;
      } else if (selectedTool === 'todo') {
        if (!selectedUser) throw new Error('请先选择微信用户');
        await api.createTodo(selectedUser, toolInput.trim()); await refreshTodos(); setToolInput(''); output = '待办已添加';
      } else if (selectedTool === 'remind' || selectedTool === 'repeat') {
        if (!selectedUser) throw new Error('请先选择微信用户');
        const result = await api.createNaturalSchedule(selectedTool, toolInput.trim(), selectedUser); output = `任务 #${result.id} 已创建`; await load();
      } else if (selectedTool === 'mail') {
        const [recipient = '', subject = '', ...bodyParts] = toolInput.split('|');
        const body = bodyParts.join('|').trim();
        if (!recipient.trim() || !subject.trim() || !body) throw new Error('请输入：收件人 | 主题 | 正文');
        if (!window.confirm(`确认向 ${recipient.trim()} 发送邮件“${subject.trim()}”吗？`)) { setToolBusy(false); return; }
        const mailPayload: { recipient: string; subject: string; body: string; data_base64?: string; filename?: string } = { recipient: recipient.trim(), subject: subject.trim(), body };
        if (toolFile) { mailPayload.data_base64 = await fileToBase64(toolFile); mailPayload.filename = toolFile.name; }
        output = (await api.sendMail(mailPayload)).output;
      } else if (selectedTool === 'createTool') {
        if (!selectedUser) throw new Error('请先选择微信用户');
        const result = await api.createGeneratedTool(selectedUser, toolInput.trim()); await refreshGeneratedTools(); output = `工具 #${result.id} 已生成，请审核定义后启用。`;
      } else if (selectedTool === 'screenshot') {
        const result = await api.desktopScreenshot(screenIndex); output = `data:${result.mime};base64,${result.data_base64}`;
      } else if (selectedTool === 'focusWindow') {
        output = (await api.focusDesktopWindow(toolInput.trim(), true)).output;
      } else if (selectedTool.startsWith('generated:')) {
        if (!selectedUser) throw new Error('请先选择微信用户');
        const id = Number(selectedTool.split(':')[1]); output = (await api.runGeneratedTool(id, selectedUser, toolInput.trim())).output;
      }
      setToolOutput(output);
    } catch (err) { setToolOutput(err instanceof Error ? err.message : '执行失败'); }
    finally { setToolBusy(false); }
  };
  useEffect(() => { if (activeSection === 'tools' && selectedUser) void api.generatedTools(selectedUser).then(result => setGeneratedTools(result.items)).catch(() => setGeneratedTools([])); }, [activeSection, selectedUser]);
  useEffect(() => {
    if (!status || providerHydratedRef.current) return;
    const form = document.querySelector<HTMLFormElement>('.provider-form');
    if (!form) return;
    const setValue = (name: string, value: string) => {
      const field = form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | null;
      if (field) field.value = value;
    };
    setValue('AI_PROVIDER', status.ai?.provider ?? 'codex');
    setValue('OPENAI_MODEL', status.ai?.openai_model ?? '');
    setValue('OPENAI_BASE_URL', status.ai?.openai_base_url ?? '');
    setValue('OPENAI_API_MODE', status.ai?.openai_api_mode ?? 'responses');
    setValue('CODEX_MODEL', status.ai?.codex_model ?? '');
    setValue('SMTP_HOST', status.smtp?.host ?? '');
    setValue('SMTP_PORT', String(status.smtp?.port ?? 587));
    setValue('SMTP_USER', status.smtp?.user ?? '');
    setValue('SMTP_FROM', status.smtp?.from ?? '');
    const ssl = form.elements.namedItem('SMTP_SSL') as HTMLInputElement | null;
    if (ssl) ssl.checked = Boolean(status.smtp?.ssl);
    providerHydratedRef.current = true;
  }, [status]);

  const loadMessages = useCallback(async (userId: string, beforeId?: number | null, appendOlder = false) => {
    if (!userId) { setMessages([]); setHasOlder(false); setOlderCursor(null); return; }
    setMessageBusy(true);
    try {
      const result = await api.messages(userId, beforeId);
      setMessages(current => appendOlder ? [...result.items, ...current] : result.items);
      setHasOlder(result.has_more);
      setOlderCursor(result.next_before_id ?? null);
      setMessageError('');
    } catch (err) {
      setMessageError(err instanceof Error ? err.message : '消息记录加载失败');
    } finally { setMessageBusy(false); }
  }, []);

  const load = useCallback(async () => {
    try {
      const [nextStatus, nextSchedules, nextCommands, nextRuns, nextConversations, nextTargets, nextNotes] = await Promise.all([
        api.status(), api.schedules(), api.commands(), api.runs(), api.conversations(), api.messageTargets(), api.userNotes(),
      ]);
      setStatus(nextStatus); setSchedules(nextSchedules); setCommands(nextCommands); setRuns(nextRuns.items);
      setConversations(nextConversations.items); setTargets(nextTargets.items); setUserNotes(current => ({ ...current, ...nextNotes.items })); setError('');
      setSelectedUser(current => current || nextTargets.items[0]?.user_id || nextConversations.items[0]?.user_id || '');
    } catch (err) { setError(err instanceof Error ? err.message : '加载失败'); }
  }, []);

  useEffect(() => { void load(); const timer = window.setInterval(() => void load(), 5000); return () => window.clearInterval(timer); }, [load]);
  useEffect(() => { if (selectedUser) void loadMessages(selectedUser); }, [selectedUser, loadMessages]);
  useEffect(() => { if (activeSection === 'messages') window.setTimeout(() => messageStreamRef.current?.scrollTo({ top: messageStreamRef.current.scrollHeight, behavior: 'smooth' }), 0); }, [activeSection]);
  useEffect(() => { const timer = window.setInterval(() => setCurrentTime(new Date()), 1000); return () => window.clearInterval(timer); }, []);
  useEffect(() => { setSelectedMessages([]); }, [selectedUser]);
  useEffect(() => { if (messages.length !== messageCountRef.current) { messageCountRef.current = messages.length; messageStreamRef.current?.scrollTo({ top: messageStreamRef.current.scrollHeight, behavior: 'smooth' }); } }, [messages]);
  useEffect(() => {
    const stream = messageStreamRef.current;
    if (!stream) return undefined;
    const copyCode = async (event: Event) => {
      const target = event.target as HTMLElement;
      const button = target.closest<HTMLButtonElement>('[data-copy-code]');
      if (!button) return;
      const code = decodeURIComponent(button.dataset.copyCode ?? '');
      try {
        await navigator.clipboard.writeText(code);
        const original = button.textContent;
        button.textContent = '已复制';
        window.setTimeout(() => { button.textContent = original ?? '复制'; }, 1200);
      } catch { button.textContent = '复制失败'; }
    };
    stream.addEventListener('click', copyCode);
    return () => stream.removeEventListener('click', copyCode);
  }, [messages]);
  useEffect(() => {
    if (!selectedUser) return undefined;
    const timer = window.setInterval(() => void loadMessages(selectedUser), 5000);
    return () => window.clearInterval(timer);
  }, [selectedUser, loadMessages]);

  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const presetSpecs: Record<string, string> = { every5: '*/5 * * * *', every15: '*/15 * * * *', hourly: '0 * * * *', daily: '0 9 * * *', weekly: '0 9 * * 1', monthly: '0 9 1 * *' };
      const payload = { ...form, spec: form.kind === 'cron' ? (presetSpecs[repeatPreset] ?? form.spec) : null, run_at: form.kind === 'once' ? new Date(form.run_at).toISOString() : null, enabled: true };
      if (editing) await api.patchSchedule(editing, payload); else await api.createSchedule(payload);
      setForm(initialForm); setEditing(null); setNotice('任务已保存'); await load();
    } catch (err) { setError(err instanceof Error ? err.message : '保存失败'); } finally { setBusy(false); }
  };
  const remove = async (id: number) => { if (!window.confirm('确认删除这个任务？')) return; try { await api.deleteSchedule(id); await load(); } catch (err) { setError(err instanceof Error ? err.message : '删除失败'); } };
  const toggle = async (schedule: Schedule) => { try { await api.patchSchedule(schedule.id, { enabled: !schedule.enabled }); await load(); } catch (err) { setError(err instanceof Error ? err.message : '更新失败'); } };
  const reconnect = async () => {
    setBusy(true); setError('');
    try {
      let nextStatus = await api.reconnect();
      setStatus(nextStatus);
      // QR generation is asynchronous. Poll quickly after this explicit user
      // action instead of making them wait for the regular five-second refresh.
      const deadline = Date.now() + 10_000;
      const terminalStates = new Set(['connected', 'error', 'expired']);
      while (!nextStatus.wechat.qr_url && !terminalStates.has(nextStatus.wechat.state) && Date.now() < deadline) {
        await new Promise(resolve => window.setTimeout(resolve, 250));
        nextStatus = await api.status();
        setStatus(nextStatus);
      }
    } catch (err) { setError(err instanceof Error ? err.message : '重连失败'); }
    finally { setBusy(false); }
  };
  const execute = async (id: string) => { if (!window.confirm('确认执行此白名单命令？')) return; try { const result = await api.executeControl(id); setControlOutput(result.output); } catch (err) { setError(err instanceof Error ? err.message : '执行失败'); } };
  const showHistory = async (id: number) => { if (expandedHistory === id) { setExpandedHistory(null); return; } try { setHistory({ ...history, [id]: await api.executions(id) }); setExpandedHistory(id); } catch (err) { setError(err instanceof Error ? err.message : '读取执行记录失败'); } };
  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedUser || (!draft.trim() && !attachment)) return;
    setSending(true); setMessageError('');
    try {
      const result = attachment
        ? await api.sendMedia(selectedUser, attachment.name, await fileToBase64(attachment), draft.trim())
        : await api.sendMessage(selectedUser, draft.trim());
      setDraft(''); setAttachment(null); setNotice(`消息已发送（${result.sent_parts} 段）`);
      await Promise.all([load(), loadMessages(selectedUser)]);
    } catch (err) { setMessageError(err instanceof Error ? err.message : '消息发送失败'); }
    finally { setSending(false); }
  };
  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } };
  const formatRunAt = (value: string | null | undefined) => value ? new Date(value).toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const userDirectory = Array.from(new Map([...conversations.map(item => ({ user_id: item.user_id })), ...targets.map(item => ({ user_id: item.user_id })), ...messages.map(item => ({ user_id: item.user_id }))].map(item => [item.user_id, item])).values());
  const selectedIsSendable = targets.some(target => target.user_id === selectedUser);
  const selectedGeneratedTool = selectedTool?.startsWith('generated:') ? generatedTools.find(item => item.id === Number(selectedTool.split(':')[1])) : undefined;
  const toolInputReady = selectedTool === 'screenshot' || (selectedTool === 'ocr' || selectedTool === 'convert' ? Boolean(toolFile) : Boolean(toolInput.trim()));
  const saveNote = (userId: string, value: string) => { const next = { ...userNotes, [userId]: value }; setUserNotes(next); localStorage.setItem('ilinkbot-user-notes', JSON.stringify(next)); setEditingNote(null); void api.updateUserNote(userId, value).catch(err => setError(err instanceof Error ? err.message : '备注保存失败')); };
  const deleteSelectedMessages = async () => {
    if (!selectedUser || !selectedMessages.length) return;
    if (!window.confirm(`确认删除选中的 ${selectedMessages.length} 条消息吗？`)) return;
    setMessageBusy(true); setMessageError('');
    try {
      await api.deleteMessages(selectedUser, selectedMessages);
      setSelectedMessages([]);
      await load();
      await loadMessages(selectedUser, null, false);
    } catch (err) { setMessageError(err instanceof Error ? err.message : '删除消息失败'); }
    finally { setMessageBusy(false); }
  };
  const displayTools = Object.entries(allTools);
  const filteredTools = displayTools.filter(([id]) => toolTab === '全部' ? true : toolTab === '常用' ? favoriteTools.includes(id) : toolGroups[toolTab]?.includes(id));
  const selectedToolDefinition = selectedTool ? allTools[selectedTool] : undefined;
  const toggleFavorite = (id: string) => setFavoriteTools(current => {
    const next = current.includes(id) ? current.filter(value => value !== id) : [...current, id];
    localStorage.setItem(favoriteStorageKey, JSON.stringify(next));
    return next;
  });

  return <main className={`shell section-${activeSection}`}><nav className="topbar"><div className="brand-mark">WL-<span>iLink</span>Bot</div><div className="top-nav">{[["connection","微信连接"],["messages","消息中心"],["schedules","定时任务"],["tools","运行工具"],["ai","模型设置"]].map(([id,label]) => <button key={id} className={activeSection === id ? "" : "inactive"} onClick={() => setActiveSection(id)}>{label}</button>)}</div></nav><div className="workspace"><aside className="sidebar"><p className="sidebar-label">Workspace</p>{[["connection","微信连接"],["messages","消息中心"],["schedules","定时任务"],["tools","运行工具"],["ai","模型设置"]].map(([id,label]) => <button key={id} className={activeSection === id ? "active" : ""} onClick={() => setActiveSection(id)}>{label}</button>)}<button className={activeSection === "users" ? "active" : ""} onClick={() => setActiveSection("users")}>用户列表</button><div className="sidebar-footer">公众号：微聊机器人</div></aside><div className="content">
    <header><div><p className="eyebrow">LOCAL WECHAT CONTROLLER</p><h1>WL-iLinkBot 控制台</h1><p className="subtitle">通过微信管理消息、定时任务、Codex 和受控电脑操作。</p></div><div className={`status-pill ${status?.wechat.state ?? 'unknown'}`}><span />{status?.wechat.state ?? '加载中'}</div></header>
    {error && <div className="alert error">{error}</div>}{notice && <div className="alert success">{notice}</div>}
    <section id="connection" className="card connection"><div><p className="eyebrow"></p><h2>微信连接</h2><p className="muted">状态：{status?.wechat.state ?? '加载中'}{status?.wechat.error ? ` · ${status.wechat.error}` : ''}</p><button onClick={() => void reconnect()} disabled={busy}>{busy ? '正在生成二维码…' : '重新连接'}</button></div>{status?.wechat.qr_url ? <img className="qr" src={status.wechat.qr_url} alt="微信登录二维码" /> : <div className="qr-placeholder">{busy ? '正在生成连接二维码…' : '二维码将在需要扫码时显示'}</div>}</section>

    <section id="users" className="card user-management"><div className="section-heading"><div><p className="eyebrow"></p><h2>用户管理</h2><p className="muted">管理用户备注。</p></div><span className="count">{userDirectory.length} 个用户</span></div><div className="managed-users">{userDirectory.map(user => <div className="managed-user" key={user.user_id}><div><strong>{userNotes[user.user_id] || "未设置备注"}</strong><small>{user.user_id}</small></div>{editingNote === user.user_id ? <input autoFocus defaultValue={userNotes[user.user_id] ?? ""} onBlur={event => saveNote(user.user_id, event.target.value)} onKeyDown={event => { if (event.key === "Enter") saveNote(user.user_id, event.currentTarget.value); }} /> : <button className="secondary" onClick={() => setEditingNote(user.user_id)}>编辑备注</button>}</div>)}</div></section><section id="messages" className="card messages-card"><div className="section-heading"><div><p className="eyebrow"></p><h2>微信消息</h2></div><span className="count">{conversations.length} 个会话</span></div>
      {messageError && <div className="alert error">{messageError}</div>}
      <div className="chat-layout">
        <aside className="conversation-list"><div className="user-list-header">用户列表</div>{userDirectory.map(target => <div className="user-row" key={`user-${target.user_id}`}><button className={selectedUser === target.user_id ? "active" : ""} onClick={() => setSelectedUser(target.user_id)}><strong>{userNotes[target.user_id] || target.user_id}</strong><span>{target.user_id}</span></button>{editingNote === target.user_id ? <input autoFocus defaultValue={userNotes[target.user_id] ?? ""} onBlur={event => saveNote(target.user_id, event.target.value)} onKeyDown={event => { if (event.key === "Enter") saveNote(target.user_id, event.currentTarget.value); }} /> : <button className="note-edit" onClick={() => setEditingNote(target.user_id)}>✎</button>}</div>)}
          {conversations.map(item => <button className={selectedUser === item.user_id ? 'active' : ''} key={item.user_id} onClick={() => setSelectedUser(item.user_id)}><strong>{userNotes[item.user_id] || item.user_id}</strong><span>{item.last_direction === 'inbound' ? '收到' : '发出'} · {item.last_text || '空消息'}</span><small>{formatRunAt(item.last_message_at)} · {item.message_count} 条</small></button>)}
          {!conversations.length && <p className="empty">还没有微信消息。请先从微信向机器人发送消息。</p>}
        </aside>
        <div className="conversation-panel">
          <div className="conversation-toolbar"><div><strong>{selectedUser ? (userNotes[selectedUser] || selectedUser.slice(0, 6)) : '请选择会话'}</strong></div>{hasOlder && <button className="secondary" disabled={messageBusy} onClick={() => void loadMessages(selectedUser, olderCursor, true)}>加载更早消息</button>}</div>
          <div className="message-actions"><button className="secondary" disabled={messageBusy || !selectedMessages.length || !selectedUser} onClick={() => void deleteSelectedMessages()}>删除选中</button><button className="secondary" disabled={!messages.length} onClick={() => { if (window.confirm("确认清空当前会话消息吗？")) { setMessages([]); setSelectedMessages([]); } }}>清空消息</button></div><div className="message-stream" ref={messageStreamRef}>
            {messages.map(item => <article className={`message-bubble ${item.direction}`} key={item.id}><label className="message-select"><input type="checkbox" checked={selectedMessages.includes(item.id)} onChange={() => setSelectedMessages(current => current.includes(item.id) ? current.filter(id => id !== item.id) : [...current, item.id])} /></label><div className="markdown-body" dangerouslySetInnerHTML={{ __html: markdownHtml(item.text) }} /><small>{item.direction === 'inbound' ? '收到' : '发出'}{item.message_type ? ` · ${item.message_type}` : ''} · {formatRunAt(item.created_at)}</small></article>)}
            {!messages.length && <p className="empty">{selectedUser ? (messageBusy ? '正在加载消息…' : '这个会话暂无记录。') : '请从左侧选择一个会话。'}</p>}
          </div>
          <form className="message-composer" onSubmit={sendMessage}>
            
            {attachment && <div className="attachment-name">已选择：{attachment.name}</div>}
            <textarea maxLength={10000} onKeyDown={handleComposerKeyDown} placeholder={targets.length ? '输入要发送到微信的消息（Enter发送，Shift+Enter换行）' : '用户先向机器人发消息后，才可以主动发送'} value={draft} onChange={event => setDraft(event.target.value)} disabled={!targets.length} />
            <div className="composer-footer"><small>{draft.length}/10000</small><label className="attachment-button" title="添加图片或文件" aria-label="添加图片或文件"><span className="attachment-button-icon" aria-hidden="true">📎</span><span className="attachment-button-text">添加文件</span><input className="attachment-input" type="file" accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.zip" onChange={event => setAttachment(event.target.files?.[0] ?? null)} disabled={!targets.length} /></label><button disabled={sending || status?.wechat.state !== 'connected' || !selectedIsSendable || (!draft.trim() && !attachment)}>{sending ? '发送中…' : '发送消息'}</button></div>
          </form>
        </div>
      </div>
    </section>

    <section id="schedules" className="card"><div className="section-heading"><div><p className="eyebrow"></p><h2>定时消息</h2><p className="current-time">当前时间：{currentTime.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}</p></div><span className="count">{schedules.length} 个任务</span></div>
      <form className="schedule-form" onSubmit={submit}><p className="schedule-help">选择重复方式即可，系统会自动设置执行时间。一次性任务请直接选择具体日期和时间。</p><input required placeholder="任务名称" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /><select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value as Form['kind'] })}><option value="cron">重复执行</option><option value="once">仅执行一次</option></select>{form.kind === 'cron' ? <><select value={repeatPreset} onChange={e => setRepeatPreset(e.target.value)}><option value="every5">每 5 分钟</option><option value="every15">每 15 分钟</option><option value="hourly">每小时整点</option><option value="daily">每天 09:00</option><option value="weekly">每周一 09:00</option><option value="monthly">每月 1 日 09:00</option><option value="custom">自定义周期</option></select>{repeatPreset === "custom" && <div className="custom-cron"><input required placeholder="例如：30 8 * * 1-5" value={form.spec} onChange={e => setForm({ ...form, spec: e.target.value })} /><details><summary>如何填写自定义周期？</summary><p>格式依次是：分钟、小时、日期、月份、星期。示例：<code>30 8 * * 1-5</code> 表示工作日 08:30；<code>0 18 * * *</code> 表示每天 18:00；<code>*/10 * * * *</code> 表示每 10 分钟。</p></details></div>} </> : <input required type="datetime-local" value={form.run_at} onChange={e => setForm({ ...form, run_at: e.target.value })} />}<div className="target-field"><input required placeholder="选择或搜索微信用户" value={form.target_user_id} onFocus={() => setUserPickerOpen(true)} onChange={e => { setForm({ ...form, target_user_id: e.target.value }); setUserPickerOpen(true); }} /><span className="picker-arrow">⌄</span>{userPickerOpen && <div className="user-picker-options">{userDirectory.filter(user => `${userNotes[user.user_id] ?? ""} ${user.user_id}`.toLowerCase().includes(form.target_user_id.toLowerCase())).map(user => <button type="button" key={user.user_id} onMouseDown={event => event.preventDefault()} onClick={() => { setForm({ ...form, target_user_id: user.user_id }); setUserPickerOpen(false); }}><strong>{userNotes[user.user_id] || "未设置备注"}</strong><small>{user.user_id}</small></button>)}{!userDirectory.length && <p>暂无用户</p>}</div>}</div><textarea required placeholder="要发送的消息" value={form.message} onChange={e => setForm({ ...form, message: e.target.value })} /><div><button disabled={busy}>{editing ? '保存修改' : '创建任务'}</button>{editing && <button type="button" className="secondary" onClick={() => { setEditing(null); setForm(initialForm); }}>取消</button>}</div></form>
      <div className="table-wrap"><table><thead><tr><th>名称</th><th>计划</th><th>目标</th><th>状态</th><th>操作</th></tr></thead><tbody>{schedules.map(schedule => <tr key={schedule.id}><td><strong>{schedule.name}</strong><small>{schedule.message}</small>{expandedHistory === schedule.id && <div className="history">{(history[schedule.id] ?? []).map(item => <small key={item.id}>{formatRunAt(item.scheduled_for)} · {item.status}{item.error ? ` · ${item.error}` : ''}</small>)}</div>}</td><td>{schedule.kind === 'cron' ? schedule.spec : formatRunAt(schedule.run_at)}</td><td>{schedule.target_user_id}</td><td><button className={`toggle ${schedule.enabled ? 'on' : ''}`} onClick={() => void toggle(schedule)}>{schedule.enabled ? '启用' : '停用'}</button></td><td className="actions"><button className="secondary" onClick={() => void showHistory(schedule.id)}>记录</button><button className="secondary" onClick={() => { setEditing(schedule.id); setForm({ name: schedule.name, kind: schedule.kind, spec: schedule.spec ?? '', run_at: schedule.run_at ? new Date(schedule.run_at).toISOString().slice(0, 16) : '', timezone: schedule.timezone, target_user_id: schedule.target_user_id, message: schedule.message }); }}>编辑</button><button className="danger" onClick={() => void remove(schedule.id)}>删除</button></td></tr>)}</tbody></table>{!schedules.length && <p className="empty">还没有定时任务。</p>}</div>
    </section>
    <section className="card tool-guide"><h2>微信工具命令</h2><p className="muted">在微信中直接发送命令即可使用工具；发送 <code>/help</code> 查看帮助，发送 <code>/tools</code> 查看全部工具。</p><div className="tool-command-list"><code>/codex 任务内容</code><code>/codex（进入持续会话）</code><code>/new</code><code>/sessions</code><code>/session 2</code><code>/exit</code><code>/translate 内容</code><code>/summary 内容</code><code>/abstract 内容</code><code>/meeting 内容</code><code>/skill 多步处理要求</code><code>/todo 添加任务</code><code>/todo list</code><code>/remind 20分钟后提醒我开会</code><code>/repeat 每天9点发送天气</code><code>/mail 收件人 | 主题 | 正文</code><code>/mail confirm</code><code>/ocr（附图片）</code><code>发送文件并附加 /convert json</code><code>/create-tool 用自然语言描述工具</code><code>/run-tool confirm 工具ID 输入内容</code><code>/control 命令ID</code></div></section>
    <section id="ai-settings" className="card ai-settings"><h2>模型设置</h2><p className="muted">当前提供方：<strong>{status?.ai?.provider ?? "加载中"}</strong> · 当前模型：<strong>{status?.ai?.model ?? "加载中"}</strong> · SMTP：<strong>{status?.smtp?.configured ? "已配置" : "未配置"}</strong></p><p className="muted">保存后立即生效；密钥和密码留空表示保留原值，并且不会回显。</p>
      <form className="provider-form" onSubmit={saveProviderSettings}><label>模型提供方<select name="AI_PROVIDER"><option value="codex">Codex</option><option value="openai">大模型 API</option></select></label><label>API 模型<input name="OPENAI_MODEL" placeholder="gpt-4o-mini" /></label><label>API 地址<input name="OPENAI_BASE_URL" placeholder="https://api.openai.com/v1" /></label><label>API 协议<select name="OPENAI_API_MODE"><option value="responses">Responses API</option><option value="chat_completions">Chat Completions 兼容接口</option></select></label><label>API Key<input name="OPENAI_API_KEY" type="password" placeholder="留空则不修改" autoComplete="off" /></label><label>Codex 模型（可选）<input name="CODEX_MODEL" placeholder="留空使用 Codex 默认模型" /></label><label>SMTP 主机<input name="SMTP_HOST" placeholder="smtp.example.com" /></label><label>SMTP 端口<input name="SMTP_PORT" type="number" defaultValue="587" /></label><label>SMTP 用户<input name="SMTP_USER" /></label><label>SMTP 密码<input name="SMTP_PASSWORD" type="password" placeholder="留空则不修改" autoComplete="new-password" /></label><label>发件人<input name="SMTP_FROM" /></label><label className="checkbox-label"><input name="SMTP_SSL" type="checkbox" value="true" /> 使用 SMTP SSL</label><button disabled={providerSaving}>{providerSaving ? "保存中" : "保存并生效"}</button>{providerNotice && <p className="muted">{providerNotice}</p>}</form>
    </section>
    <section className="card generated-tools"><h2>已生成工具</h2><p className="muted">仅显示当前微信用户生成的工具。自动工具只处理文本，审核启用后才能运行。</p>{generatedTools.length ? generatedTools.map(tool => <article className="generated-tool" key={tool.id}><div><strong>#{tool.id}</strong><p>{tool.description}</p><small>{tool.enabled ? "已启用" : "待审核"}</small></div><pre>{tool.definition}</pre><div className="actions"><button className="secondary" disabled={!selectedUser} onClick={async () => { if (!selectedUser) return; await api.updateGeneratedTool(tool.id, selectedUser, !Boolean(tool.enabled)); await refreshGeneratedTools(); }}>{tool.enabled ? "停用工具" : "审核并启用"}</button>{Boolean(tool.enabled) && <button onClick={() => openTool(`generated:${tool.id}`)}>运行工具</button>}</div></article>) : <p className="empty">暂无生成工具</p>}</section>
    <div id="tools"><div className="tool-tabs">{['全部', '常用', 'AI', '效率'].map(tab => <button type="button" className={toolTab === tab ? 'active' : ''} key={tab} onClick={() => setToolTab(tab)}>{tab}{tab === '常用' ? ` ${favoriteTools.length}` : ''}</button>)}</div><div className="grid">
      {filteredTools.map(([id, item]) => <div className="card tool-card" key={id}><button type="button" className="tool-open" onClick={() => openTool(id)}><h2>{item.label}</h2><p className="muted">{item.description}</p><span>查看详情与测试 →</span></button><button type="button" className="favorite" aria-pressed={favoriteTools.includes(id)} onClick={() => toggleFavorite(id)}>{favoriteTools.includes(id) ? '★ 取消常用' : '☆ 设为常用'}</button></div>)}
      {!filteredTools.length && <div className="card tools-empty"><p>常用工具为空。</p><span>切换到“全部”，可将任意工具设为常用。</span></div>}
    </div></div>
    {selectedTool && <div className="tool-overlay" onClick={() => setSelectedTool(null)}><aside className="tool-drawer" role="dialog" aria-modal="true" aria-label={selectedToolDefinition?.label ?? '工具详情'} onClick={event => event.stopPropagation()}><button type="button" className="drawer-close" aria-label="关闭" onClick={() => setSelectedTool(null)}>×</button><h2>{selectedGeneratedTool ? `工具 #${selectedGeneratedTool.id}` : selectedToolDefinition?.label}</h2><p className="tool-usage">{selectedGeneratedTool ? selectedGeneratedTool.description : selectedToolDefinition?.usage}</p>
      {selectedToolDefinition?.features?.length && <div className="tool-details"><h3>能力说明</h3><ul>{selectedToolDefinition.features.map(feature => <li key={feature}>{feature}</li>)}</ul>{selectedToolDefinition.commands?.length && <><h3>微信用法</h3><div className="tool-command-list">{selectedToolDefinition.commands.map(command => <code key={command}>{command}</code>)}</div></>}</div>}
      {(selectedTool in aiTools || selectedTool === 'ocr' || selectedTool === 'createTool') && selectedTool !== 'Codex' && <p className="tool-context">当前模型：<strong>{status?.ai?.provider === 'codex' ? 'Codex' : '大模型 API'}</strong></p>}
      {['todo', 'remind', 'repeat', 'createTool'].includes(selectedTool) && <p className="tool-context">当前微信用户：<strong>{selectedUser || '未选择'}</strong></p>}
      {selectedTool === 'control' ? <select value={toolInput} onChange={event => setToolInput(event.target.value)}><option value="">请选择命令</option>{commands.map(command => <option key={command.id} value={command.id}>{command.label}</option>)}</select> : selectedTool === 'screenshot' ? <><label className="screen-picker">截图范围<select value={screenIndex === null ? 'all' : String(screenIndex)} onChange={event => setScreenIndex(event.target.value === 'all' ? null : Number(event.target.value))}><option value="all">全部屏幕</option><option value="0">屏幕 0</option><option value="1">屏幕 1</option><option value="2">屏幕 2</option><option value="3">屏幕 3</option></select></label><p className="muted">屏幕序号从 0 开始；选择不存在的屏幕会返回提示。</p></> : selectedTool === 'ocr' || selectedTool === 'convert' ? <><input type="file" accept={selectedTool === 'ocr' ? 'image/*' : '.json,.csv,.txt,.md,.png,.jpg,.jpeg,.webp'} onChange={event => setToolFile(event.target.files?.[0] ?? null)} />{selectedTool === 'convert' && <select value={convertTarget} onChange={event => setConvertTarget(event.target.value)}><option value="pdf">PDF</option><option value="json">JSON</option><option value="csv">CSV</option><option value="md">Markdown</option><option value="txt">纯文本</option><option value="html">HTML</option></select>}</> : <textarea placeholder={selectedTool === 'Codex' ? '输入测试任务' : selectedGeneratedTool ? '输入工具要处理的内容' : allTools[selectedTool]?.placeholder} value={toolInput} onChange={event => setToolInput(event.target.value)} />}
      {selectedTool === 'mail' && <label className="drawer-file">附件（可选）<input type="file" onChange={event => setToolFile(event.target.files?.[0] ?? null)} /></label>}
      <button disabled={toolBusy || !toolInputReady} onClick={() => void testTool()}>{toolBusy ? <><span className="spinner" aria-hidden="true" />执行中</> : selectedTool === 'mail' ? '确认并发送' : selectedTool === 'convert' ? '转换并下载' : '执行'}</button>{toolOutput && (selectedTool === 'screenshot' && toolOutput.startsWith('data:image/') ? <img className="desktop-screenshot" src={toolOutput} alt="当前桌面截图" /> : <pre className="output">{toolOutput}</pre>)}
      {selectedTool === 'todo' && <div className="todo-list">{todos.map(item => <div key={item.id}><span className={item.done ? 'done' : ''}>#{item.id} {item.text}</span>{!item.done && <button className="secondary" onClick={async () => { if (!selectedUser) return; await api.completeTodo(item.id, selectedUser); await refreshTodos(); }}>完成</button>}</div>)}{!todos.length && <p className="empty">暂无待办</p>}</div>}
    </aside></div>}
  </div></div></main>;
}
export default App;
