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
      <div role="alert" className="rounded-md border border-danger p-4 text-ink">
        <p className="font-medium">The {this.props.label} failed to render.</p>
        <p className="mt-1 text-xs text-ink-faint">{this.state.error.message}</p>
        <button
          type="button"
          onClick={() => this.setState({ error: null })}
          className="mt-2 rounded-md border border-line-strong px-2 py-1 text-xs text-ink-muted hover:text-ink"
        >
          Try again
        </button>
      </div>
    );
  }
}
