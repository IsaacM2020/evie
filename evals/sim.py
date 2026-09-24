"""A pretend Mac for testing the planner: Safari pages keyed by address (pressing a link really
navigates), app screens by name, a world snapshot, and a record of every op. No real app is touched."""
import json

from evie.hands import HandsResult


class SimHands:
    def __init__(self, pages: dict[str, list[dict]] | None = None, apps: dict[str, list[dict]] | None = None,
                 world: dict | None = None, page_text: dict[str, str] | None = None, script_out: str = ""):
        self.pages, self.apps = pages or {}, apps or {}
        self.world = world or {"front_app": "Finder", "apps": ["Finder", "Safari"], "windows": [], "tabs": [], "selected": ""}
        self.page_text = page_text or {}
        self.script_out = script_out
        self.url = ""
        self.front = self.world.get("front_app", "")
        self.focus = "web"  # "web" or an app name: what observe reads
        self.calls: list[tuple[str, dict]] = []
        self.snap = 0
        for t in self.world.get("tabs", []):
            if t.get("current") and t.get("order") == 1:
                self.url = t["url"]

    def _page(self, url: str) -> list[dict] | None:
        """Exact address (ignoring a trailing /), else a key ending in * matches as a prefix."""
        for k, els in self.pages.items():
            if k.rstrip("/") == url.rstrip("/"):
                return els
        for k, els in self.pages.items():
            if k.endswith("*") and url.startswith(k[:-1]):
                return els
        return None

    def _screen(self) -> dict:
        self.snap += 1
        if self.focus == "web":
            els = self._page(self.url)
            if els is None:
                els = [{"id": "w1", "role": "heading", "label": "404 Not Found"}]
            return {"snapshot": f"s{self.snap}", "app": "Safari", "kind": "web", "url": self.url, "window": self.url,
                    "elements": json.dumps(els)}
        return {"snapshot": f"s{self.snap}", "app": self.focus, "kind": "app", "window": self.focus,
                "elements": json.dumps(self.apps.get(self.focus, []))}

    def _el(self, eid: str) -> dict | None:
        els = (self._page(self.url) or []) if self.focus == "web" else self.apps.get(self.focus, [])
        return next((e for e in els if e["id"] == eid), None)

    async def do(self, op, timeout=5.0, **a):
        self.calls.append((op, a))
        if op == "world":
            return HandsResult(True, "ok", {"world": json.dumps(self.world)})
        if op == "observe":
            app = a.get("app")
            if app and app != "Safari":
                self.focus = app
            elif app == "Safari":
                self.focus = "web"
            return HandsResult(True, "ok", self._screen())
        if op == "open_url":
            self.url, self.focus = a["url"], "web"
            return HandsResult(True, "opened", {"window": "11", "index": "3"})
        if op == "use_tab":
            tab = next(t for t in self.world["tabs"] if t["window"] == a["window"] and t["index"] == a["index"])
            self.url, self.focus = tab["url"], "web"
            return HandsResult(True, "using that tab", {"url": self.url})
        if op == "wait_page":
            return HandsResult(True, "loaded", {"url": self.url})
        if op == "press":
            el = self._el(a["id"])
            if el is None:
                return HandsResult(False, "no such element")
            if el.get("href"):
                self.url = el["href"]
            return HandsResult(True, "pressed")
        if op in ("set_text", "key", "menu", "type"):
            return HandsResult(True, "ok")
        if op == "activate":
            self.front, self.focus = a["app"], (a["app"] if a["app"] != "Safari" else "web")
            return HandsResult(True, "ok")
        if op == "screen_info":
            text = next((v for k, v in self.page_text.items() if k.rstrip("/") == self.url.rstrip("/")), "")
            return HandsResult(True, "ok", {"url": self.url, "page_text": text})
        if op == "marked_shot":  # a screenshot with numbered boxes over the elements it read
            els = self.apps.get(self.focus, [])
            return HandsResult(True, "ok", {"png": "iVBORw0KGgo=",
                                            "marks": json.dumps({str(i + 1): e["id"] for i, e in enumerate(els)})})
        if op == "applescript":
            return HandsResult(True, "ok", {"out": self.script_out})
        return HandsResult(False, f"unknown op {op}")

    def ops(self) -> list[str]:
        return [op for op, _ in self.calls]
