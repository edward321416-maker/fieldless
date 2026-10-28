const RUN_IT = "https://github.com/edward321416-maker/fieldless#run-the-app";

// The public demo is a static site: it shows surveys already run and cannot start new ones.
export default function DemoNotice() {
  return (
    <section className="panel-section" aria-labelledby="demo-notice-title">
      <h2 id="demo-notice-title">About this demo</h2>
      <p>
        This public demo shows street surveys Fieldless has already run. Every finding on the map has its street
        photo, and the numbers show what the survey took.
      </p>
      <p className="hint">
        To survey new streets, run Fieldless with your own Meta and Mapillary keys:{" "}
        <a href={RUN_IT} target="_blank" rel="noreferrer">how to run it</a>.
      </p>
    </section>
  );
}
