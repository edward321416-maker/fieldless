import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./ErrorBoundary";
import { LedgerReviewProvider } from "./LedgerReviewContext";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <LedgerReviewProvider><App /></LedgerReviewProvider>
    </ErrorBoundary>
  </StrictMode>,
);
