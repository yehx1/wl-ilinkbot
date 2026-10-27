# iLinkBot frontend

React + Vite 本地控制台。开发时单独启动：

```powershell
cd frontend
npm install
npm run dev
```

Vite 运行在 `http://127.0.0.1:5173`，`/api` 自动代理到 Python 后端 `http://127.0.0.1:3210`。

生产构建：

```powershell
npm run build
```

后端和前端按两个进程运行；不要把 Vite 或 API 绑定到公网地址。控制台只显示后端允许的命令 ID、二维码和脱敏状态，不显示凭证或微信上下文令牌。
