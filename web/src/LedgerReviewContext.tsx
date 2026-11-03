import { createContext, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type { ReviewState } from "./LedgerReviewPane";

interface StoredReview {
  scanId: number;
  signature: string;
  open: boolean;
  state: ReviewState;
}

// Page memory survives opening a map inspector. It is never sent to a server
// or stored in the browser, and a reload or another scan clears it.
export const LedgerReviewContext = createContext<{
  session: StoredReview | null;
  setSession: Dispatch<SetStateAction<StoredReview | null>>;
} | null>(null);

export function LedgerReviewProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<StoredReview | null>(null);
  const value = useMemo(() => ({ session, setSession }), [session]);
  return <LedgerReviewContext.Provider value={value}>{children}</LedgerReviewContext.Provider>;
}
