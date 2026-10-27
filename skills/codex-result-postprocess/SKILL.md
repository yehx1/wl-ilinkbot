---
name: codex-result-postprocess
description: Process Codex results before returning them to WeChat, including removing CLI transcript noise and sending referenced local files as attachments.
---

# Codex result post-processing

Apply this skill after every Codex execution and before the result is sent to WeChat.

## Text results

- Return the final user-facing answer only.
- Remove CLI diagnostics and transcript sections such as version banners, workdir/model/provider metadata, warnings, `exec` blocks, `tokens used`, and duplicated final answers.
- Preserve useful explanations, status messages, and error details.
- Do not expose local configuration paths, session identifiers, or internal tool logs unless the user explicitly asks for diagnostics.

## Files and images

- Inspect the final text for local paths to generated artifacts.
- Supported attachments include images, PDF, text, CSV, JSON, DOCX, XLSX, and ZIP files.
- Send an existing local file as a WeChat attachment only when it is explicitly referenced by the Codex result or clearly created for the user's request.
- The host integration recognizes bare local paths and Markdown links to local paths, then calls the project's WeChat file-send interface. Relative paths use the Codex workdir; explicitly referenced absolute paths may be anywhere on the local machine. Include the exact path in the final result; a Markdown link alone is not evidence that delivery occurred.
- Reject missing files and files larger than 20 MB.
- After sending an attachment, keep the text response short and state that the file was sent.
- Never send credentials, environment files, databases, or arbitrary files merely because a path appears in diagnostic output.

## Desktop requests

For screenshot or application-control requests, prefer the controlled desktop operations exposed by the application. If Codex creates a screenshot file itself, apply the file rules above so the artifact reaches WeChat.
