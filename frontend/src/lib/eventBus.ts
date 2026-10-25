// One shared EventSource for /imports/events (the app-wide event bus). Pages and
// TaskContext subscribe here instead of each opening their own connection; the
// stream opens with the first subscriber and closes after the last one leaves.
type Handler = (data: any | null, raw: MessageEvent) => void;

const handlers = new Set<Handler>();
let source: EventSource | null = null;

export function subscribeEvents(handler: Handler): () => void {
  handlers.add(handler);
  if (!source) {
    source = new EventSource("/api/v1/imports/events");
    source.onmessage = raw => {
      let data: any | null = null;
      try { data = JSON.parse(raw.data); } catch { /* keepalive: data stays null */ }
      // Copy so a handler that unsubscribes mid-dispatch cannot skip a sibling.
      [...handlers].forEach(h => h(data, raw));
    };
  }
  return () => {
    handlers.delete(handler);
    if (handlers.size === 0 && source) { source.close(); source = null; }
  };
}
