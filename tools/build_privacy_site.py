"""Gera o site estático da política de privacidade (public/) a partir de site.json.

    python tools/build_privacy_site.py [raiz do repo do site]

Fonte do texto (site.json → "source"):
  {"type": "android-strings", "res": "<app>/app/src/main/res", "sections": ["data", ...]}
      mesmo texto da tela de política dentro do app. Strings usadas em cada pasta de idioma:
      privacy_policy_title, privacy_updated (com %1$s = data), privacy_intro,
      privacy_section_<nome>_title e privacy_section_<nome>_body. %1$s no corpo = e-mail.
  {"type": "markdown", "dir": "content"}
      content/<code>.md: "# Título", parágrafos, "## Seção", listas "- ", **negrito**,
      [texto](url). Páginas extras: content/<code>.<pagina>.md → <path><pagina>/index.html
      (ex.: delete-account, exigida pela Play quando o app tem contas).

Cada idioma em "languages" vira uma página; o primeiro fica na raiz "/" e redireciona
navegadores em outro idioma (a menos que o visitante escolha um, ou use ?lang=<code>).
Também grava 404.html e, se "appAdsTxt" existir, public/app-ads.txt. Depois: commit + push;
o GitHub Actions publica public/ no Firebase Hosting.
"""
import html
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
CFG = json.load(open(os.path.join(ROOT, "site.json"), encoding="utf-8"))
OUT = os.path.join(ROOT, "public")
EMAIL = CFG["email"]
BASE = CFG["baseUrl"].rstrip("/")
LANGS = CFG["languages"]
THEME = {"primary": "#12417a", "primaryDark": "#a9c7ff", "chip": "#d6e3ff", "onChip": "#001b3d",
         "chipDark": "#2b4678", "onChipDark": "#d6e3ff", **CFG.get("theme", {})}

URL = re.compile(r"(?<![\w/\"=])((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}/[^\s,;)<]*[^\s,;).<])")
EMAIL_RE = re.compile(r"(?<![\w.])([\w.+-]+@[\w-]+\.[\w.]+\w)")


def linkify(escaped):
    escaped = URL.sub(lambda m: f'<a href="{m.group(1) if m.group(1).startswith("http") else "https://" + m.group(1)}" rel="noopener">{m.group(1)}</a>', escaped)
    return EMAIL_RE.sub(r'<a href="mailto:\1">\1</a>', escaped)


# ---------- fontes ----------
def android_strings(lang):
    res = CFG["source"]["res"]
    if not os.path.isabs(res):
        res = os.path.join(ROOT, res)
    strings = {}
    for folder in ("values", lang["resFolder"]):
        f = os.path.join(res, folder, "strings.xml")
        if not os.path.exists(f):
            continue
        for e in ET.parse(f).getroot().iter("string"):
            text = "".join(e.itertext()).replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")
            strings[e.get("name")] = text
    return strings


def paragraphs(text):
    """Texto puro → <p>/<ul>, com links e e-mails."""
    out, items = [], []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if all(re.match(r"\s*[•\-*]\s+", l) for l in lines):
            items = "".join(f"<li>{linkify(html.escape(re.sub(r'^[•*-]\s+', '', l.strip())))}</li>" for l in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append("<p>" + "<br>".join(linkify(html.escape(l)) for l in lines) + "</p>")
    return "\n".join(out)


def from_strings(lang):
    s = android_strings(lang)
    title = s["privacy_policy_title"]
    updated = s.get("privacy_updated", "%1$s").replace("%1$s", lang["date"])
    intro = paragraphs(s.get("privacy_intro", "").replace("%1$s", EMAIL))
    sections = []
    for name in CFG["source"]["sections"]:
        body = s[f"privacy_section_{name}_body"].replace("%1$s", EMAIL)
        sections.append((name, s[f"privacy_section_{name}_title"], paragraphs(body)))
    return title, updated, intro, sections


def inline_md(text):
    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" rel="noopener">\1</a>', t)
    if "<a " not in t:
        t = linkify(t)
    return t


def from_markdown(lang, page=None):
    name = f"{lang['code']}.{page}.md" if page else f"{lang['code']}.md"
    text = open(os.path.join(ROOT, CFG["source"].get("dir", "content"), name), encoding="utf-8").read()
    text = text.replace("{email}", EMAIL).replace("{date}", lang["date"]).replace("{app}", lang["appName"])
    title, intro, sections, current = "", [], [], None
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().split("\n")
        if lines[0].startswith("# "):
            title = lines[0][2:].strip()
            lines = lines[1:]
        if lines and lines[0].startswith("## "):
            current = [re.sub(r"\W+", "-", lines[0][3:].lower()).strip("-"), lines[0][3:].strip(), []]
            sections.append(current)
            lines = lines[1:]
        if not lines:
            continue
        if all(re.match(r"[-*]\s+", l) for l in lines):
            chunk = "<ul>" + "".join(f"<li>{inline_md(l[2:].strip())}</li>" for l in lines) + "</ul>"
        else:
            chunk = "<p>" + " ".join(inline_md(l) for l in lines) + "</p>"
        (current[2] if current else intro).append(chunk)
    updated = lang.get("updatedLabel", "Last updated: {date}").replace("{date}", lang["date"])
    return title, updated, "\n".join(intro), [(i, t, "\n".join(b)) for i, t, b in sections]


# ---------- página ----------
STYLE = """
:root { color-scheme: light dark;
  --bg: #f9f9fe; --surface: #ffffff; --text: #1a1c20; --muted: #434750;
  --primary: %(primary)s; --outline: #c3c6d1; --chip: %(chip)s; --on-chip: %(onChip)s; }
@media (prefers-color-scheme: dark) { :root {
  --bg: #111318; --surface: #1d2024; --text: #e2e2e9; --muted: #c3c6d1;
  --primary: %(primaryDark)s; --outline: #43474e; --chip: %(chipDark)s; --on-chip: %(onChipDark)s; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }
header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; justify-content: space-between; }
.brand { display: flex; align-items: center; gap: 12px; font-weight: 700; font-size: 20px; color: var(--primary); }
.brand img { width: 40px; height: 40px; border-radius: 10px; }
nav { display: flex; gap: 8px; flex-wrap: wrap; }
nav a { display: inline-flex; align-items: center; min-height: 40px; padding: 0 14px; border-radius: 20px;
  border: 1px solid var(--outline); color: var(--text); text-decoration: none; font-size: 14px; }
nav a[aria-current="page"] { background: var(--chip); color: var(--on-chip); border-color: transparent; font-weight: 600; }
h1 { font-size: 30px; line-height: 1.25; margin: 32px 0 4px; }
.updated { color: var(--muted); margin: 0 0 24px; font-size: 14px; }
.intro { font-size: 17px; }
section { background: var(--surface); border: 1px solid var(--outline); border-radius: 16px; padding: 16px 20px; margin: 16px 0; }
h2 { font-size: 18px; margin: 0 0 6px; color: var(--primary); }
section p, section ul { margin: 0 0 8px; } section > :last-child { margin-bottom: 0; }
a { color: var(--primary); overflow-wrap: anywhere; }
footer { margin-top: 32px; color: var(--muted); font-size: 14px; }
""" % THEME


def redirect_script():
    codes = {l["code"]: l["path"] for l in LANGS[1:]}
    return """<script>
(function () { try {
  var p = new URLSearchParams(location.search), first = %s, map = %s;
  if (p.get('lang') === first) { localStorage.setItem('lang', first); return; }
  var lang = localStorage.getItem('lang') || (navigator.language || '').slice(0, 2).toLowerCase();
  if (map[lang] && location.pathname === '/') location.replace(map[lang] + location.hash);
} catch (e) {} })();
</script>""" % (json.dumps(LANGS[0]["code"]), json.dumps(codes))


REMEMBER = """<script>
document.querySelectorAll('nav a[data-lang]').forEach(function (a) {
  a.addEventListener('click', function () { try { localStorage.setItem('lang', a.getAttribute('data-lang')); } catch (e) {} });
});
</script>"""


def render(lang, content, page=None):
    title, updated, intro, sections = content
    suffix = f"{page}/" if page else ""
    links = []
    for i, o in enumerate(LANGS):
        href = "/?lang=" + o["code"] if i == 0 and not page else o["path"] + suffix
        current = ' aria-current="page"' if o is lang else ""
        links.append(f'<a href="{href}" hreflang="{o["htmlLang"]}" data-lang="{o["code"]}"{current}>{html.escape(o["label"])}</a>')
    nav = "".join(links)
    nav_label = html.escape(lang.get("navLabel", "Language"))
    alternates = "\n".join(f'  <link rel="alternate" hreflang="{o["htmlLang"]}" href="{BASE}{o["path"]}{suffix}">' for o in LANGS)
    secs = "\n".join(f'  <section id="{sid}">\n    <h2>{html.escape(t)}</h2>\n    {body}\n  </section>' for sid, t, body in sections)
    icon = '<img src="/icon.png" alt="">' if os.path.exists(os.path.join(OUT, "icon.png")) else ""
    is_root = lang is LANGS[0] and not page
    return f"""<!DOCTYPE html>
<html lang="{lang['htmlLang']}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · {html.escape(lang['appName'])}</title>
  <meta name="description" content="{html.escape(lang.get('description', title))}">
  <link rel="canonical" href="{BASE}{lang['path']}{suffix}">
{alternates}
  {redirect_script() if is_root and len(LANGS) > 1 else ''}
  <style>{STYLE}</style>
</head>
<body>
<main>
  <header>
    <div class="brand">{icon}{html.escape(lang['appName'])}</div>
    {f'<nav aria-label="{nav_label}">{nav}</nav>' if len(LANGS) > 1 else ''}
  </header>
  <h1>{html.escape(title)}</h1>
  <p class="updated">{html.escape(updated)}</p>
  <div class="intro">{intro}</div>
{secs}
  <footer>© {CFG.get('year', '')} {html.escape(CFG.get('developer', ''))} · <a href="mailto:{EMAIL}">{EMAIL}</a></footer>
</main>
{REMEMBER}
</body>
</html>
"""


def write(rel, text):
    target = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("gravado", os.path.relpath(target, ROOT))


def main():
    os.makedirs(OUT, exist_ok=True)
    icon = CFG.get("icon")
    if icon:
        src = icon if os.path.isabs(icon) else os.path.join(ROOT, icon)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(OUT, "icon.png"))
    kind = CFG["source"]["type"]
    for lang in LANGS:
        content = from_strings(lang) if kind == "android-strings" else from_markdown(lang)
        write(os.path.join(lang["path"].strip("/"), "index.html"), render(lang, content))
        for page in CFG.get("extraPages", []):
            write(os.path.join(lang["path"].strip("/"), page, "index.html"), render(lang, from_markdown(lang, page), page))
    links = " · ".join(f'<a href="{l["path"]}">{html.escape(l["label"])}</a>' for l in LANGS)
    write("404.html", f'<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                      f'<title>404</title><style>{STYLE}</style></head><body><main><h1>404</h1><p>{links}</p></main></body></html>\n')
    if CFG.get("appAdsTxt"):
        write("app-ads.txt", CFG["appAdsTxt"].strip() + "\n")


if __name__ == "__main__":
    main()
