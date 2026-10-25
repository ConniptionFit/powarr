import Modal from "./Modal";
import type { ImportPreview } from "../lib/api";

function Change({ c }: { c: { path: string; old: string | null; new: string | null } }) {
  return (
    <li className="text-xs font-mono break-all">
      <span className="text-slate-400">{c.path}</span>{" "}
      <span className="text-red-300/80 line-through">{c.old ?? "unset"}</span>{" "}
      <span className="text-slate-500">→</span>{" "}
      <span className="text-green-300">{c.new ?? "removed"}</span>
    </li>
  );
}

// Shows what an imported settings file would change before anything is written.
export default function ImportPreviewModal({ preview, busy, onApply, onClose }: {
  preview: ImportPreview; busy: boolean; onApply: () => void; onClose: () => void;
}) {
  const total = preview.app_settings.length + preview.integrations.length;
  return (
    <Modal onClose={onClose} label="Review settings import" className="w-full max-w-2xl max-h-[85vh] flex flex-col">
      <div className="p-5 border-b border-purple-900/30">
        <h2 className="text-white font-semibold">Review settings import</h2>
        <p className="text-slate-400 text-xs mt-1">
          {total === 0
            ? "This file matches your current settings. Nothing would change."
            : `${total} item${total === 1 ? "" : "s"} would change`}
          {preview.settings_unchanged > 0 && `, ${preview.settings_unchanged} setting${preview.settings_unchanged === 1 ? "" : "s"} already identical`}.
          Credentials are never changed.
        </p>
      </div>
      <div className="overflow-y-auto p-5 space-y-4 flex-1">
        {preview.app_settings.map(s => (
          <section key={s.key}>
            <h3 className="text-sm text-white font-medium">
              {s.key}{" "}
              <span className={`text-[10px] uppercase tracking-wide ml-1 ${s.status === "new" ? "text-green-400" : "text-amber-400"}`}>
                {s.status}
              </span>
            </h3>
            {s.changes.length > 0 && <ul className="mt-1 space-y-0.5">{s.changes.map(c => <Change key={c.path} c={c} />)}</ul>}
          </section>
        ))}
        {preview.integrations.map(i => (
          <section key={`int-${i.name}`}>
            <h3 className="text-sm text-white font-medium capitalize">
              {i.name} integration{" "}
              <span className={`text-[10px] uppercase tracking-wide ml-1 ${i.status === "new" ? "text-green-400" : "text-amber-400"}`}>
                {i.status}
              </span>
            </h3>
            {i.changes.length > 0 && <ul className="mt-1 space-y-0.5">{i.changes.map(c => <Change key={c.path} c={c} />)}</ul>}
          </section>
        ))}
      </div>
      <div className="p-4 border-t border-purple-900/30 flex justify-end gap-2">
        <button onClick={onClose} className="px-3 py-1.5 rounded-lg text-slate-300 hover:text-white text-sm">Cancel</button>
        <button onClick={onApply} disabled={busy || total === 0}
          className="px-3 py-1.5 rounded-lg bg-brand text-white text-sm disabled:opacity-40">
          {busy ? "Importing…" : "Apply import"}
        </button>
      </div>
    </Modal>
  );
}
