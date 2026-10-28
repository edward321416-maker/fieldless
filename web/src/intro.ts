// A first visit (no survey in the address) opens on what Fieldless does; a shared link to a survey opens on it
// (docs/specs/2026-10-29-first-screen-design.md 1).
export const startsWithIntro = (hash: string) => !/scan=\d+/.test(hash);
