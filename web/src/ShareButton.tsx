import { useState } from "react";

interface Props {
  label?: string;
}

// Copies the address of what is on screen ("#scan=17&item=4"), so whoever opens it sees the same survey and item.
export default function ShareButton({ label = "Share" }: Props) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setState("copied");
    } catch {
      setState("failed");
    }
    window.setTimeout(() => setState("idle"), 2500);
  }
  return (
    <button type="button" className="share-button" onClick={copy} aria-live="polite">
      {state === "copied" ? "Link copied" : state === "failed" ? "Copy the address bar" : label}
    </button>
  );
}
