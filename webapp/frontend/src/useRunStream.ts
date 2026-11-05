import { useEffect, useState } from "react";

import { streamUrl } from "./api";
import { applyEvents, initialRun, type RunState } from "./runState";
import type { RunEvent, RunSummary } from "./types";

export type Connection = "connecting" | "live" | "reconnecting" | "closed" | "error";

// Tokens arrive by the hundred a second; they are applied in batches, on a
// timer rather than animation frames so a page in a background tab keeps up.
const FLUSH_MS = 60;

/** A run's state, kept up to date from its event stream; ``onEnd`` once it is over. */
export function useRunStream(runId: string, onEnd?: (summary: RunSummary) => void) {
  const [run, setRun] = useState<RunState>(() => initialRun(runId));
  const [connection, setConnection] = useState<Connection>("connecting");

  useEffect(() => {
    setRun(initialRun(runId));
    setConnection("connecting");
    const source = new EventSource(streamUrl(runId));
    let buffer: RunEvent[] = [];
    let timer: number | undefined;

    const flush = () => {
      timer = undefined;
      if (!buffer.length) return;
      const batch = buffer;
      buffer = [];
      setRun((state) => applyEvents(state, batch));
    };

    source.onopen = () => setConnection("live");
    source.onmessage = (message) => {
      buffer.push(JSON.parse(message.data) as RunEvent);
      if (timer === undefined) timer = window.setTimeout(flush, FLUSH_MS);
    };
    source.addEventListener("end", (message) => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
      flush();
      const summary = JSON.parse((message as MessageEvent).data) as RunSummary;
      setRun((state) => ({ ...state, ended: true, summary, status: summary.status }));
      setConnection("closed");
      onEnd?.(summary);
    });
    // EventSource reconnects by itself (sending the last id it saw); it gives
    // up only when the server refuses, e.g. a run that does not exist.
    source.onerror = () => setConnection(source.readyState === EventSource.CLOSED ? "error" : "reconnecting");

    return () => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // onEnd is read when the stream ends, not a reason to reconnect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { run, connection };
}

/** The current time, ticking while ``active``. */
export function useNow(active: boolean, everyMs = 1000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now() / 1000);
    const id = window.setInterval(() => setNow(Date.now() / 1000), everyMs);
    return () => window.clearInterval(id);
  }, [active, everyMs]);
  return now;
}
