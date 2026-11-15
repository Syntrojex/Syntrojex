#!/usr/bin/env python3
"""Generates assets/github-streak.svg from live GitHub data.
(Set ALL_CARDS=1 to also build github-stats.svg and github-langs.svg.)

Runs with only the standard library.
  * With GITHUB_TOKEN (GitHub Actions provides one): uses the official API
    (all-time contributions, language bytes across all your own repos).
  * Without a token (or if the API is rate-limited): falls back to reading the
    public profile pages (last 12 months of contributions, language per repo).
"""
import html
import json
import os
import re
import urllib.request
from datetime import date, datetime, timedelta, timezone

USER = os.environ.get("GH_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "Syntrojex"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
OUT = os.environ.get("OUT_DIR", "assets")
ALL = bool(os.environ.get("ALL_CARDS"))  # also build the stats + languages cards
TODAY = datetime.now(timezone.utc).date()
e = html.escape


# ----------------------------------------------------------------- fetching
def http(url, data=None, headers=None):
    h = {"User-Agent": "syntrojex-profile-cards"}
    if TOKEN and url.startswith("https://api.github.com"):
        h["Authorization"] = f"Bearer {TOKEN}"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def languages_api():
    repos, page = [], 1
    while True:
        batch = json.loads(http(f"https://api.github.com/users/{USER}/repos?per_page=100&type=owner&page={page}"))
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    repos = [r for r in repos if not r["fork"]]
    stars = sum(r["stargazers_count"] for r in repos)
    totals = {}
    for r in repos:
        for lang, n in json.loads(http(r["languages_url"])).items():
            totals[lang] = totals.get(lang, 0) + n
    return totals, len(repos), stars


def languages_scrape():
    totals, count, stars = {}, 0, 0
    for page in range(1, 11):
        h = http(f"https://github.com/{USER}?tab=repositories&type=source&page={page}")
        items = re.findall(r'itemprop="name codeRepository"', h)
        count += len(items)
        for lang in re.findall(r'itemprop="programmingLanguage">([^<]+)<', h):
            totals[lang] = totals.get(lang, 0) + 1
        for m in re.finditer(r'/stargazers"[^>]*>(.*?)</a>', h, re.S):
            nums = re.findall(r"\d[\d,]*", re.sub(r"<[^>]+>", "", m.group(1)))
            if nums:
                stars += int(nums[0].replace(",", ""))
        if len(items) < 30:
            break
    return totals, count, stars


def gql(query):
    body = json.dumps({"query": query}).encode()
    return json.loads(http("https://api.github.com/graphql", data=body, headers={"Content-Type": "application/json"}))


def contributions_api():
    created = gql('{user(login:"%s"){createdAt}}' % USER)["data"]["user"]["createdAt"]
    days = {}
    acts = {"commits": 0, "prs": 0, "issues": 0, "approx": False}
    for year in range(int(created[:4]), TODAY.year + 1):
        end = f"{year}-12-31T23:59:59Z" if year < TODAY.year else TODAY.isoformat() + "T23:59:59Z"
        q = ('{user(login:"%s"){contributionsCollection(from:"%d-01-01T00:00:00Z",to:"%s")'
             '{totalCommitContributions totalPullRequestContributions totalIssueContributions '
             'contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}') % (USER, year, end)
        cc = gql(q)["data"]["user"]["contributionsCollection"]
        acts["commits"] += cc["totalCommitContributions"]
        acts["prs"] += cc["totalPullRequestContributions"]
        acts["issues"] += cc["totalIssueContributions"]
        for w in cc["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[date.fromisoformat(d["date"])] = d["contributionCount"]
    return days, acts


def followers_api():
    return json.loads(http(f"https://api.github.com/users/{USER}"))["followers"]


def followers_scrape():
    h = http(f"https://github.com/{USER}")
    m = re.search(r'text-bold color-fg-default">([\d,.k]+)</span>\s*followers', h)
    return m.group(1) if m else "—"


def activity_scrape():
    """Public profile only shows percentages, so these numbers are approximate (~)."""
    try:
        h = http(f"https://github.com/{USER}?action=show&controller=profiles&tab=contributions&user_id={USER}"
                 f"&from={TODAY.year}-01-01&to={TODAY.year}-12-31", headers={"X-Requested-With": "XMLHttpRequest"})
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
        tot = re.search(r"([\d,]+) contributions in", t)
        if not tot:
            return None
        total = int(tot.group(1).replace(",", ""))
        pct = lambda w: int((re.search(r"(\d+)% " + w, t) or [0, 0])[1])
        return {"commits": round(total * pct("commits") / 100), "prs": round(total * pct("pull requests") / 100),
                "issues": round(total * pct("issues") / 100), "approx": True}
    except Exception:
        return None


def contributions_scrape():
    h = http(f"https://github.com/users/{USER}/contributions")
    cells = re.findall(r'data-date="(\d{4}-\d{2}-\d{2})" id="(contribution-day-component-\d+-\d+)"', h)
    tips = dict(re.findall(r'for="(contribution-day-component-\d+-\d+)"[^>]*>([^<]*)</tool-tip>', h))
    days = {}
    for d, cid in cells:
        m = re.match(r"(\d+) contribution", tips.get(cid, ""))
        days[date.fromisoformat(d)] = int(m.group(1)) if m else 0
    return days


def get_data():
    langs, repos, stars, followers, acts = {}, 0, 0, 0, None
    try:
        days, acts = contributions_api()
        if ALL:
            langs, repos, stars = languages_api()
            followers = followers_api()
        print("data source: GitHub API")
    except Exception as ex:  # no token / rate limited -> public pages
        print("API unavailable (%s) -> reading public pages" % ex)
        days = contributions_scrape()
        acts = activity_scrape() if ALL else None
        if ALL:
            langs, repos, stars = languages_scrape()
            followers = followers_scrape()
    return langs, repos, stars, followers, {d: n for d, n in days.items() if d <= TODAY}, acts


# --------------------------------------------------------------- streak math
def streaks(days):
    total = sum(days.values())
    # first day with an actual contribution (not Jan 1 of the account's first year)
    active = [d for d, n in days.items() if n > 0]
    first = min(active) if active else (min(days) if days else TODAY)
    # current streak: today may still be empty without breaking it
    cur_end = TODAY if days.get(TODAY, 0) > 0 else TODAY - timedelta(days=1)
    cur_len, d = 0, cur_end
    while days.get(d, 0) > 0:
        cur_len += 1
        d -= timedelta(days=1)
    cur = (d + timedelta(days=1), cur_end) if cur_len else None
    best_len, best, run, start = 0, None, 0, None
    for d in sorted(days):
        if days[d] > 0:
            if run == 0:
                start = d
            run += 1
            if run > best_len:
                best_len, best = run, (start, d)
        else:
            run = 0
    return total, first, cur_len, cur, best_len, best


def fmt(d, year=False):
    return f"{d:%b} {d.day}" + (f", {d.year}" if year else "")


def span(r):
    if not r:
        return "—"
    a, b = r
    if a == b:
        return fmt(a, a.year != TODAY.year)
    cross = a.year != b.year or a.year != TODAY.year
    return f"{fmt(a, cross)} – {fmt(b, cross)}"


# ------------------------------------------------------------------ drawing
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
STYLE = """<style>
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.r{animation:rise .8s cubic-bezier(.2,.8,.2,1) both}
@media (prefers-reduced-motion: reduce){*{animation:none!important}}</style>"""
DEFS = """<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1426"/><stop offset=".6" stop-color="#0f1f33"/><stop offset="1" stop-color="#0b3a4d"/></linearGradient>
  <linearGradient id="shine" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="300" y2="0" spreadMethod="repeat">
    <stop offset="0" stop-color="#00EAFF"/><stop offset=".3" stop-color="#FFFFFF"/><stop offset=".6" stop-color="#0095AD"/><stop offset="1" stop-color="#00EAFF"/>
    <animateTransform attributeName="gradientTransform" type="translate" from="0 0" to="300 0" dur="3.5s" repeatCount="indefinite"/>
  </linearGradient>
</defs>"""


def sweep(W, H):
    return ('<defs><linearGradient id="sweep" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="220" y2="0">'
            '<stop offset="0" stop-color="#00EAFF" stop-opacity="0"/><stop offset=".5" stop-color="#00EAFF" stop-opacity=".95"/><stop offset="1" stop-color="#00EAFF" stop-opacity="0"/>'
            f'<animateTransform attributeName="gradientTransform" type="translate" values="-220 0;{W} 0;{W} 0" keyTimes="0;.55;1" dur="7s" repeatCount="indefinite"/></linearGradient></defs>'
            f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="18" fill="none" stroke="url(#sweep)" stroke-width="1.5"/>')


def start(W, H, title):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{e(title)}" font-family="{FONT}"><title>{e(title)}</title>',
            STYLE, DEFS,
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="url(#bg)" stroke="#1f4560"/>']


def pill(o, x, y, text, dot=False, delay=.7):
    w = int(len(text) * 6.9 + (32 if dot else 22))
    o.append(f'<g class="r" style="animation-delay:{delay}s"><rect x="{x}" y="{y}" width="{w}" height="24" rx="12" fill="none" stroke="#2B5570"/>')
    tx = x + 11
    if dot:
        o.append(f'<circle cx="{x + 15}" cy="{y + 12}" r="3.5" fill="#30D158" style="animation:pulse 2.4s ease-in-out infinite"/>')
        tx = x + 26
    o.append(f'<text x="{tx}" y="{y + 16}" font-size="11.5" font-weight="500" fill="#FFFFFF">{e(text)}</text></g>')
    return w


def streak_card(days):
    total, first, cur_len, cur, best_len, best = streaks(days)
    W, H = 495, 195  # same size as the original streak-stats card
    o = start(W, H, "GitHub streak")
    cols = [
        (82.5, f"{total:,}", "TOTAL CONTRIBUTIONS", f"{fmt(first, True)} – Present", False),
        (247.5, str(cur_len), "CURRENT STREAK", span(cur) if cur_len else "No active streak", True),
        (412.5, str(best_len), "LONGEST STREAK", span(best), False),
    ]
    for i, (cx, num, label, cap, grad) in enumerate(cols):
        d = .1 + i * .12
        fill = "url(#shine)" if grad else "#FFFFFF"
        o.append(f'<g class="r" style="animation-delay:{d:.2f}s">'
                 f'<text x="{cx}" y="104" font-size="42" font-weight="700" letter-spacing="-1" text-anchor="middle" fill="{fill}">{e(num)}</text>'
                 f'<text x="{cx}" y="130" font-size="11" font-weight="600" letter-spacing="1.2" text-anchor="middle" fill="#8FA6B8">{label}</text>'
                 f'<text x="{cx}" y="149" font-size="12" text-anchor="middle" fill="#5B7A90">{e(cap)}</text></g>')
    for x in (165, 330):
        o.append(f'<line x1="{x}" x2="{x}" y1="50" y2="150" stroke="#1f4560"/>')
    o.append(sweep(W, H))
    o.append("</svg>")
    return "\n".join(o)


PALETTE = ["#00EAFF", "#00B8D4", "#4F8CFF", "#7C6CFF", "#0095AD", "#8FA6B8"]


def langs_card(langs, repos):
    W, H = 430, 182
    items = sorted(langs.items(), key=lambda kv: -kv[1])
    if len(items) > 6:
        items = items[:5] + [("Other", sum(v for _, v in items[5:]))]
    total = sum(v for _, v in items) or 1
    o = start(W, H, "Most used languages")
    o.append('<g class="r" style="animation-delay:.1s"><text x="26" y="34" font-size="10.5" font-weight="600" letter-spacing="1.4" fill="#00EAFF">MOST USED LANGUAGES</text></g>')
    o.append('<defs><clipPath id="barclip"><rect x="26" y="48" width="378" height="8" rx="4"/></clipPath></defs>')
    o.append('<rect x="26" y="48" width="378" height="8" rx="4" fill="#00EAFF" fill-opacity=".08"/>')
    o.append('<g clip-path="url(#barclip)"><g style="transform-box:fill-box;transform-origin:left;animation:grow 1.1s cubic-bezier(.2,.8,.2,1) .3s both">')
    x = 26.0
    for i, (name, v) in enumerate(items):
        w = 378 * v / total
        o.append(f'<rect x="{x:.2f}" y="48" width="{w + .6:.2f}" height="8" fill="{PALETTE[i]}"/>')
        x += w
    o.append("</g></g>")
    for i, (name, v) in enumerate(items):
        col, row = divmod(i, 3)
        cx = 26 + col * 196
        cy = 90 + row * 22
        pct = f"{100 * v / total:.1f}%"
        o.append(f'<g class="r" style="animation-delay:{.4 + i * .08:.2f}s">'
                 f'<circle cx="{cx + 4}" cy="{cy - 4}" r="4" fill="{PALETTE[i]}"/>'
                 f'<text x="{cx + 16}" y="{cy}" font-size="12.5" font-weight="500" fill="#FFFFFF">{e(name)}</text>'
                 f'<text x="{cx + 178}" y="{cy}" font-size="12" text-anchor="end" fill="#8FA6B8">{pct}</text></g>')
    o.append(f'<g class="r" style="animation-delay:.9s"><text x="26" y="160" font-size="10.5" fill="#5B7A90">Across {repos} public repositories you own</text></g>')
    o.append(sweep(W, H))
    o.append("</svg>")
    return "\n".join(o)


def stats_card(langs, repos, stars, followers, acts):
    W, H = 430, 182
    o = start(W, H, "GitHub stats")
    o.append('<g class="r" style="animation-delay:.1s"><text x="26" y="34" font-size="10.5" font-weight="600" letter-spacing="1.4" fill="#00EAFF">GITHUB STATS · OVERVIEW</text></g>')

    def val(k):
        if not acts:
            return "—"
        return ("~" if acts.get("approx") else "") + f"{acts[k]:,}"
    fol = followers if isinstance(followers, str) else f"{followers:,}"
    cells = [(f"{stars:,}", "TOTAL STARS"), (fol, "FOLLOWERS"), (str(repos), "REPOSITORIES"),
             (val("commits"), "COMMITS"), (val("prs"), "PULL REQUESTS"), (val("issues"), "ISSUES")]
    xs = [71.5, 215, 358.5]
    for i, (num, label) in enumerate(cells):
        r, c = divmod(i, 3)
        y = 72 + r * 50
        o.append(f'<g class="r" style="animation-delay:{.2 + i * .07:.2f}s">'
                 f'<text x="{xs[c]}" y="{y}" font-size="25" font-weight="700" letter-spacing="-.6" text-anchor="middle" fill="#FFFFFF">{e(num)}</text>'
                 f'<text x="{xs[c]}" y="{y + 15}" font-size="9.5" font-weight="600" letter-spacing="1.1" text-anchor="middle" fill="#8FA6B8">{label}</text></g>')
    for x in (143, 287):
        o.append(f'<line x1="{x}" x2="{x}" y1="52" y2="132" stroke="#1f4560"/>')
    items = sorted(langs.items(), key=lambda kv: -kv[1])
    if len(items) > 6:
        items = items[:5] + [("Other", sum(v for _, v in items[5:]))]
    total = sum(v for _, v in items) or 1
    o.append('<defs><clipPath id="barclip"><rect x="26" y="156" width="378" height="6" rx="3"/></clipPath></defs>')
    o.append('<rect x="26" y="156" width="378" height="6" rx="3" fill="#00EAFF" fill-opacity=".08"/>')
    o.append('<g clip-path="url(#barclip)"><g style="transform-box:fill-box;transform-origin:left;animation:grow 1.1s cubic-bezier(.2,.8,.2,1) .3s both">')
    x = 26.0
    for i, (name, v) in enumerate(items):
        w = 378 * v / total
        o.append(f'<rect x="{x:.2f}" y="156" width="{w + .6:.2f}" height="6" fill="{PALETTE[i]}"/>')
        x += w
    o.append("</g></g>")
    o.append(sweep(W, H))
    o.append("</svg>")
    return "\n".join(o)


def main():
    langs, repos, stars, followers, days, acts = get_data()
    os.makedirs(OUT, exist_ok=True)
    cards = [("github-streak.svg", streak_card(days))]
    if ALL:
        cards += [("github-stats.svg", stats_card(langs, repos, stars, followers, acts)),
                  ("github-langs.svg", langs_card(langs, repos))]
    for name, svg in cards:
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(svg)
    print("done ->", ", ".join(n for n, _ in cards))


if __name__ == "__main__":
    main()
