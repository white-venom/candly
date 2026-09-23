import { Component, type ReactNode } from "react";

/** Keeps one broken widget (e.g. the canvas chart choking on bad data) from blanking the page. */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="flex flex-col items-center justify-center gap-2 p-8 text-center">
        <p className="text-sm font-medium text-ink">The {this.props.label} failed to render.</p>
        <p className="max-w-md text-xs text-ink-faint">{this.state.error.message}</p>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="mt-1 h-7 rounded-md border border-line px-2.5 text-xs text-ink-muted hover:bg-raised hover:text-ink"
        >
          Try again
        </button>
      </div>
    );
  }
}
