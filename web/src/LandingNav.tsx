// The first screen's links to the overview's sections. "Examples" goes to the example surveys, which the overview shows
// only when there is a survey besides the open one (Overview, `others`), so the link is there only then.
export default function LandingNav({ hasExamples }: { hasExamples: boolean }) {
  return (
    <nav className="landing-nav" aria-label="Landing page sections">
      <a href="#overview-items">What it finds</a>
      <a href="#overview-flow">How it works</a>
      <a href="#overview-checks">Validation</a>
      {hasExamples && <a href="#overview-cities">Examples</a>}
    </nav>
  );
}
