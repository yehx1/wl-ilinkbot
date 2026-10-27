# iLinkBot Codex execution policy

Messages received in Codex mode are real user requests. Use Codex's native tools when an action is requested; do not merely print a command for the user to run.

## Completion standard

- Before performing a side-effecting action, identify an observable postcondition that proves the user's requested outcome.
- A tool call marked `completed`, an empty stderr stream, or a process exit code of zero proves only that the tool process finished. None of these alone proves that the requested outcome occurred.
- Preserve and inspect command/API return values that can signal failure. Do not pipe meaningful Boolean results or status information to `Out-Null` unless an independent verification replaces them.
- After the action, make a separate read-only observation of the real target state. Base the final answer on that observation, not on the attempted command.
- If verification fails, try a reasonable safe alternative when one is available, then verify again. If the postcondition still cannot be established, report the action as failed or partially completed and include the observed state.
- Never state “已完成”, “已发送”, “已切换”, “已最大化”, or equivalent success language unless the requested postcondition was observed.

## Windows desktop actions

- Operate on the actual visible top-level window, not merely the first process with a matching name.
- For a request to bring a window forward and maximize it, verify all of the following after the attempt:
  - the target window handle is still valid and visible;
  - it is not minimized;
  - it is maximized (`IsZoomed` or `WINDOWPLACEMENT.showCmd == 3`);
  - `GetForegroundWindow()` equals the target window handle.
- `SetForegroundWindow`, `BringWindowToTop`, `AppActivate`, and `ShowWindowAsync` may be refused or may only queue work. Check their results, allow asynchronous state changes to settle, and verify the final state.
- Windows may legitimately deny foreground activation. In that case, report the verified partial result instead of claiming success.

## User-facing result

Keep the final response concise and state only what was verified. When an action fails, distinguish the command/process result from the actual application state.

When the user asks for a file, image, or other artifact to be sent through WeChat, include its exact local path in the final response. Relative paths are resolved from the Codex workdir; absolute paths may refer to any local location. The host application converts referenced local files into WeChat attachments; a Markdown link by itself is not proof that the file was delivered. Do not claim that an attachment was sent unless the host application reports that delivery succeeded.
