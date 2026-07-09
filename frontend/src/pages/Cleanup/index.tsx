import { useState } from "react";
import { Trash2, DownloadCloud, History } from "lucide-react";
import DeletionSuggestions from "./DeletionSuggestions";
import FailedImports from "./FailedImports";
import DeletionHistory from "./DeletionHistory";

const TABS = [
  { key: "deletion", label: "Deletion Suggestions", icon: Trash2 },
  { key: "imports", label: "Failed Imports", icon: DownloadCloud },
  { key: "history", label: "Deletion History", icon: History },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export default function Cleanup() {
  const [tab, setTab] = useState<TabKey>("imports");

  return (
    <div className="p-4 sm:p-8">
      <h1 className="text-2xl font-bold text-white mb-4">Cleanup</h1>

      <div className="flex items-center gap-1 border-b border-purple-900/40 mb-6">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === key
                ? "border-purple-500 text-brand-light"
                : "border-transparent text-slate-400 hover:text-white"
            }`}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {/* Tabs stay mounted so their state (filters, sort, confirmations) is fully independent and preserved */}
      <div className={tab === "deletion" ? "" : "hidden"}>
        <DeletionSuggestions />
      </div>
      <div className={tab === "imports" ? "" : "hidden"}>
        <FailedImports />
      </div>
      <div className={tab === "history" ? "" : "hidden"}>
        <DeletionHistory />
      </div>
    </div>
  );
}
