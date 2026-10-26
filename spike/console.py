"""Console setup shared by the spike command-line scripts."""
import sys


def utf8_stdout() -> None:
    """Make print() safe for any text, including when stdout is piped.

    On this Windows machine a piped stdout uses the ANSI code page (cp949), so printing a sign name such as
    'Café' raises UnicodeEncodeError and would abort a paid run halfway. Switch stdout to UTF-8 and replace
    anything that still cannot be written. Streams without reconfigure() (test doubles, StringIO) are left alone.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
