import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.tsx";
import { DashboardBoundary } from "./components/DashboardBoundary";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DashboardBoundary>
      <App />
    </DashboardBoundary>
  </StrictMode>,
);
