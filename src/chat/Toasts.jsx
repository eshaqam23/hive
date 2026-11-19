// Toasts for the outcome of an operator action. A failed POST always lands here with the server's
// own error text, so no button fails silently. Errors stay until dismissed; notes fade after 5 s.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { CloseIcon } from "../shell/icons.jsx";

let nextId = 0;

export function useToasts() {
  const [toasts, setToasts] = useState([]);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (toast) => {
      nextId += 1;
      const id = nextId;
      setToasts((list) => [...list, { id, tone: "note", ...toast }].slice(-4));
      if (toast.tone !== "error") timers.current.set(id, window.setTimeout(() => dismiss(id), 5000));
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const all = timers.current;
    return () => all.forEach((t) => window.clearTimeout(t));
  }, []);

  return { toasts, push, dismiss };
}

export function ToastRegion({ toasts, onDismiss }) {
  return (
    <div aria-label="Notifications" className="pointer-events-none fixed bottom-6 right-6 z-40 flex w-[min(420px,calc(100vw-48px))] flex-col gap-2" role="region">
      {toasts.map((t) => (
        <div
          className={`pointer-events-auto flex animate-fade items-start gap-3 rounded-xl border bg-card px-4 py-3 shadow-float ${
            t.tone === "error" ? "border-danger/60" : "border-line-strong"
          }`}
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
        >
          <span aria-hidden="true" className={`mt-1.5 size-2 flex-none rounded-full ${t.tone === "error" ? "bg-danger" : "bg-live"}`} />
          <div className="min-w-0 flex-1">
            {t.title && <p className="text-[14px] font-semibold">{t.title}</p>}
            <p className="break-words text-[14px] leading-snug text-muted">{t.text}</p>
          </div>
          <button aria-label="Dismiss" className="ctl size-7 hover:bg-hover" onClick={() => onDismiss(t.id)} type="button">
            <CloseIcon size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
