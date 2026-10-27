# iLinkBot backend

Python/FastAPI 后端负责微信 iLink 连接、SQLite 持久化、定时消息、Codex CLI 和固定白名单控制。

## Windows 启动

```powershell
cd backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m app
```

默认 API 监听 `http://127.0.0.1:3210`。若 PowerShell 策略阻止脚本，可使用 `backend\.venv\Scripts\python.exe -m app`。首次启动后在前端扫码登录；凭证与数据库在 `backend/data/`，不会提交到仓库。

## 配置

模型配置可以在网页保存并立即生效，也可以编辑 `.env`。`AI_PROVIDER` 可选 `codex` 或 `openai`；API 模式配置 `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL` 和 `OPENAI_API_MODE=responses|chat_completions`。Codex 配置使用 `CODEX_COMMAND`、`CODEX_MODEL`、`CODEX_WORKDIR` 和超时参数。电脑控制只从 `config/control_allowlist.json` 读取固定命令，不能通过微信或 API 提交任意可执行文件、参数或 shell 文本。默认只允许本机监听。

## 测试

```powershell
python -m pytest -q
```

微信指令详见根目录 README，包括翻译、总结、摘要、OCR、格式转换、提醒、待办、重复任务、会议纪要、邮件和自然语言生成工具。主动定时发送要求目标用户此前至少向机器人发送过一条消息，以便获得 iLink 上下文令牌。

同样的能力可从网页“运行工具”调用。文件与图片通过 JSON Base64 传输；通用文件限制为 20 MB，OCR 图片限制为 10 MB。自动生成工具按微信用户隔离，默认禁用，必须审核后才能运行。
