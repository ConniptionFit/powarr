import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Save, Trash2, X } from "lucide-react";
import { mediaApi, fmtBytes, type MediaRule, type RuleCondition } from "../../lib/api";
import { useToast } from "../../context/ToastContext";
import { SkeletonTable } from "../../components/Skeleton";

const OP_LABEL: Record<string, string> = {
  gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "is", ne: "is not", contains: "contains",
};
const MEDIA_TYPES = ["movie", "show", "episode", "artist", "album", "track"];

const TEMPLATES: MediaRule[] = [
  { name: "Unwatched for 18 months, over 10 GB", match: "all", conditions: [
    { field: "days_unwatched", op: "gt", value: 548 }, { field: "size_gb", op: "gt", value: 10 }] },
  { name: "Never watched, in library over a year", match: "all", conditions: [
    { field: "watch_count", op: "eq", value: 0 }, { field: "days_in_library", op: "gt", value: 365 }] },
  { name: "Old, big movies", match: "all", conditions: [
    { field: "media_type", op: "eq", value: "movie" }, { field: "year", op: "lt", value: 2005 },
    { field: "size_gb", op: "gt", value: 20 }] },
];

type Rule = MediaRule & { id?: string };
const blankCondition: RuleCondition = { field: "size_gb", op: "gt", value: 10 };

export default function Rules() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: catalog, isLoading } = useQuery({ queryKey: ["media-rules"], queryFn: mediaApi.rules });
  const [rules, setRules] = useState<Rule[]>([]);
  const [sel, setSel] = useState(0);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (catalog && !dirty) setRules(catalog.rules);
  }, [catalog, dirty]);

  const rule = rules[sel] as Rule | undefined;
  const update = (patch: Partial<Rule>) => {
    setRules(rs => rs.map((r, i) => (i === sel ? { ...r, ...patch } : r)));
    setDirty(true);
  };
  const add = (r: Rule) => {
    setRules(rs => [...rs, r]);
    setSel(rules.length);
    setDirty(true);
  };

  // Preview the selected rule as it is being edited, debounced so typing a
  // number doesn't fire a request per keystroke.
  const previewKey = JSON.stringify(rule ? { m: rule.match, c: rule.conditions, n: rule.name || "x" } : null);
  const [debounced, setDebounced] = useState(previewKey);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(previewKey), 350);
    return () => clearTimeout(t);
  }, [previewKey]);
  const preview = useQuery({
    queryKey: ["media-rule-preview", debounced],
    queryFn: () => {
      const d = JSON.parse(debounced);
      return mediaApi.previewRule({ name: d.n, match: d.m, conditions: d.c });
    },
    enabled: !!rule,
    retry: false,
  });

  const save = useMutation({
    mutationFn: () => mediaApi.saveRules(rules),
    onSuccess: res => {
      setRules(res.rules);
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["media-rules"] });
      toast.success("Rules saved");
    },
    onError: (e: Error) => toast.error(`Couldn't save: ${e.message}`),
  });

  const fields = catalog?.fields ?? {};
  const fieldKeys = useMemo(() => Object.keys(fields), [fields]);

  if (isLoading) return <div className="p-4 sm:p-8"><SkeletonTable rows={4} /></div>;

  const setCond = (i: number, patch: Partial<RuleCondition>) => {
    if (!rule) return;
    update({ conditions: rule.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
  };
  const changeField = (i: number, field: string) => {
    const f = fields[field];
    const value = f.kind === "num" ? 0 : f.kind === "bool" ? true : "";
    setCond(i, { field, op: f.ops[0], value });
  };

  return (
    <div className="p-4 sm:p-8">
      <p className="text-slate-400 text-sm mb-5 max-w-2xl">
        Saved filters over your library, for example “unwatched 18 months and over 10 GB”. Rules only
        <span className="text-white"> show what matches</span>. They never delete, protect or re-score anything.
      </p>

      <div className="grid grid-cols-1 lg:grid-cols-[16rem_1fr] gap-5">
        <aside className="space-y-2">
          {rules.map((r, i) => (
            <button key={r.id ?? `new-${i}`} onClick={() => setSel(i)}
              className={`w-full text-left px-3 py-2 rounded-lg border text-sm truncate transition-colors ${
                i === sel ? "border-brand-light bg-brand/20 text-white"
                  : "border-purple-900/30 bg-surface-raised text-slate-300 hover:text-white"}`}>
              {r.name || "Untitled rule"}
            </button>
          ))}
          <button onClick={() => add({ name: "New rule", match: "all", conditions: [{ ...blankCondition }] })}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border border-dashed border-purple-900/50 text-slate-400 hover:text-white text-sm">
            <Plus size={14} /> New rule
          </button>
          <div className="pt-3">
            <p className="text-slate-500 text-[11px] uppercase tracking-wider mb-1.5">Start from a template</p>
            {TEMPLATES.map(t => (
              <button key={t.name} onClick={() => add(structuredClone(t))}
                className="w-full text-left text-xs text-slate-400 hover:text-white py-1">
                {t.name}
              </button>
            ))}
          </div>
        </aside>

        {rule ? (
          <section className="bg-surface-raised rounded-xl border border-purple-900/30 p-5 space-y-4 min-w-0">
            <div className="flex gap-3 items-end flex-wrap">
              <label className="flex-1 min-w-48 text-xs text-slate-400">
                Name
                <input value={rule.name} onChange={e => update({ name: e.target.value })} maxLength={80}
                  className="mt-1 w-full bg-surface border border-purple-900/40 rounded-md px-2.5 py-1.5 text-white text-sm focus:outline-none focus:border-brand-light" />
              </label>
              <label className="text-xs text-slate-400">
                Match
                <select value={rule.match} onChange={e => update({ match: e.target.value as "all" | "any" })}
                  className="mt-1 block bg-surface border border-purple-900/40 rounded-md px-2.5 py-1.5 text-white text-sm">
                  <option value="all">all conditions</option>
                  <option value="any">any condition</option>
                </select>
              </label>
            </div>

            <div className="space-y-2">
              {rule.conditions.map((c, i) => {
                const f = fields[c.field];
                if (!f) return null;
                return (
                  <div key={i} className="flex gap-2 items-center flex-wrap">
                    <select aria-label="Field" value={c.field} onChange={e => changeField(i, e.target.value)}
                      className="bg-surface border border-purple-900/40 rounded-md px-2 py-1.5 text-white text-sm">
                      {fieldKeys.map(k => <option key={k} value={k}>{fields[k].label}</option>)}
                    </select>
                    <select aria-label="Operator" value={c.op} onChange={e => setCond(i, { op: e.target.value })}
                      className="bg-surface border border-purple-900/40 rounded-md px-2 py-1.5 text-white text-sm">
                      {f.ops.map(o => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}
                    </select>
                    {f.kind === "num" && (
                      <input aria-label="Value" type="number" value={String(c.value)}
                        onChange={e => setCond(i, { value: e.target.value === "" ? 0 : Number(e.target.value) })}
                        className="w-28 bg-surface border border-purple-900/40 rounded-md px-2 py-1.5 text-white text-sm" />
                    )}
                    {f.kind === "bool" && (
                      <select aria-label="Value" value={String(c.value)} onChange={e => setCond(i, { value: e.target.value === "true" })}
                        className="bg-surface border border-purple-900/40 rounded-md px-2 py-1.5 text-white text-sm">
                        <option value="true">yes</option><option value="false">no</option>
                      </select>
                    )}
                    {f.kind === "str" && (
                      <>
                        <input aria-label="Value" value={String(c.value)} list={c.field === "media_type" ? "rule-media-types" : undefined}
                          onChange={e => setCond(i, { value: e.target.value })}
                          className="w-44 bg-surface border border-purple-900/40 rounded-md px-2 py-1.5 text-white text-sm" />
                      </>
                    )}
                    <button aria-label="Remove condition" disabled={rule.conditions.length === 1}
                      onClick={() => update({ conditions: rule.conditions.filter((_, j) => j !== i) })}
                      className="text-slate-500 hover:text-red-400 disabled:opacity-30 p-1">
                      <X size={14} />
                    </button>
                  </div>
                );
              })}
              <datalist id="rule-media-types">{MEDIA_TYPES.map(t => <option key={t} value={t} />)}</datalist>
              <button onClick={() => update({ conditions: [...rule.conditions, { ...blankCondition }] })}
                className="text-xs text-brand-light hover:text-white flex items-center gap-1">
                <Plus size={12} /> Add condition
              </button>
            </div>

            <div className="rounded-lg bg-surface border border-purple-900/30 p-3">
              {preview.isError ? (
                <p className="text-amber-400 text-sm">{(preview.error as Error).message}</p>
              ) : preview.data ? (
                <>
                  <p className="text-white text-sm font-medium">
                    {preview.data.count.toLocaleString()} item{preview.data.count === 1 ? "" : "s"} match
                    <span className="text-slate-400 font-normal"> · {fmtBytes(preview.data.total_bytes)}</span>
                  </p>
                  {preview.data.sample.length > 0 && (
                    <ul className="mt-2 divide-y divide-purple-900/20 text-xs">
                      {preview.data.sample.map(s => (
                        <li key={s.id} className="py-1.5 flex justify-between gap-3">
                          <span className="truncate text-slate-300">
                            {s.parent_title ? `${s.parent_title} · ` : ""}{s.title}
                          </span>
                          <span className="text-slate-500 shrink-0">{fmtBytes(s.file_size)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {preview.data.count > preview.data.sample.length && (
                    <p className="text-slate-600 text-[11px] mt-1">Showing the largest {preview.data.sample.length}.</p>
                  )}
                </>
              ) : (
                <p className="text-slate-500 text-sm">Checking…</p>
              )}
            </div>

            <div className="flex gap-2 flex-wrap">
              <button onClick={() => save.mutate()} disabled={!dirty || save.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand text-white text-sm disabled:opacity-40">
                <Save size={14} /> {save.isPending ? "Saving…" : dirty ? "Save rules" : "Saved"}
              </button>
              {rule.id && !dirty && (
                <button onClick={() => mediaApi.exportRuleCsv(rule.id!)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-purple-900/40 text-slate-300 hover:text-white text-sm">
                  <Download size={14} /> Export matches (CSV)
                </button>
              )}
              <button
                onClick={() => {
                  setRules(rs => rs.filter((_, i) => i !== sel));
                  setSel(0);
                  setDirty(true);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-slate-500 hover:text-red-400 text-sm ml-auto">
                <Trash2 size={14} /> Remove
              </button>
            </div>
          </section>
        ) : (
          <section className="bg-surface-raised rounded-xl border border-purple-900/30 p-8 text-center text-slate-400 text-sm">
            No rules yet. Add one, or start from a template on the left.
          </section>
        )}
      </div>
    </div>
  );
}
