// Reading standard input the same way in the three entry points that need it, so none of them
// carries its own copy. `process.stdin` rather than `Bun.stdin`: the tooling typecheck project has
// no Bun globals (see `tsconfig.scripts.json`), and the Node stream is typed and behaves the same.
export type ByteStream = AsyncIterable<Uint8Array>;

/** Every byte of `stream`, decoded as UTF-8. */
export async function readAllText(stream: ByteStream): Promise<string> {
  const decoder = new TextDecoder();
  let text = "";
  for await (const chunk of stream) text += decoder.decode(chunk, { stream: true });
  return text + decoder.decode();
}

/** Calls `handle` once per newline-terminated line, and once for a trailing partial line. */
export async function forEachLine(
  stream: ByteStream,
  handle: (line: string) => Promise<void>,
): Promise<void> {
  const decoder = new TextDecoder();
  let buffered = "";
  for await (const chunk of stream) {
    buffered += decoder.decode(chunk, { stream: true });
    const lines = buffered.split("\n");
    buffered = lines.pop() ?? "";
    for (const line of lines) {
      if (line.trim().length === 0) continue;
      await handle(line);
    }
  }
  const rest = buffered + decoder.decode();
  if (rest.trim().length > 0) await handle(rest);
}
