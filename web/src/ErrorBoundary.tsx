import { Component, type ErrorInfo, type ReactNode } from "react";

// A crash while drawing the page leaves a way back, not a blank screen.
export default class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="page-crashed" role="alert">
        <h1>Fieldless</h1>
        <p>This page stopped working.</p>
        <p><a href="/">Open the first page</a></p>
      </main>
    );
  }
}
