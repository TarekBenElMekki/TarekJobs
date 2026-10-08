"""Deterministic Keejob parsing ported from the proven T/TEA collector.

Source SHA256: 84859850B95BEFDF69FA6EAED47BEA688200EDF1FFB38DEC6C2FC69B931D0FB1
History: 3fe294e, bdedaf6, 8bcb5ea plus inspected working-tree campaign changes.
No runtime dependency on T/TEA; collector/scorer/session code is deliberately absent.
TN-1 adaptations: URL-encoded/Unicode slugs, bounded 1000-card parse, unknown company.
"""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import json
import re
import unicodedata
from urllib.parse import parse_qsl, urljoin, urlsplit


MAIN_SOURCE_URL = "https://www.keejob.com/offres-emploi/"

OFFER_PATH = re.compile(r"/offres-emploi/([0-9]+)/([^/?#\s]+)/?")

COMPANY_PATH = re.compile(
    r"/offres-emploi/(?:"
    r"companies/[0-9]+(?:/[A-Za-z0-9_-]+)?"
    r"|entreprises/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*"
    r")/?"
)

def validate_cap(value: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= 1000:
        raise ValueError(f"{name} must be an integer from 1 to 1000")

def public_url(url: str, *, detail: bool = False) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in ("www.keejob.com", "keejob.com")
            or parsed.username or parsed.password):
        raise ValueError("Only public HTTPS Keejob URLs are allowed")
    offer = OFFER_PATH.fullmatch(parsed.path)
    listing = (parsed.path == "/offres-emploi/" or
               re.fullmatch(r"/offres-emploi/metiers/[A-Za-z0-9_-]+/", parsed.path))
    if not offer and (detail or not listing):
        raise ValueError("Only public Keejob offer or category paths are allowed")
    canonical = "https://www.keejob.com" + parsed.path.rstrip("/") + "/"
    # Offer identity ignores tracking. Listings retain only the inspected page parameter.
    if listing:
        pages = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key == "page"]
        if pages:
            if len(pages) != 1 or not re.fullmatch(r"[0-9]{1,5}", pages[0]) or not 1 <= int(pages[0]) <= 10000:
                raise ValueError("Invalid public listing page")
            if int(pages[0]) > 1:
                canonical += "?page=" + str(int(pages[0]))
    return canonical

def company_profile_url(value: str, base_url: str = "https://www.keejob.com/") -> str:
    """Canonicalize only explicit public Keejob employer-profile URLs."""

    parsed = urlsplit(urljoin(base_url, value))

    if (
        parsed.scheme != "https"
        or parsed.netloc not in ("www.keejob.com", "keejob.com")
        or parsed.username
        or parsed.password
        or not COMPANY_PATH.fullmatch(parsed.path)
    ):
        raise ValueError("Expected a public Keejob company profile URL")

    return "https://www.keejob.com" + parsed.path.rstrip("/") + "/"

@dataclass
class _Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)

    def text(self) -> str:
        return " ".join(" ".join(child.text() if isinstance(child, _Node) else child
                                 for child in self.children).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

class _HTML(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

def discover_offers(html: str, source_url: str, limit: int = 15) -> list[str]:
    validate_cap(limit, "max_jobs")
    source_url = public_url(source_url)
    offers, seen = [], set()
    for node in _HTML(html).root.walk():
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        try:
            url = public_url(urljoin(source_url, node.attrs["href"]), detail=True)
        except ValueError:
            continue
        reference = OFFER_PATH.fullmatch(urlsplit(url).path).group(1)
        if reference not in seen:
            seen.add(reference)
            offers.append(url)
            if len(offers) == limit:
                break
    return offers

def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold().replace("’", "'"))
                   if not unicodedata.combining(char))

def extract_offer(html: str, url: str) -> dict:
    url = public_url(url, detail=True)
    root = _HTML(html).root
    nodes = list(root.walk())
    record = {"source_url": url, "source_reference": OFFER_PATH.fullmatch(urlsplit(url).path).group(1)}
    # Structured public page markup, when present, supplements labelled text.
    for node in nodes:
        if node.tag == "script" and node.attrs.get("type") == "application/ld+json":
            try:
                data = json.loads(node.text())
                entries = data if isinstance(data, list) else data.get("@graph", [data])
                for entry in entries:
                    if not isinstance(entry, dict) or entry.get("@type") != "JobPosting":
                        continue
                    for source, target in (("title", "title"), ("datePosted", "publication_date"),
                                           ("employmentType", "contract"), ("description", "description")):
                        if isinstance(entry.get(source), str):
                            record[target] = _HTML(entry[source]).root.text()
                    organization = entry.get("hiringOrganization", {})
                    if isinstance(organization, dict) and isinstance(organization.get("name"), str):
                        record["company"] = organization["name"]
                    location = entry.get("jobLocation", {})
                    if isinstance(location, dict):
                        address = location.get("address", {})
                        if isinstance(address, dict) and isinstance(address.get("addressLocality"), str):
                            record["location"] = address["addressLocality"]
            except (ValueError, TypeError, AttributeError):
                pass
    aliases = {"entreprise": "company", "societe": "company", "employeur": "company",
               "lieu de travail": "location", "localisation": "location", "region": "location",
               "type de contrat": "contract", "contrat": "contract", "type d'emploi": "contract",
               "experience": "experience", "experience requise": "experience",
               "niveau d'etude": "education", "niveau d'etudes": "education",
               "etude": "education", "formation": "education", "langues": "languages", "langue": "languages",
               "date de publication": "publication_date", "publiee le": "publication_date",
               "publie le": "publication_date"}
    # Visit small elements first so an enclosing page container cannot consume
    # several unrelated fields as a single labelled value.
    for node in sorted(nodes, key=lambda item: len(item.text())):
        if node.tag == "h1" and node.text():
            record.setdefault("title", node.text())
        prop = node.attrs.get("itemprop")
        if prop in ("description", "datePosted", "qualifications", "experienceRequirements"):
            target = {"datePosted": "publication_date", "qualifications": "education",
                      "experienceRequirements": "experience"}.get(prop, prop)
            record.setdefault(target, node.attrs.get("content") or node.text())
        if node.tag == "a" and node.text() and node.attrs.get("href"):
            try:
                profile_url = company_profile_url(node.attrs["href"], url)
            except ValueError:
                profile_url = None

            if profile_url:
                record.setdefault("company", node.text())
                record.setdefault("company_profile_url", profile_url)
        if node.tag not in ("div", "p", "li", "tr", "dd", "dt", "span"):
            continue
        text = node.text()
        for label, target in aliases.items():
            match = re.match(r"^" + re.escape(label) + r"\s*:\s*(.+)$", _fold(text))
            if match and len(text) < 1000:
                record.setdefault(target, text.split(":", 1)[1].strip())
    # Also accept semantic label/value siblings without a printed colon.
    for parent in nodes:
        children = [child for child in parent.children if isinstance(child, _Node)]
        for label, value in zip(children, children[1:]):
            target = aliases.get(_fold(label.text()).rstrip(" :"))
            if target and value.text() and len(value.text()) < 1000:
                record.setdefault(target, value.text())
    if not record.get("description"):
        # Semantic heading followed by sibling content, not presentation classes.
        for parent in nodes:
            collecting, chunks = False, []
            for child in parent.children:
                if not isinstance(child, _Node):
                    continue
                if child.tag in ("h2", "h3", "h4"):
                    if collecting:
                        break
                    collecting = "description" in _fold(child.text())
                elif collecting and child.tag not in ("script", "style", "form"):
                    chunks.append(child.text())
            if chunks:
                record["description"] = "\n".join(chunks)
                break
    if not record.get("title"):
        raise ValueError("Offer lacks title")
    record.setdefault("company", "Unknown")
    return record

def _looks_like_challenge(text: str) -> bool:
    """Recognize obvious block/challenge pages; never attempt to solve them."""

    if not isinstance(text, str):
        return True

    folded = _fold(
        text[:250_000]
    )

    strong_markers = (
        "cf-chl-",
        "challenge-platform",
        "g-recaptcha",
        "hcaptcha",
    )

    if any(
        marker in folded
        for marker in strong_markers
    ):
        return True

    phrases = (
        "verify you are human",
        "verification humaine",
        "verifiez que vous etes humain",
        "checking your browser",
        "attention required cloudflare",
        "unusual traffic",
        "access denied",
    )

    return any(
        phrase in folded
        for phrase in phrases
    )

def discover_cards(html: str, source_url: str, limit: int = 20) -> list[dict]:
    urls = discover_offers(html, source_url, limit)
    root = _HTML(html).root
    parents = {id(child): node for node in root.walk() for child in node.children if isinstance(child, _Node)}
    anchors = {}
    def anchor_rank(node):
        parent = parents.get(id(node))
        return (bool(parent and parent.tag in ("h1", "h2", "h3", "h4")), len(node.text()))

    for node in root.walk():
        if node.tag == "a" and node.attrs.get("href"):
            try:
                url = public_url(urljoin(source_url, node.attrs["href"]), detail=True)
                if url not in anchors or anchor_rank(node) > anchor_rank(anchors[url]):
                    anchors[url] = node
            except ValueError:
                pass
    cards = []
    for url in urls:
        anchor = anchors[url]
        container = anchor
        parent = parents.get(id(container))
        # Never let a surrounding multi-offer page become one card's evidence.
        while parent and parent.tag not in ("root", "body", "html", "main"):
            if len(discover_offers(_node_links(parent), source_url, 20)) != 1:
                break
            container = parent
            parent = parents.get(id(parent))
        card = {"source_url": url, "source_reference": OFFER_PATH.fullmatch(urlsplit(url).path).group(1),
                "title": anchor.text()[:500], "card_text": container.text()[:2000]}
        for node in container.walk():
            prop = node.attrs.get("itemprop") or ""
            classes = (node.attrs.get("class") or "").lower().split()
            for target, labels in (("company", ("company", "company-name", "hiringorganization")),
                                   ("location", ("location", "job-location", "addresslocality")),
                                   ("publication_date", ("date", "dateposted", "publication-date"))):
                if prop.lower() in labels or any(label in classes for label in labels) or (target == "publication_date" and node.tag == "time"):
                    card.setdefault(target, (node.attrs.get("content") or node.text())[:500])
            if node.tag == "a" and node.attrs.get("href"):
                try:
                    profile_url = company_profile_url(
                        node.attrs["href"],
                        source_url,
                    )
                except ValueError:
                    profile_url = None

                if profile_url:
                    if node.text():
                        card.setdefault("company", node.text()[:500])

                    card.setdefault(
                        "company_profile_url",
                        profile_url,
                    )
            icons = {part for child in node.children if isinstance(child, _Node) and child.tag == "i"
                     for part in (child.attrs.get("class") or "").split()}
            for icon, target in (("fa-map-marker-alt", "location"), ("fa-clock", "publication_date"),
                                 ("fa-industry", "sector"), ("fa-briefcase", "contract")):
                if icon in icons and node.text():
                    card.setdefault(target, node.text()[:500])
            text = node.text()
            for label, target in (("Lieu de travail", "location"), ("Localisation", "location"),
                                  ("Entreprise", "company"), ("Publiée le", "publication_date")):
                if _fold(text).startswith(_fold(label) + " :") or _fold(text).startswith(_fold(label) + ":"):
                    card.setdefault(target, text.split(":", 1)[1].strip()[:500])
        cards.append(card)
    return cards

def _node_links(node):
    # Only used to count distinct offers in a potential card container.
    from html import escape
    return "".join('<a href="' + escape(n.attrs.get("href") or "", quote=True) + '"></a>'
                   for n in node.walk() if n.tag == "a")

def next_listing_url(html, source_url):
    """Follow only an observed immediate-next link on the same public register."""
    source_url = public_url(source_url)
    current = int(dict(parse_qsl(urlsplit(source_url).query)).get("page", "1"))
    for node in _HTML(html).root.walk():
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        try:
            raw = urljoin(source_url, node.attrs["href"])
            query = parse_qsl(urlsplit(raw).query, keep_blank_values=True)
            if len(query) != 1 or query[0][0] != "page":
                continue
            target = public_url(raw)
        except ValueError:
            continue
        target_page = int(dict(parse_qsl(urlsplit(target).query)).get("page", "1"))
        if urlsplit(target).path == urlsplit(source_url).path and target_page == current + 1:
            return target
    return None

def listing_page(html, source_url):
    return {"cards": discover_cards(html, source_url, 20), "next": next_listing_url(html, source_url)}
