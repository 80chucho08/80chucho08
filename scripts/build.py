"""Builds the profile README cards (light + dark SVGs) from public GitHub data.

Usage:  python scripts/build.py            # fetch fresh data, redraw everything
        python scripts/build.py --offline  # redraw from scripts/state.json only

Only the standard library is used. A token is read from GITHUB_TOKEN, or from
`gh auth token` when running locally.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
STATE = ROOT / "scripts" / "state.json"
PROFILE = json.loads((ROOT / "profile.json").read_text(encoding="utf-8"))

W = 880  # every card shares this width so they line up in the README
MONO = "ui-monospace,SFMono-Regular,'Cascadia Code',Menlo,Consolas,'Liberation Mono',monospace"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

THEMES = {
    "dark": {
        "bg": "#0d1218", "panel": "#131a22", "border": "#253041", "grid": "#1a2330",
        "text": "#e6edf3", "muted": "#8b96a5", "dim": "#5d6876",
        "accent": "#2dd4a7", "accent2": "#f5a524",
        "heat": ["#18212c", "#0e4b3d", "#137a60", "#1ca883", "#2dd4a7"],
    },
    "light": {
        "bg": "#ffffff", "panel": "#f6f8fa", "border": "#d8dee4", "grid": "#eef1f4",
        "text": "#1f2328", "muted": "#59636e", "dim": "#8c959f",
        "accent": "#0d9473", "accent2": "#b86e00",
        "heat": ["#ebeef1", "#b3e8d8", "#6fd1b3", "#24b089", "#0b7f62"],
    },
}


# ---------------------------------------------------------------- data

def token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("No token: set GITHUB_TOKEN or log in with `gh auth login`.")


def graphql(query: str, variables: dict) -> dict:
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token()}", "User-Agent": "profile-readme-build"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


MAIN_Q = """
query($login: String!) {
  user(login: $login) {
    createdAt
    contributionsCollection {
      contributionYears
      contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } }
    }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, first: 100, isFork: false) {
      totalCount
      nodes { stargazerCount primaryLanguage { name color } }
    }
  }
}"""


def fetch() -> dict:
    login = PROFILE["login"]
    u = graphql(MAIN_Q, {"login": login})["user"]
    cc = u["contributionsCollection"]
    days = [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]

    # one aliased query for the total of every year the account has contributions in
    years = sorted(cc["contributionYears"])
    parts = [
        f'y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y}-12-31T23:59:59Z")'
        " { contributionCalendar { totalContributions } }"
        for y in years
    ]
    yq = "query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }"
    yd = graphql(yq, {"login": login})["user"]
    per_year = {str(y): yd[f"y{y}"]["contributionCalendar"]["totalContributions"] for y in years}

    langs: dict[str, dict] = {}
    for n in u["repositories"]["nodes"]:
        lang = n["primaryLanguage"]
        if lang:
            e = langs.setdefault(lang["name"], {"count": 0, "color": lang["color"] or "#888888"})
            e["count"] += 1

    return {
        "fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "last_year": cc["contributionCalendar"]["totalContributions"],
        "per_year": per_year,
        "repos": u["repositories"]["totalCount"],
        "stars": sum(n["stargazerCount"] for n in u["repositories"]["nodes"]),
        "languages": langs,
    }


def streaks(days: list[dict]) -> tuple[int, int]:
    counts = [d["contributionCount"] for d in days]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    # today may simply not have a commit yet, so the current streak may end yesterday
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1
    current = 0
    while i >= 0 and counts[i]:
        current += 1
        i -= 1
    return current, longest


# ---------------------------------------------------------------- svg helpers

def esc(s) -> str:
    return escape(str(s), quote=True)


def svg(width: int, height: int, label: str, body: str, t: dict, rounded: bool = True) -> str:
    r = 14 if rounded else 0
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{esc(label)}"><title>{esc(label)}</title>'
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="{r}" fill="{t["bg"]}" stroke="{t["border"]}"/>'
        f"{body}</svg>"
    )


def text(x, y, s, size, fill, *, family=MONO, weight=400, anchor="start", extra="") -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}" {extra}>{esc(s)}</text>'
    )


def fmt(n: int) -> str:
    return f"{n:,}"


def write(name: str, theme: str, content: str) -> None:
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / f"{name}-{theme}.svg").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------- cards

def hero(t: dict) -> str:
    H = 230
    dots = "".join(
        f'<circle cx="{x}" cy="{y}" r="1.1" fill="{t["grid"]}"/>'
        for x in range(24, W, 24) for y in range(24, H, 24)
    )
    # a wireframe cube, a nod to the Three.js work
    cx, cy, s = 735, 112, 54
    front = [(cx - s, cy - s + 18), (cx + s - 18, cy - s + 18), (cx + s - 18, cy + s), (cx - s, cy + s)]
    back = [(x + 36, y - 36) for x, y in front]
    def poly(pts, **kw):
        p = " ".join(f"{x},{y}" for x, y in pts)
        return f'<polygon points="{p}" fill="none" stroke="{kw["c"]}" stroke-width="{kw.get("w", 1.6)}" {kw.get("x", "")}/>'
    edges = "".join(
        f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="{t["accent"]}" stroke-width="1.6"/>'
        for a, b in zip(front, back)
    )
    cube = (
        poly(back, c=t["dim"], x='stroke-dasharray="4 4"')
        + edges
        + poly(front, c=t["accent"], w=2)
        + f'<circle cx="{back[1][0]}" cy="{back[1][1]}" r="4" fill="{t["accent2"]}"/>'
    )
    tag = PROFILE["tagline"]
    loc = PROFILE["location"]
    pill_w = len(loc) * 7.8 + 34
    body = (
        dots
        + text(48, 62, "~/hello", 14, t["accent"], weight=600)
        + text(48, 112, f"Hi, I'm {PROFILE['name']}", 40, t["text"], family=SANS, weight=700)
        + text(48, 146, tag["en"], 17, t["muted"], family=SANS)
        + text(48, 172, tag["es"], 15, t["dim"], family=SANS, extra='font-style="italic"')
        + f'<rect x="48" y="188" width="{pill_w:.0f}" height="26" rx="13" fill="{t["panel"]}" stroke="{t["border"]}"/>'
        + f'<circle cx="64" cy="201" r="4" fill="{t["accent2"]}"/>'
        + text(76, 205.5, loc, 13, t["muted"])
        + cube
    )
    label = f"Hi, I'm {PROFILE['name']}. {tag['en']} / {tag['es']}"
    return svg(W, H, label, body, t)


BADGE_ICONS = {
    # tiny hand-drawn glyphs, centred on (0, 0), 16px box
    "github": lambda c: f'<path d="M-4 -5 L-8 0 L-4 5 M4 -5 L8 0 L4 5" fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    "site": lambda c: f'<circle r="7" fill="none" stroke="{c}" stroke-width="1.8"/><ellipse rx="3" ry="7" fill="none" stroke="{c}" stroke-width="1.5"/><line x1="-7" x2="7" stroke="{c}" stroke-width="1.5"/>',
    "linkedin": lambda c: f'<rect x="-7.5" y="-7.5" width="15" height="15" rx="3" fill="{c}"/><text y="4.5" text-anchor="middle" font-family="{SANS}" font-size="11" font-weight="700" fill="#fff">in</text>',
    "mail": lambda c: f'<rect x="-8" y="-6" width="16" height="12" rx="2" fill="none" stroke="{c}" stroke-width="1.8"/><path d="M-7 -4 L0 1 L7 -4" fill="none" stroke="{c}" stroke-width="1.6"/>',
}


def badge(link: dict, t: dict) -> tuple[str, int]:
    label = link["label"]
    width = int(len(label) * 8.4 + 58)
    icon = BADGE_ICONS.get(link["kind"], BADGE_ICONS["site"])(t["accent"])
    body = (
        f'<g transform="translate(24 22)">{icon}</g>'
        + text(42, 27, label, 14, t["text"], weight=600)
    )
    out = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="44" viewBox="0 0 {width} 44" role="img" aria-label="{esc(label)}">'
        f'<title>{esc(label)}</title><rect x="0.5" y="0.5" width="{width - 1}" height="43" rx="22" fill="{t["panel"]}" stroke="{t["border"]}"/>'
        f"{body}</svg>"
    )
    return out, width


def stats(data: dict, t: dict) -> str:
    current, longest = streaks(data["days"])
    since = min(data["per_year"]) if data["per_year"] else str(dt.date.today().year)
    total = sum(data["per_year"].values())
    tiles = [
        (fmt(data["last_year"]), "contributions · last year", "contribuciones · último año"),
        (f"{current}d", "current streak", "racha actual"),
        (f"{longest}d", "longest streak (1y)", "racha más larga (1 año)"),
        (fmt(total), f"contributions since {since}", f"contribuciones desde {since}"),
        (str(data["repos"]), "public repos", "repos públicos"),
        (str(data["stars"]), "stars earned", "estrellas recibidas"),
    ]
    H = 346
    body = text(32, 44, "$ gh stats --me", 14, t["accent"], weight=600)
    body += text(W - 32, 44, "on GitHub · en GitHub", 13, t["dim"], anchor="end")
    tw, th, gap = (W - 64 - 2 * 16) / 3, 88, 16
    for i, (num, en, es) in enumerate(tiles):
        x = 32 + (i % 3) * (tw + gap)
        y = 64 + (i // 3) * (th + gap)
        color = t["accent2"] if i == 1 else t["text"]
        body += f'<rect x="{x:.1f}" y="{y}" width="{tw:.1f}" height="{th}" rx="10" fill="{t["panel"]}" stroke="{t["border"]}"/>'
        body += text(x + 18, y + 40, num, 28, color, family=SANS, weight=700)
        body += text(x + 18, y + 62, en, 12.5, t["muted"])
        body += text(x + 18, y + 78, es, 11.5, t["dim"], extra='font-style="italic"')

    # language bar: share of public repos by primary language
    langs = sorted(data["languages"].items(), key=lambda kv: -kv[1]["count"])
    n = sum(v["count"] for _, v in langs) or 1
    top, rest = langs[:6], sum(v["count"] for _, v in langs[6:])
    by = 300
    bx, bw = 32, W - 64
    body += f'<clipPath id="bar"><rect x="{bx}" y="{by}" width="{bw}" height="8" rx="4"/></clipPath><g clip-path="url(#bar)">'
    x = bx
    segs = [(k, v["count"], v["color"]) for k, v in top] + ([("Other", rest, t["dim"])] if rest else [])
    for _, c, color in segs:
        w = bw * c / n
        body += f'<rect x="{x:.1f}" y="{by}" width="{w + 0.5:.1f}" height="8" fill="{color}"/>'
        x += w
    body += "</g>"
    lx = bx
    for name, c, color in segs:
        item = f"{name} {round(100 * c / n)}%"
        body += f'<circle cx="{lx + 5}" cy="{by + 26}" r="5" fill="{color}"/>'
        body += text(lx + 15, by + 30.5, item, 12, t["muted"])
        lx += len(item) * 7.3 + 34
    body += text(32, by - 12, "languages by repo · lenguajes por repo", 12, t["dim"])
    label = (
        f"On GitHub: {fmt(data['last_year'])} contributions in the last year, {current}-day current streak, "
        f"longest streak {longest} days, {fmt(total)} contributions since {since}, "
        f"{data['repos']} public repos, {data['stars']} stars earned."
    )
    return svg(W, H, label, body, t)


def graph(data: dict, t: dict) -> str:
    days = data["days"]
    weeks: list[list[dict]] = []
    for d in days:
        wd = (dt.date.fromisoformat(d["date"]).weekday() + 1) % 7  # Sunday = 0, like GitHub
        if not weeks or wd == 0:
            weeks.append([None] * 7)
        weeks[-1][wd] = d
    mx = max((d["contributionCount"] for d in days), default=0)

    def level(c: int) -> int:
        if c == 0 or mx == 0:
            return 0
        return min(4, 1 + int(3 * c / mx + 0.5)) if mx > 4 else min(4, c)

    cell, gap = 12, 3
    gx = (W - len(weeks) * (cell + gap) + gap) / 2
    gy = 74
    body = text(32, 40, f"{fmt(data['last_year'])} contributions in the last year", 15, t["text"], weight=600)
    body += text(32, 58, f"{fmt(data['last_year'])} contribuciones en el último año", 12.5, t["dim"], extra='font-style="italic"')
    last_month, last_label_wi = None, -9
    months_en = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    for wi, week in enumerate(weeks):
        x = gx + wi * (cell + gap)
        first = next((d for d in week if d), None)
        if first:
            m = dt.date.fromisoformat(first["date"]).month
            if m != last_month:
                # skip a label that would collide with the previous one or run off the right edge
                if wi - last_label_wi >= 3 and wi < len(weeks) - 2:
                    body += text(x, gy + 7 * (cell + gap) + 16, months_en[m - 1], 11, t["dim"])
                    last_label_wi = wi
                last_month = m
        for wd, d in enumerate(week):
            if not d:
                continue
            y = gy + wd * (cell + gap)
            fill = t["heat"][level(d["contributionCount"])]
            body += (
                f'<rect x="{x:.1f}" y="{y}" width="{cell}" height="{cell}" rx="2.5" fill="{fill}">'
                f'<title>{d["date"]}: {d["contributionCount"]}</title></rect>'
            )
    # legend
    lx = W - 32 - 5 * (cell + gap) - 90
    ly = 34
    body += text(lx - 8, ly + 10, "less · menos", 11, t["dim"], anchor="end")
    for i, c in enumerate(t["heat"]):
        body += f'<rect x="{lx + i * (cell + gap)}" y="{ly}" width="{cell}" height="{cell}" rx="2.5" fill="{c}"/>'
    body += text(lx + 5 * (cell + gap) + 6, ly + 10, "more · más", 11, t["dim"])
    H = gy + 7 * (cell + gap) + 34
    return svg(W, H, f"{fmt(data['last_year'])} contributions in the last year", body, t)


def whoami(t: dict) -> str:
    rows = PROFILE["whoami"]
    row_h = 44
    H = 64 + 34 + len(rows) * row_h + 24
    body = (
        f'<path d="M14 0.5 H{W - 14} A13.5 13.5 0 0 1 {W - 0.5} 14 V40 H0.5 V14 A13.5 13.5 0 0 1 14 0.5 Z" fill="{t["panel"]}"/>'
        f'<line x1="0.5" y1="40" x2="{W - 0.5}" y2="40" stroke="{t["border"]}"/>'
        + "".join(f'<circle cx="{24 + i * 18}" cy="20.5" r="5.5" fill="{c}"/>' for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"]))
        + text(W / 2, 25, f"{PROFILE['login']}@github: ~", 12.5, t["dim"], anchor="middle")
        + text(32, 80, "$", 15, t["accent"], weight=700)
        + text(50, 80, "whoami", 15, t["text"], weight=600)
    )
    key_w = max(len(r["key"]) for r in rows) * 9 + 24
    for i, r in enumerate(rows):
        y = 116 + i * row_h
        body += text(50, y, r["key"], 14.5, t["accent2"], weight=600)
        body += text(50 + key_w, y, r["en"], 14.5, t["text"])
        body += text(50 + key_w, y + 18, f"# {r['es']}", 12.5, t["dim"])
    cy = 116 + len(rows) * row_h - 6
    body += text(32, cy, "$", 15, t["accent"], weight=700)
    body += f'<rect x="50" y="{cy - 13}" width="9" height="17" fill="{t["accent"]}"><animate attributeName="opacity" values="1;1;0;0" dur="1.1s" repeatCount="indefinite"/></rect>'
    label = "whoami: " + "; ".join(f"{r['key']}: {r['en']}" for r in rows)
    return svg(W, H, label, body, t)


# ---------------------------------------------------------------- readme

def picture(name: str, alt: str, width: str = "100%", height: str | None = None) -> str:
    size = f'height="{height}"' if height else f'width="{width}"'
    return (
        f'<picture><source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg">'
        f'<img src="assets/{name}-light.svg" {size} alt="{esc(alt)}"></picture>'
    )


def readme(data: dict) -> str:
    current, longest = streaks(data["days"])
    tag = PROFILE["tagline"]
    links = [l for l in PROFILE["links"] if l["url"]]
    badges = "\n".join(
        f'<a href="{esc(l["url"])}">{picture("badge-" + l["kind"], l["label"], height="44")}</a>' for l in links
    )
    total = sum(data["per_year"].values())
    return f"""{picture("hero", f"Hi, I'm {PROFILE['name']}. {tag['en']}")}

<p align="center">
{badges}
</p>

{picture("stats", f"{fmt(data['last_year'])} contributions in the last year, {current}-day streak, longest {longest} days, {fmt(total)} total, {data['repos']} public repos, {data['stars']} stars")}

{picture("graph", f"{fmt(data['last_year'])} contributions in the last year")}

{picture("whoami", "whoami: " + "; ".join(f"{r['key']}: {r['en']}" for r in PROFILE["whoami"]))}

<p align="center"><sub>Cards redrawn every night by a GitHub Action · Tarjetas redibujadas cada noche por un GitHub Action</sub></p>
"""


# ---------------------------------------------------------------- main

def main() -> None:
    offline = "--offline" in sys.argv
    if offline:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        try:
            data = fetch()
            STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except Exception as e:  # keep drawing from the last good copy rather than breaking the profile
            if not STATE.exists():
                raise
            print(f"fetch failed ({e}); drawing from {STATE.name}", file=sys.stderr)
            data = json.loads(STATE.read_text(encoding="utf-8"))

    for name, t in THEMES.items():
        write("hero", name, hero(t))
        write("stats", name, stats(data, t))
        write("graph", name, graph(data, t))
        write("whoami", name, whoami(t))
        for link in PROFILE["links"]:
            if link["url"]:
                write("badge-" + link["kind"], name, badge(link, t)[0])
    (ROOT / "README.md").write_text(readme(data), encoding="utf-8")
    print(f"built: {data['last_year']} contributions last year, {data['repos']} repos")


if __name__ == "__main__":
    main()
