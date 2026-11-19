"""Test-only page controllers for the persistent workspace shell.

Reuse the existing page scripts in a local scope, rather than evaluating fetched
HTML or maintaining a second implementation of account and resource operations.
Production templates and their script entry points remain unchanged.
"""
import re
from html.parser import HTMLParser


PAGES = {
    "home": "index.html", "features": "test-features.html",
    "access": "access.html", "admin": "admin.html",
}
SHARED_SCRIPTS = {"/static/theme.js", "/static/session.js", "/static/performance-monitor.js"}
SHARED_STYLES = {"/static/theme.css", "/static/session.css"}


def prepare_workspace_page(page, name):
    def script(match):
        source = re.search(r'\bsrc="([^"]+)"', match.group(1))
        return match.group(0) if source and source.group(1) in SHARED_SCRIPTS else ""

    page = re.sub(r"<script\b([^>]*)>[\s\S]*?</script>", script, page)

    def style(match):
        source = re.search(r'\bhref="([^"]+)"', match.group(0))
        if source and source.group(1) in SHARED_STYLES:
            return match.group(0)
        return re.sub(r"\s*/?>$", ' data-test-page-style="' + name + '">', match.group(0))

    page = re.sub(r'<style\b[^>]*>|<link\b(?=[^>]*rel="stylesheet")[^>]*>', style, page)
    page = re.sub(r"(<body[^>]*>)", rf'\1<div class="test-page-view" data-test-view="{name}">', page, count=1)
    page = page.replace('data-performance-monitor="disabled"', 'data-performance-monitor="test"', 1)
    if 'data-performance-monitor=' not in page:
        page = page.replace("<html ", '<html data-performance-monitor="test" ', 1)
    return page.replace("</body>", "</div></body>", 1).replace("<!-- PERFORMANCE_MONITOR_SCRIPT -->", "").replace(
        "</head>", '<script src="/static/performance-monitor.js" defer></script></head>', 1,
    )


class _PageScripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = [dict(attrs), ""]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append(self.current)
            self.current = None


def page_controller(static_dir, name):
    if name == "editor":
        scripts = [({}, (static_dir / "codex-tip-editor.js").read_text(encoding="utf-8"))]
    else:
        parser = _PageScripts()
        parser.feed((static_dir / PAGES[name]).read_text(encoding="utf-8"))
        # Classic body scripts run before deferred head scripts in the existing pages.
        scripts = sorted(parser.scripts, key=lambda item: "defer" in item[0])
    sources = []
    for attrs, inline in scripts:
        source = attrs.get("src")
        if source in SHARED_SCRIPTS:
            continue
        if source:
            if not source.startswith("/static/") or ".." in source:
                raise ValueError("Workspace controllers require local static scripts")
            inline = (static_dir / source.removeprefix("/static/")).read_text(encoding="utf-8")
        sources.append(inline)
    return "export function mount(context) {\nconst {document, window, setInterval, clearInterval, ResizeObserver} = context;\n" + "\n;\n".join(sources) + "\n}\n"
