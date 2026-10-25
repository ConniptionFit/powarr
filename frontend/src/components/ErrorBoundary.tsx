import { Component, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { AlertTriangle } from "lucide-react";

interface Props { children: ReactNode; }
interface State { error: Error | null; }

class Boundary extends Component<Props, State> {
  state: State = { error: null };
  static getDerivedStateFromError(error: Error): State { return { error }; }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div role="alert" className="p-4 sm:p-8">
        <div className="max-w-xl rounded-xl border border-red-500/30 bg-red-500/5 p-6">
          <div className="flex items-center gap-2 text-red-300 font-semibold">
            <AlertTriangle size={18} /> This page hit an error
          </div>
          <p className="mt-2 text-sm text-slate-400 break-words">{error.message}</p>
          <div className="mt-4 flex gap-2">
            <button
              onClick={() => this.setState({ error: null })}
              className="px-3 py-2 rounded-lg bg-brand/20 text-brand-light hover:bg-brand/30 text-sm"
            >
              Try again
            </button>
            <button
              onClick={() => window.location.reload()}
              className="px-3 py-2 rounded-lg bg-surface-overlay text-slate-300 hover:bg-white/10 text-sm"
            >
              Reload app
            </button>
          </div>
        </div>
      </div>
    );
  }
}

// Keyed on pathname so navigating away from a crashed page clears the error
// (and a stale lazy-chunk failure after a redeploy recovers on next navigation).
export default function ErrorBoundary({ children }: Props) {
  const { pathname } = useLocation();
  return <Boundary key={pathname}>{children}</Boundary>;
}
