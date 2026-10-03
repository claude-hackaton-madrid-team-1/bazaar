"""Check the offline deck. Optionally walk the already-loaded Chrome session."""

import argparse
import json
import os
import re
import subprocess
import time
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class DeckParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.slides = []
        self.images = []
        self.external = []
        self.comments = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section" and "slide" in attrs.get("class", "").split():
            self.slides.append((attrs["id"], int(attrs["data-seconds"])))
        if tag == "img":
            self.images.append(attrs)
        for attr in ("src", "href"):
            if attrs.get(attr, "").startswith("http"):
                self.external.append(attrs[attr])

    def handle_comment(self, data):
        self.comments.append(data)


def check_static():
    html = (ROOT / "index.html").read_text()
    deck = DeckParser()
    deck.feed(html)
    assert [s[0] for s in deck.slides] == [
        "hook", "voice", "infra", "safe", "results", "memory", "close"
    ]
    assert sum(s[1] for s in deck.slides) == 165
    assert not deck.external, "Offline deck must not depend on a network resource"
    assert len(deck.images) == 2 and all(
        i["src"].startswith("data:image/png;base64,") and i.get("alt")
        for i in deck.images
    )
    assert len(re.findall(r'data-node="\d"', html)) == 7
    for value in ("15", "15.02", "27"):
        assert re.search(r"<!-- Source:[^>]*-->\s*<[^>]+>" + re.escape(value), html), value
    assert "Source: vendor/bazaar-kit/RULES.md Fair play" in html
    assert not re.search(r"postgres(?:ql)?://|Bearer\s+[A-Za-z0-9_-]{12,}|tk-[A-Za-z0-9-]{8,}", html)
    script = (ROOT / "script-3min.md").read_text()
    counts = {}
    for language, segment in zip(("English", "Spanish"), script.split("## English\n")[1].split("## Spanish\n")):
        segment = segment.split("## Rehearsal controls")[0]
        paragraphs = [p for p in segment.split("\n\n") if p.strip() and not p.startswith(("###", "Cue:", "Sources:", "Source:"))]
        counts[language] = sum(len(re.findall(r"\b[\w'-]+\b", p)) for p in paragraphs)
    print(f"PASS: {len(deck.slides)} slides; 165 s; offline images; numeric source comments.")
    print("Spoken word counts:", json.dumps(counts))
    return deck


def check_browser(session):
    # ponytail: use the installed CLI and an existing isolated browser session;
    # add direct CDP only if the CLI can no longer run these checks.
    prefix = ["agent-browser", "--session", session]
    env = os.environ | {"AGENT_BROWSER_SOCKET_DIR": "/private/tmp/bazaar-pitch-browser"}

    def run(*args, code=None):
        result = subprocess.run(prefix + list(args), input=code, capture_output=True, text=True, env=env, check=True)
        return result.stdout.strip()

    def evaluate(code):
        return json.loads(run("eval", "--stdin", code=code))

    run("set", "viewport", "1600", "900")
    evaluate("document.body.classList.add('capture');deck.show(0);true")
    report = {"slides": [], "total_seconds": 165, "browser": "cloud Chrome via agent-browser"}
    for index, name in enumerate(("hook", "voice", "infra", "safe", "results", "memory", "close")):
        if index:
            run("press", "ArrowRight")
        time.sleep(0.85)
        check = evaluate("""(()=>{
          const s=document.querySelector('.slide.active'),b=s.getBoundingClientRect();
          const out=[...s.querySelectorAll('h1,h2,p,img,.policy,.infra-caption,.stats,.node,.voice-route,.provider')].filter(e=>{
            const r=e.getBoundingClientRect();return r.left<b.left-1||r.right>b.right+1||r.top<b.top-1||r.bottom>b.bottom+1;
          }).map(e=>e.className||e.tagName);
          return {current:deck.current,id:s.id,overflow:out,imagesLoaded:[...s.querySelectorAll('img')].every(i=>i.complete&&i.naturalWidth>0),
            sensitive:/(?:postgres(?:ql)?:\\/\\/|tk-[\\w-]{6,}|bk-[\\w-]{6,}|Bearer\\s+\\S+|[\\w.+-]+@[\\w.-]+\\.[a-z]{2,})/i.test(s.innerText)};
        })()""")
        assert check["current"] == index and check["id"] == name, check
        assert not check["overflow"] and not check["sensitive"] and check["imagesLoaded"], check
        if index == 2:
            architecture = []
            for node in range(7):
                architecture.append(evaluate(f"deck.revealNode({node});({{count:document.querySelectorAll('.node.revealed').length,caption:document.querySelector('#infra-caption').textContent}})"))
                assert architecture[-1]["count"] == node + 1
            check["architecture"] = architecture
            evaluate("""document.querySelector('#infra-caption').innerHTML='<!-- Source: vendor/bazaar-kit/RULES.md, Fair play. --><span class="budget">5 req/s</span> shared';true""")
            time.sleep(1.1)
        run("screenshot", str(ROOT / "preview" / f"{index + 1:02}-{name}.png"))
        report["slides"].append(check)
    run("press", "ArrowLeft")
    assert evaluate("deck.current") == 5
    run("press", "Home")
    run("press", "Space")
    assert evaluate("deck.current") == 1
    run("press", "Home")
    run("click", "#stage")
    assert evaluate("deck.current") == 1
    run("press", "Home")
    run("press", "PageDown")
    assert evaluate("deck.current") == 1
    run("press", "PageUp")
    assert evaluate("deck.current") == 0
    assert evaluate("deck.seconds.reduce((a,b)=>a+b,0)") == 165
    run("focus", "#next")
    run("press", "ArrowRight")
    assert evaluate("deck.current") == 1
    report["navigation"] = "PASS: ArrowRight, ArrowLeft, Space, click, Home, PageDown, PageUp"
    (ROOT / "preview" / "checks.json").write_text(json.dumps(report, indent=2) + "\n")
    print("PASS: seven Chrome slide captures; no canvas overflow; images loaded; architecture builds; navigation.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser-session", help="Already-loaded agent-browser session; never starts a game service")
    args = parser.parse_args()
    check_static()
    if args.browser_session:
        check_browser(args.browser_session)
