"""Deterministic, offline-only artifact renderers for P6.4."""

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    filename: str
    media_type: str
    content: bytes
    sha256: str


def _artifact(filename: str, media_type: str, value: str) -> RenderedArtifact:
    content = value.encode("utf-8")
    if len(content) > 512 * 1024:
        raise ValueError("practice_artifact_budget_exceeded")
    return RenderedArtifact(
        filename=filename,
        media_type=media_type,
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def render_clock_html(*, title: str, accent: str = "#111111") -> RenderedArtifact:
    """Render one dependency-free clock; ``accent`` accepts six-digit hex only."""

    if not title.strip() or len(title) > 80:
        raise ValueError("practice_clock_title_invalid")
    if (
        len(accent) != 7
        or accent[0] != "#"
        or any(character not in "0123456789abcdefABCDEF" for character in accent[1:])
    ):
        raise ValueError("practice_clock_accent_invalid")
    safe_title = html.escape(title, quote=True)
    value = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ min-height: 100vh; margin: 0; display: grid; place-items: center; }}
    main {{ text-align: center; padding: 2rem; border: 2px solid {accent}; border-radius: 1rem; }}
    #clock {{ font-size: clamp(3rem, 12vw, 8rem); font-variant-numeric: tabular-nums; }}
  </style>
</head>
<body>
  <main><h1>{safe_title}</h1><time id="clock" aria-live="off"></time></main>
  <script>
    const clock = document.getElementById('clock');
    const tick = () => {{ clock.textContent = new Date().toLocaleTimeString(); }};
    tick(); setInterval(tick, 1000);
  </script>
</body>
</html>
"""
    return _artifact("clock.html", "text/html; charset=utf-8", value)


def render_slide_deck_html(
    *, title: str, slides: tuple[tuple[str, tuple[str, ...]], ...]
) -> RenderedArtifact:
    """Render an offline HTML slide deck; this is intentionally not called PPTX."""

    if not title.strip() or len(title) > 100 or not 1 <= len(slides) <= 12:
        raise ValueError("practice_slide_deck_invalid")
    sections: list[str] = []
    for heading, bullets in slides:
        if not heading.strip() or len(heading) > 120 or len(bullets) > 8:
            raise ValueError("practice_slide_invalid")
        bullet_html = "".join(f"<li>{html.escape(item)}</li>" for item in bullets)
        sections.append(
            f'<section tabindex="0"><h2>{html.escape(heading)}</h2>'
            f"<ul>{bullet_html}</ul></section>"
        )
    value = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{margin:0;background:#111;color:#fff;font:24px system-ui,sans-serif}}
main{{scroll-snap-type:y mandatory;height:100vh;overflow-y:auto}}
section{{box-sizing:border-box;min-height:100vh;padding:10vh 10vw;scroll-snap-align:start}}
h1,h2{{font-size:clamp(2rem,6vw,5rem)}} li{{margin:.75rem 0}}
</style></head><body><main><section><h1>{title}</h1></section>{sections}</main></body></html>
""".format(title=html.escape(title), sections="".join(sections))
    return _artifact("slides.html", "text/html; charset=utf-8", value)


__all__ = ["RenderedArtifact", "render_clock_html", "render_slide_deck_html"]
