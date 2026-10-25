"""Lightweight personal-detail detectors for the Docs tab.

No neural model. Emails, phone numbers, and money are regular expressions.
Names, street addresses, and locations are short, explicit heuristics plus a
fixed word list. They miss a lot and sometimes cover an ordinary word. Callers
black out the spans; this module does not draw.

Limits worth keeping honest:

- Emails need an @ sign. "name at domain dot com" is ignored.
- Phone numbers need separators (or a leading +). A bare digit string is left
  alone so account numbers are not treated as phones.
- Money needs a currency symbol or code. A bare decimal is ignored.
- Addresses are US-style street lines, PO Boxes, and "City, ST ZIP" lines.
  A street with no suffix, and most non-US layouts, are missed. Each line is
  judged on its own.
- Names are a title ("Dr. Jane Smith"), a label ("Name: Ada Lovelace"), or a
  common given name plus a capitalized surname. Unusual, single, and lowercase
  names are missed. "Dr. Pepper" is covered on purpose of the title rule.
- Locations are a fixed list of countries, US states, and major cities, plus
  postal lines. Small towns are missed. Two-letter abbreviations are covered
  only beside a city or ZIP, so the word "or" stays. A few country names that
  are also personal names (Jordan, Chad, Israel, Turkey) are omitted.
"""
from __future__ import annotations

import re

PRESET_ORDER = ("names", "money", "addresses", "emails", "phones", "locations")

PRESET_LABELS = {
    "names": "Names",
    "money": "Money",
    "addresses": "Addresses",
    "emails": "Emails",
    "phones": "Phone numbers",
    "locations": "Locations",
}

# One line each, for the API. The Docs screen and the README say the same thing in prose.
PRESET_NOTES = {
    "emails": "Needs an @ sign. Spaces around @ are tolerated. 'name at domain dot com' is missed.",
    "phones": "US numbers with separators, and numbers that start with +. A bare digit string is not a phone.",
    "money": "Needs $ € £ ¥ or a currency code. A bare decimal is ignored. 1.234,56 € is often missed.",
    "addresses": "US-style street lines, PO Boxes, and City, ST ZIP. No street suffix, and most non-US layouts, are missed.",
    "names": "Titles, Name: labels, or a common given name plus a capitalized surname. Unusual and lowercase names are missed.",
    "locations": "A fixed list of countries, states, and major cities, plus City, ST ZIP. Small towns are missed.",
}

_ALIASES = {
    "name": "names",
    "names": "names",
    "money": "money",
    "address": "addresses",
    "addresses": "addresses",
    "email": "emails",
    "emails": "emails",
    "phone": "phones",
    "phones": "phones",
    "phonenumber": "phones",
    "phonenumbers": "phones",
    "telephone": "phones",
    "tel": "phones",
    "location": "locations",
    "locations": "locations",
    "place": "locations",
    "places": "locations",
    "city": "locations",
    "cities": "locations",
}


def parse_presets(value) -> list[str]:
    """Comma-separated ids, or a list of them. Unknown names are an error. Order is fixed."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        chunks = []
        for item in value:
            chunks.extend(re.split(r"[,;]+", str(item)))
    else:
        chunks = re.split(r"[,;]+", str(value))
    seen: set[str] = set()
    for chunk in chunks:
        key = re.sub(r"[\s_\-]+", "", chunk.strip().casefold())
        if not key:
            continue
        canon = _ALIASES.get(key)
        if not canon:
            raise ValueError(
                f'Unknown preset "{chunk.strip()}". '
                "Use names, money, addresses, emails, phones, or locations."
            )
        seen.add(canon)
    return [p for p in PRESET_ORDER if p in seen]


# --- emails -----------------------------------------------------------------

EMAIL_RE = re.compile(
    r"(?<![\w.+-])"
    r"[A-Za-z0-9](?:[A-Za-z0-9_%+\-]|[ \t]*\.[ \t]*[A-Za-z0-9])*"
    r"[ \t]*@[ \t]*"
    r"[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?"
    r"(?:[ \t]*\.[ \t]*[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*"
    r"[ \t]*\.[ \t]*[A-Za-z]{2,24}"
    r"(?!\w)"
)


# --- phones -----------------------------------------------------------------

# International form first so "+1 415 555 2671" is one match, not two.
PHONE_RE = re.compile(
    r"(?<!\w)(?:"
    r"\+(?:[ \t.\-]*\(?\d{1,4}\)?){2,6}"
    r"|(?:\+?1[ \t.\-]?)?(?:\(\s*[2-9]\d{2}\s*\)[ \t.\-]?|[2-9]\d{2}[ \t.\-])[2-9]\d{2}[ \t.\-]\d{4}"
    r")(?!\d)"
)


def _phone_spans(text: str) -> list[tuple[int, int]]:
    out = []
    for m in PHONE_RE.finditer(text):
        raw = m.group()
        digits = re.sub(r"\D", "", raw)
        if raw.lstrip().startswith("+"):
            if not 8 <= len(digits) <= 15:
                continue
        elif len(digits) == 11:
            if not digits.startswith("1"):
                continue
        elif len(digits) != 10:
            continue
        out.append((m.start(), m.end()))
    return out


# --- money ------------------------------------------------------------------

_CCY = r"USD|US\$|EUR|GBP|CAD|AUD|JPY|CHF|CNY|INR"
MONEY_RE = re.compile(
    rf"(?i)(?<!\w)(?:"
    rf"(?:{_CCY})[ \t]*-?\$?[ \t]*\d{{1,3}}(?:,\d{{3}})+(?:\.\d{{1,2}})?(?!\d)"
    rf"|(?:{_CCY})[ \t]*-?\$?[ \t]*\d{{1,7}}(?:\.\d{{1,2}})?(?!\d)"
    rf"|[\$€£¥][ \t]*-?\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?(?!\d)"
    rf"|\([ \t]*-?\$[ \t]*\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{2}})[ \t]*\)"
    rf"|-?\d{{1,3}}(?:,\d{{3}})+(?:\.\d{{2}})?[ \t]*(?:USD|EUR|GBP|CAD|AUD|dollars|euros|pounds)\b"
    rf"|-?\d{{1,6}}(?:\.\d{{2}})?[ \t]*(?:dollars|euros|pounds)\b"
    rf"|-?\d{{1,7}}\.\d{{2}}[ \t]*(?:USD|EUR|GBP|CAD|AUD)\b"
    rf")"
)


# --- addresses --------------------------------------------------------------

_STREET_SUFFIX = (
    r"street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|place|pl|"
    r"terrace|ter|way|circle|cir|highway|hwy|parkway|pkwy|trail|trl|square|sq"
)

STREET_RE = re.compile(
    rf"(?i)(?<!\w)\d{{1,6}}[ \t]+"
    rf"(?:(?:north|south|east|west|northeast|northwest|southeast|southwest|n|s|e|w|ne|nw|se|sw)\.?[ \t]+)?"
    rf"(?:[A-Za-z0-9][A-Za-z0-9'.\-]*[ \t]+){{1,4}}"
    rf"(?:{_STREET_SUFFIX})\.?"
    rf"(?:[ \t]*,?[ \t]*(?:apt|apartment|suite|ste|unit|#)[ \t]*\.?[ \t]*[A-Za-z0-9\-]+)?"
    rf"(?!\w)"
)

POBOX_RE = re.compile(r"(?i)(?<!\w)P\.?[ \t]*O\.?[ \t]*Box[ \t]+#?[ \t]*\d{1,6}(?!\d)")

_STATE_ABBR = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
    "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
    "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
)
_ABBR_SET = set(_STATE_ABBR)
_ABBR_ALT = "|".join(_STATE_ABBR)

# ZIP makes a lowercase "il" safe. Without a ZIP, bare two-letter abbreviations are not matched.
POSTAL_RE = re.compile(
    rf"(?<!\w)[A-Za-z][A-Za-z.'’\-]{{1,}}(?:[ \t]+[A-Za-z][A-Za-z.'’\-]+){{0,3}}"
    rf",[ \t]+(?:{_ABBR_ALT}|{_ABBR_ALT.lower()})[ \t]+\d{{5}}(?:-\d{{4}})?(?!\d)"
)


# --- locations --------------------------------------------------------------

# Full names only. Two-letter abbreviations are added when they sit next to a city or a ZIP.
_STATES = (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
    "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico",
    "New York", "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania",
    "Rhode Island", "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont",
    "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
    "Ontario", "Quebec", "British Columbia", "Alberta", "Manitoba", "Saskatchewan", "Nova Scotia",
    "New Brunswick", "Newfoundland and Labrador", "Prince Edward Island",
)

# Country names that are also common personal names are omitted on purpose.
_COUNTRIES = (
    "Afghanistan", "Albania", "Algeria", "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan",
    "Bangladesh", "Belarus", "Belgium", "Bolivia", "Brazil", "Bulgaria", "Cambodia", "Cameroon",
    "Canada", "Chile", "China", "Colombia", "Costa Rica", "Croatia", "Cuba", "Czech Republic", "Czechia",
    "Denmark", "Dominican Republic", "Ecuador", "Egypt", "England", "Estonia", "Ethiopia", "Finland",
    "France", "Germany", "Ghana", "Greece", "Guatemala", "Haiti", "Honduras", "Hungary", "Iceland",
    "India", "Indonesia", "Iran", "Iraq", "Ireland", "Italy", "Jamaica", "Japan", "Kazakhstan", "Kenya",
    "Kuwait", "Latvia", "Lebanon", "Lithuania", "Luxembourg", "Malaysia", "Mexico", "Morocco", "Nepal",
    "Netherlands", "New Zealand", "Nicaragua", "Nigeria", "North Korea", "Norway", "Pakistan", "Panama",
    "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia", "Saudi Arabia",
    "Scotland", "Senegal", "Serbia", "Singapore", "Slovakia", "Slovenia", "South Africa", "South Korea",
    "Spain", "Sri Lanka", "Sweden", "Switzerland", "Syria", "Taiwan", "Tanzania", "Thailand", "Tunisia",
    "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom", "United States", "Uruguay",
    "Venezuela", "Vietnam", "Wales", "Yemen", "Zambia", "Zimbabwe",
)

_CITIES = (
    "New York", "New York City", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia",
    "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville", "Fort Worth",
    "Columbus", "Charlotte", "Indianapolis", "San Francisco", "Seattle", "Denver", "Washington",
    "Boston", "Nashville", "Detroit", "Portland", "Las Vegas", "Baltimore", "Milwaukee",
    "Albuquerque", "Tucson", "Fresno", "Sacramento", "Atlanta", "Miami", "Minneapolis",
    "Kansas City", "Colorado Springs", "Raleigh", "Omaha", "Oakland", "Tampa", "New Orleans",
    "Cleveland", "Pittsburgh", "Cincinnati", "St. Louis", "Saint Louis", "Orlando", "Honolulu",
    "Anchorage", "London", "Manchester", "Birmingham", "Glasgow", "Edinburgh", "Dublin", "Paris",
    "Lyon", "Marseille", "Berlin", "Munich", "Hamburg", "Frankfurt", "Madrid", "Barcelona", "Rome",
    "Milan", "Amsterdam", "Rotterdam", "Brussels", "Vienna", "Prague", "Warsaw", "Budapest",
    "Athens", "Lisbon", "Stockholm", "Oslo", "Copenhagen", "Helsinki", "Zurich", "Geneva",
    "Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary", "Sydney", "Melbourne", "Brisbane",
    "Perth", "Auckland", "Wellington", "Tokyo", "Osaka", "Kyoto", "Seoul", "Beijing", "Shanghai",
    "Hong Kong", "Singapore", "Bangkok", "Mumbai", "Delhi", "Bengaluru", "Bangalore", "Dubai",
    "Abu Dhabi", "Cairo", "Johannesburg", "Cape Town", "Nairobi", "Lagos", "Mexico City",
    "Sao Paulo", "São Paulo", "Buenos Aires", "Santiago", "Lima", "Bogota", "Bogotá",
    "Rio de Janeiro", "Washington, D.C.", "Washington, DC",
)

_GAZETTEER = tuple(dict.fromkeys((*_STATES, *_COUNTRIES, *_CITIES)))
_PLACE_PHRASES = {p.casefold() for p in _GAZETTEER}

def _compile_places() -> re.Pattern:
    parts = sorted(_GAZETTEER, key=len, reverse=True)
    body = "|".join(re.escape(p) for p in parts)
    return re.compile(rf"(?i)(?<!\w)(?:{body})(?!\w)")


_PLACE_RE = _compile_places()

_SMALL_WORDS = {"and", "of", "the", "de", "da", "del", "la", "le", "von", "van"}


def _looks_named(s: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÿ'’\-]+", s)
    if not words or not words[0][:1].isupper():
        return False
    for i, w in enumerate(words):
        if w[:1].isupper():
            continue
        if i > 0 and w.casefold() in _SMALL_WORDS:
            continue
        return False
    return True


def _location_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for m in _PLACE_RE.finditer(text):
        if not _looks_named(m.group()):
            continue
        end = m.end()
        tail = re.match(rf",[ \t]+({_ABBR_ALT})\b", text[end:])
        if tail and tail.group(1).upper() in _ABBR_SET:
            end += tail.end()
        spans.append((m.start(), end))
    return spans


# --- names ------------------------------------------------------------------

# Common given names, not a census. Months that are also names stay in the list;
# "May 12" still fails because the next token is not a surname.
_GIVEN = {n.casefold() for n in (
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
    "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen",
    "Christopher", "Lisa", "Daniel", "Nancy", "Matthew", "Betty", "Anthony", "Margaret", "Mark", "Sandra",
    "Donald", "Ashley", "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kenneth", "Dorothy", "Kevin", "Carol", "Brian", "Amanda", "George", "Melissa", "Timothy", "Deborah",
    "Ronald", "Stephanie", "Edward", "Rebecca", "Jason", "Sharon", "Jeffrey", "Laura", "Ryan", "Cynthia",
    "Jacob", "Kathleen", "Gary", "Amy", "Nicholas", "Angela", "Eric", "Shirley", "Jonathan", "Anna",
    "Stephen", "Brenda", "Larry", "Pamela", "Justin", "Emma", "Scott", "Nicole", "Brandon", "Helen",
    "Benjamin", "Samantha", "Samuel", "Katherine", "Raymond", "Christine", "Gregory", "Debra", "Frank",
    "Rachel", "Alexander", "Carolyn", "Patrick", "Janet", "Jack", "Catherine", "Dennis", "Maria", "Jerry",
    "Heather", "Tyler", "Diane", "Aaron", "Ruth", "Jose", "Julie", "Adam", "Olivia", "Nathan", "Joyce",
    "Henry", "Virginia", "Douglas", "Victoria", "Zachary", "Kelly", "Peter", "Lauren", "Kyle", "Christina",
    "Noah", "Joan", "Ethan", "Evelyn", "Jeremy", "Judith", "Walter", "Megan", "Christian", "Cheryl",
    "Keith", "Andrea", "Roger", "Hannah", "Terry", "Jacqueline", "Austin", "Martha", "Sean", "Gloria",
    "Gerald", "Teresa", "Carl", "Ann", "Harold", "Sara", "Dylan", "Madison", "Arthur", "Frances",
    "Lawrence", "Kathryn", "Jordan", "Janice", "Jesse", "Jean", "Bryan", "Abigail", "Billy", "Alice",
    "Joe", "Julia", "Bruce", "Judy", "Gabriel", "Sophia", "Logan", "Grace", "Alan", "Denise", "Juan",
    "Amber", "Wayne", "Doris", "Ralph", "Marilyn", "Roy", "Danielle", "Eugene", "Beverly", "Louis",
    "Isabella", "Philip", "Theresa", "Bobby", "Diana", "Johnny", "Natalie", "Bradley", "Brittany",
    "Howard", "Charlotte", "Albert", "Marie", "Harry", "Kayla", "Fred", "Alexis", "Martin", "Lori",
    "Carlos", "Ada", "Jane", "Will", "Bill", "Bob", "Jim", "Jimmy", "Tom", "Dan", "Ben", "Sam", "Alex",
    "Anna", "Anne", "Claire", "Clara", "Lucy", "Chloe", "Maya", "Mia", "Ella", "Leah", "Zoe", "Iris",
    "Ruby", "Hazel", "Nora", "Rose", "Eve", "June", "April", "May", "August", "Priya", "Ahmed",
    "Mohammed", "Muhammad", "Luis", "Ana", "Sofia", "Elena", "Olga", "Ivan", "Wei", "Minh", "Chen",
    "Rosa", "Carmen", "Miguel", "Andre", "André", "Marie", "Pierre", "Hans", "Greta", "Ingrid",
)}

_PARTICLES = {"da", "de", "del", "della", "di", "du", "la", "le", "van", "von", "der", "den", "ten", "ter"}

_SURNAME_STOP = {
    "street", "st", "avenue", "ave", "road", "rd", "boulevard", "blvd", "lane", "ln", "drive", "dr",
    "court", "ct", "place", "pl", "terrace", "ter", "way", "circle", "cir", "highway", "hwy",
    "parkway", "pkwy", "trail", "trl", "square", "sq", "north", "south", "east", "west",
    "beach", "city", "county", "heights", "springs", "valley", "island", "hills", "river", "lake",
    "falls", "grove", "harbor", "harbour", "point", "ridge", "view", "bay", "creek", "woods",
    "gardens", "garden", "plaza", "center", "centre", "station", "airport", "university", "college",
    "school", "hospital", "clinic", "department", "dept", "company", "inc", "llc", "ltd", "corp",
    "corporation", "report", "reports", "page", "total", "amount", "number", "date", "invoice",
    "account", "balance", "phone", "email", "address", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "january", "february", "march", "september", "october",
    "november", "december", "the", "and", "for", "with", "from", "this", "that", "your", "our",
    "dear", "hello", "thanks", "thank", "please", "see", "note", "notes", "attached", "above",
    "below", "following", "due", "paid", "box", "reference", "office", "today", "soon", "week",
    "next", "then", "meet", "call", "ship", "also", "pending", "after", "before", "lunch", "here",
    "there", "regards", "sincerely", "subject", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "at", "by", "or", "an", "as", "it", "if", "we", "he", "she",
}

# Same list the surname check uses. Months and "Will" are left out so "Dr. May Smith" still counts.
_TITLE_STOP = _SURNAME_STOP

_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*\.?")

_CAP_WORD = r"[A-Z][A-Za-z'’\-]*"
HONORIFIC_RE = re.compile(
    rf"(?i:(?<!\w)(?:mr|mrs|ms|mx|miss|dr|prof|sir|dame|madam)\.?)"
    rf"[ \t]+(?:{_CAP_WORD})"
    rf"(?:[ \t]+[A-Z]\.?)?"
    rf"(?:[ \t]+(?:{_CAP_WORD})){{0,2}}"
)
HONORIFIC_LO_RE = re.compile(
    r"(?i)(?<!\w)(?:mr|mrs|ms|mx|miss|dr|prof|sir|dame|madam)\.?"
    r"[ \t]+[a-z][a-z'’\-]+(?:[ \t]+[a-z][a-z'’\-]+){0,2}"
)
LABEL_RE = re.compile(
    r"(?i)(?<!\w)(?:full[ \t]+name|patient(?:[ \t]+name)?|employee|customer|client|contact|"
    r"attn|attention|signed(?:[ \t]+by)?|prepared[ \t]+by|requested[ \t]+by|name)"
    r"[ \t]*[:\-][ \t]*"
)


def _core(tok: str) -> str:
    if len(tok) > 2 and tok.endswith("."):
        return tok[:-1]
    return tok


def _gap_ok(text: str, end: int, start: int) -> bool:
    if start < end:
        return False
    gap = text[end:start]
    return bool(gap) and gap.strip() == "" and "\n" not in gap and "\r" not in gap


def _is_surname(tok: str) -> bool:
    if tok.casefold() in _SURNAME_STOP or tok.casefold() in _PARTICLES:
        return False
    if len(tok) < 2 or not tok[:1].isupper():
        return False
    return re.fullmatch(r"[A-Za-z][A-Za-z'’\-]+", tok) is not None


def _title_name_ok(span: str) -> bool:
    words = span.split()
    if len(words) < 2:
        return False
    return _core(words[1]).casefold() not in _TITLE_STOP


def _given_spans(text: str) -> list[tuple[int, int]]:
    tokens = list(_WORD.finditer(text))
    spans = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        core = _core(tok.group())
        if core.casefold() not in _GIVEN or not core[:1].isupper():
            i += 1
            continue
        j = i + 1
        if j < len(tokens) and _gap_ok(text, tok.end(), tokens[j].start()) and re.fullmatch(r"[A-Z]\.?", tokens[j].group()):
            j += 1
        if j >= len(tokens) or not _gap_ok(text, tokens[j - 1].end(), tokens[j].start()):
            i += 1
            continue
        while j < len(tokens) and _gap_ok(text, tokens[j - 1].end(), tokens[j].start()):
            piece = _core(tokens[j].group())
            if piece.casefold() in _PARTICLES and piece[:1].islower():
                j += 1
                continue
            break
        if j >= len(tokens) or not _gap_ok(text, tokens[j - 1].end(), tokens[j].start()):
            i += 1
            continue
        sur = _core(tokens[j].group())
        if not _is_surname(sur):
            i += 1
            continue
        phrase = text[tok.start():tokens[j].end()]
        words = re.findall(r"[A-Za-z'’\-]+", phrase)
        if " ".join(words).casefold() in _PLACE_PHRASES:
            i += 1
            continue
        spans.append((tok.start(), tokens[j].end()))
        i = j + 1
    return spans


def _cut_lower_honorific(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Keep the title and following lowercase name words. Stop at a stopword so the rest of the sentence stays."""
    rel = list(_WORD.finditer(text[start:end]))
    if len(rel) < 2:
        return None
    keep = 1
    for i, w in enumerate(rel):
        if i == 0:
            continue
        core = _core(w.group())
        if core.casefold() in _TITLE_STOP or core.casefold() in _SURNAME_STOP:
            break
        keep = i + 1
    if keep < 2:
        return None
    return start, start + rel[keep - 1].end()


def _labeled_spans(text: str) -> list[tuple[int, int]]:
    tokens = list(_WORD.finditer(text))
    spans = []
    for m in LABEL_RE.finditer(text):
        idx = None
        for i, tok in enumerate(tokens):
            if tok.start() < m.end():
                continue
            gap = text[m.end():tok.start()]
            if gap.strip() == "" and "\n" not in gap and "\r" not in gap:
                idx = i
            break
        if idx is None:
            continue
        first = _core(tokens[idx].group())
        if first.casefold() in _TITLE_STOP:
            continue
        titled = first[:1].isupper()
        known = first.casefold() in _GIVEN
        if not titled and not known:
            continue
        j = idx
        # One token is enough when it is a known given name or a title-case word of 3+ letters.
        last = idx
        taken = 0
        while j < len(tokens) and taken < 4:
            if j > idx and not _gap_ok(text, tokens[j - 1].end(), tokens[j].start()):
                break
            core = _core(tokens[j].group())
            if j > idx and core.casefold() in _PARTICLES and core[:1].islower():
                last = j
                taken += 1
                j += 1
                continue
            if j > idx and (core.casefold() in _SURNAME_STOP or core.casefold() in _TITLE_STOP):
                break
            if j > idx and not core[:1].isupper() and not (known and core[:1].islower()):
                break
            if j == idx and len(tokens) == idx + 1 and not (known or (titled and len(first) >= 3)):
                break
            last = j
            taken += 1
            j += 1
        if taken == 1 and not (known or (titled and len(first) >= 3)):
            continue
        if taken >= 1 and (taken >= 2 or known or (titled and len(first) >= 3)):
            spans.append((m.start(), tokens[last].end()))
    return spans


def _name_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for m in HONORIFIC_RE.finditer(text):
        if _title_name_ok(m.group()):
            spans.append((m.start(), m.end()))
    for m in HONORIFIC_LO_RE.finditer(text):
        cut = _cut_lower_honorific(text, m.start(), m.end())
        if cut and _title_name_ok(text[cut[0]:cut[1]]):
            spans.append(cut)
    spans.extend(_labeled_spans(text))
    spans.extend(_given_spans(text))
    return spans


# --- public -----------------------------------------------------------------

def _inside(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start >= a and end <= b for a, b in ranges)


def _merge(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    by: dict[str, list[tuple[int, int]]] = {}
    for start, end, kind in spans:
        if end <= start:
            continue
        by.setdefault(kind, []).append((start, end))
    out = []
    for kind, items in by.items():
        items.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        kept: list[tuple[int, int]] = []
        for start, end in items:
            if kept and start < kept[-1][1]:
                if end > kept[-1][1]:
                    kept[-1] = (kept[-1][0], end)
                continue
            kept.append((start, end))
        out.extend((s, e, kind) for s, e in kept)
    return out


def find_preset_spans(text: str, presets) -> list[tuple[int, int, str]]:
    """Character spans (start, end, preset id) for the selected presets. Overlaps of the same kind are merged."""
    chosen = set(parse_presets(presets))
    if not text or not chosen:
        return []
    email_ranges = [(m.start(), m.end()) for m in EMAIL_RE.finditer(text)]
    raw: list[tuple[int, int, str]] = []
    if "emails" in chosen:
        raw.extend((s, e, "emails") for s, e in email_ranges)
    if "phones" in chosen:
        raw.extend((s, e, "phones") for s, e in _phone_spans(text))
    if "money" in chosen:
        raw.extend((m.start(), m.end(), "money") for m in MONEY_RE.finditer(text))
    if "addresses" in chosen or "locations" in chosen:
        for m in POSTAL_RE.finditer(text):
            if "addresses" in chosen:
                raw.append((m.start(), m.end(), "addresses"))
            if "locations" in chosen:
                raw.append((m.start(), m.end(), "locations"))
    if "addresses" in chosen:
        for rx in (STREET_RE, POBOX_RE):
            for m in rx.finditer(text):
                if not _inside(email_ranges, m.start(), m.end()):
                    raw.append((m.start(), m.end(), "addresses"))
    if "locations" in chosen:
        for s, e in _location_spans(text):
            if not _inside(email_ranges, s, e):
                raw.append((s, e, "locations"))
    if "names" in chosen:
        for s, e in _name_spans(text):
            if not _inside(email_ranges, s, e):
                raw.append((s, e, "names"))
    return _merge(raw)
