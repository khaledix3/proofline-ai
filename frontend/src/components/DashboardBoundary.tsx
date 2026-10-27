import { tr } from "../i18n";
import { Component, type ReactNode } from "react";
export class DashboardBoundary extends Component<
  {
    children: ReactNode;
  },
  {
    failed: boolean;
  }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main style={{ margin: 0, width: "100%", padding: 40 }}>
        <div className="notice error" role="alert">
          <h2>{tr("Evidence could not be displayed safely")}</h2>
          <p>
            {tr(
              "The response was incomplete or incompatible. No source correction was requested. Refresh the dashboard and check backend compatibility if this continues.",
            )}
          </p>
          <button onClick={() => window.location.reload()}>
            {tr("Reload dashboard")}
          </button>
        </div>
      </main>
    ) : (
      this.props.children
    );
  }
}
