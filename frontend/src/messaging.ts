export function chunkText(text: string, maxChars = 1800): string[] {
  if (maxChars < 1) throw new Error('maxChars must be positive');
  let remaining = text.trim();
  const chunks: string[] = [];
  while (remaining.length > maxChars) {
    const boundary = Math.max(remaining.lastIndexOf('\n', maxChars), remaining.lastIndexOf(' ', maxChars));
    const cut = boundary < Math.floor(maxChars / 2) ? maxChars : boundary;
    const part = remaining.slice(0, cut).trim();
    if (part) chunks.push(part);
    remaining = remaining.slice(cut).trim();
  }
  if (remaining) chunks.push(remaining);
  return chunks;
}
