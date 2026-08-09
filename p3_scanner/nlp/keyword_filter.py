"""
Multilingual keyword pre-filter for mpox signal detection.
Covers: English, Nigerian Pidgin, Hausa, Yoruba, Igbo, French (Cameroon cross-border).

Returns:
  - matched_keywords: list of keywords found
  - detected_language: best-guess ISO code
  - is_candidate: True if any keyword matched
"""

import re
from typing import Tuple, List

# ── English ────────────────────────────────────────────────────
EN_MPOX = [
    r"\bmpox\b", r"\bmonkeypox\b", r"\bmonkey\s*pox\b",
    r"\borthopoxvirus\b", r"\bpoxvirus\b",
    r"\bclade\s*i\b", r"\bclade\s*ii\b", r"\bclade\s*1\b", r"\bclade\s*2\b",
    r"\bvesicular\s*rash\b", r"\bpustular\s*rash\b", r"\bpox\s*lesion",
    r"\bmpox\s*outbreak\b", r"\bmpox\s*case", r"\bmpox\s*alert",
]
EN_CONTEXT = [
    # These alone are too broad — only count when paired with disease context
    r"\bsmallpox\b", r"\bpustule", r"\bvesicle[s]?\b",
    r"\bNCDC\b", r"\bwho\s+mpox\b",
]

# ── Nigerian Pidgin ─────────────────────────────────────────────
PCM_MPOX = [
    r"\bmpox\b", r"\bmonkeypox\b",
    r"\bbody\s+dey\s+scratch\b", r"\bskin\s+sore\b",
    r"\bcuta\s+mpox\b",  # Hausa blend common in Pidgin
    r"\bdisease\s+wey\s+dey\s+spread\b",
    r"\bpox\s+disease\b",
]
PCM_MARKERS = [
    r"\bdey\b", r"\bwey\b", r"\bna\b", r"\bno\s+be\b",
    r"\bdem\b", r"\bpikin\b",
]

# ── Hausa ───────────────────────────────────────────────────────
HA_MPOX = [
    r"\bmpox\b", r"\bmonkeypox\b",
    r"\bcuta\s+mpox\b",             # mpox disease
    r"\bamosanin\s+fata\b",         # skin disease
    r"\bcuta\s+fata\b",             # skin disease
    r"\bfurutu\b",                  # pustules/rash
    r"\bcuta\s+barbashe\b",         # spotted fever-like
    r"\bukuntu\b",                  # monkey (hausa)
]
HA_MARKERS = [
    r"\bda\b", r"\bne\b", r"\bce\b", r"\bwa\b",
    r"\bbanza\b", r"\bkuma\b", r"\bsai\b",
]

# ── Yoruba ──────────────────────────────────────────────────────
YO_MPOX = [
    r"\bmpox\b", r"\bmonkeypox\b",
    r"\barun\s+mpox\b",             # mpox illness
    r"\barun\s+ape\b",              # monkey illness
    r"\bape\s+agbado\b",            # monkey + pox
    r"\birora\s+ara\b",             # skin sores
    r"\bwiwu\s+ara\b",              # swelling on body
    r"\bkokoro\s+arun\b",           # disease germ
]
YO_MARKERS = [
    r"\bni\b", r"\nje\b", r"\bnaa\b", r"\bati\b",
    r"\bsibe\b", r"\blaarin\b",
]

# ── Igbo ────────────────────────────────────────────────────────
IG_MPOX = [
    r"\bmpox\b", r"\bmonkeypox\b",
    r"\boria\s+mpox\b",             # mpox sickness
    r"\boria\s+nkume\b",            # pox-like illness
    r"\boria\b.*\bnkita\b",         # monkey illness pattern
    r"\bacha\s+oria\b",
    r"\bufufu\s+oria\b",            # skin disease
]
IG_MARKERS = [
    r"\bna\b", r"\bebe\b", r"\bosi\b", r"\baga\b",
    r"\bnke\b", r"\bgizie\b",
]

# ── French (cross-border: Cameroon, Benin, Niger) ───────────────
FR_MPOX = [
    r"\bmpox\b", r"\bmonkeypox\b",
    r"\bvariole\s+du\s+singe\b",    # official French term
    r"\borthopoxvirus\b",
    r"\bcas\s+de\s+mpox\b",         # mpox case
    r"\bfli[èe]vre\s+.*\s+l[eé]sion\b",
    r"\bl[eé]sion\s+cutan",
]

# ── Misinformation theme patterns ──────────────────────────────
MISINFO_THEMES = {
    "vaccine_blame": [
        r"\bmpox\s*(is|was|caused|from|due)\s*(by\s*)?(the\s*)?vaccine",
        r"\b(covid|corona)\s*vaccine\s*.{0,40}(monkeypox|mpox)",  # "covid vaccine... mpox"
        r"\b(monkeypox|mpox)\s*.{0,40}(covid|corona)\s*vaccine",
        r"\bvaccine\s*(is\s*)?(causing|caused|spread|spreading)\s*.{0,30}(mpox|pox)",
        r"\bvaccinated\s*.{0,20}got\s*(mpox|monkeypox)",
        r"\bjab\s*.{0,30}(mpox|pox)",
        r"\bvaccine\s*(shed|shedding)\s*.{0,30}(pox|mpox)",
        r"\bvaccine\s*(give[s]?|gave)\s*.{0,20}(mpox|pox)",
    ],
    "denial": [
        r"\bmpox\s+(is|are|was)\s+a?\s*(not\s+real|fake|hoax|scam|invented|fabricated)",
        r"\b(mpox|monkeypox)\s+is\s+a\s+(hoax|scam|lie|fake|fraud)",
        r"\bmpox\s+na\s+(fake|lie|scam|fraud)",      # Pidgin: "mpox na fake"
        r"\bmpox\s+no\s+(dey\s+real|exist|real)",    # Pidgin: "mpox no dey real"
        r"\b(fake|false|hoax|fabricated)\s+(mpox|monkeypox)",
        r"\bno\s+such\s+(thing|disease)\s+as\s+(mpox|monkeypox)",
        r"\b(mpox|monkeypox)\s+doesn.?t\s+exist",
        r"\bmpox\s+(is\s+)?(just\s+)?an?\s+agenda",
        r"\bplandemic\s*.{0,30}(mpox|pox)",
    ],
    "bioweapon": [
        r"\b(mpox|monkeypox)\s*.{0,50}(bio.?weapon|lab.?made|engineered|manufactured)",
        r"\b(bio.?weapon|lab.?leak|lab.?origin)\s*.{0,50}(mpox|monkeypox)",
        r"\b(mpox|monkeypox)\s*.{0,30}created\s*(by\s*)?(government|WHO|CDC|lab|pentagon|military|bill\s*gates?)",
        r"\bbill\s*gates?\s*.{0,50}(mpox|monkeypox)",
        r"\b(mpox|monkeypox)\s*.{0,30}(population\s+control|depopulation)",
        r"\b(mpox|monkeypox)\s*.{0,30}(great\s+reset|new\s+world\s+order)",
    ],
    "traditional_cure": [
        r"\b(herb|herbal|neem|garlic|turmeric|ginger|bitter\s+leaf)\s*.{0,20}(cure[sd]?|treat[s]?|heal[s]?|prevent[s]?)\s*.{0,20}(mpox|monkeypox|pox)",
        r"\b(mpox|monkeypox|pox)\s*.{0,50}(traditional\s+medicine|native\s+doctor|herbalist|spiritualist)",
        r"\b(mpox|monkeypox)\s*.{0,30}(no\s+need|don.?t\s+need)\s*.{0,20}hospital",
        r"\b(mpox|monkeypox)\s*.{0,30}cure\s*(it\s*)?at\s+home",
        r"\b(mpox|monkeypox)\s*.{0,30}(local\s+remedy|home\s+remedy)",
        r"\bprayer\s*.{0,20}(cure[sd]?|heal[s]?|treat[s]?)\s*.{0,20}(mpox|monkeypox)",
    ],
    "stigma_gay": [
        r"\b(mpox|monkeypox)\s+(is\s+a?\s*)?(only\s+)?(gay|homosexual|lgb|queer|sodomite|immoral)\s*(disease|people|men|community)",
        r"\b(mpox|monkeypox)\s*.{0,20}(only\s+)?(affects?|targets?|punishes?)\s*(gay|homosexual|lgb|queer)",
        r"\b(mpox|monkeypox)\s*.{0,40}(gay|homosexual|lgb|queer|homo)\s*(and|men|people|community|behaviour|behavior|lifestyle)",
        r"\bgay\s*(disease|plague|curse|punishment)\s*.{0,30}(mpox|monkeypox)",
        r"\b(punishment|wrath|judgment|curse)\s*.{0,30}(gay|homo|homosexual|lgb|queer|immoral|sin)\s*.{0,40}(mpox|monkeypox)",
        r"\b(mpox|monkeypox)\s*.{0,30}(punishment|wrath|judgment|curse)\s*.{0,30}(gay|homo|immoral|sin|homosexual)",
        r"\b(mpox|monkeypox)\s*.{0,30}(immoral|immorality|fornication|sin)\b",
        r"\b(homosexual|gay|immoral)\s*.{0,30}(spread|cause[sd]?|behind)\s*.{0,20}(mpox|monkeypox)",
    ],
    "exaggerated_transmission": [
        r"\b(mpox|monkeypox)\s*(spreads?\s*(through\s+)?(the\s+)?air\b|is\s+airborne|aerosol)",
        r"\b(mpox|monkeypox)\s*.{0,30}(more\s+contagious|deadlier)\s*.{0,20}(covid|ebola|measles|flu)",
        r"\b(mpox|monkeypox)\s*.{0,30}kills?\s+everyone",
        r"\b(mpox|monkeypox)\s*.{0,30}highly\s+contagious\s*.{0,20}(like|as)\s+(measles|flu|cold|covid)",
        r"\b(mpox|monkeypox)\s*.{0,30}surface[s]?\s*.{0,20}enough\s+to\s+(get|catch|spread|infect)",
        r"\bjust\s+(touching|breathing)\s*.{0,30}(mpox|monkeypox)",
    ],
    "false_treatment": [
        r"\bchloroquine\s*.{0,30}(mpox|monkeypox)",
        r"\bivermectin\s*.{0,50}(mpox|monkeypox)",
        r"\b(mpox|monkeypox)\s*.{0,50}ivermectin",
        r"\bbleach\s*.{0,30}(mpox|monkeypox|pox)",
        r"\b(mpox|monkeypox)\s*.{0,30}hydroxychloroquine",
        r"\b(mpox|monkeypox)\s*.{0,30}miracle\s+(cure|drug|treatment|remedy)",
    ],
    "conspiracy_who": [
        r"\b(who|world\s+health\s+org)\s*.{0,40}(mpox|monkeypox)\s*.{0,30}(control|agenda|profit)",
        r"\b(mpox|monkeypox)\s*.{0,30}(great\s+reset|new\s+world\s+order|agenda\s+2030)",
        r"\b(mpox|monkeypox)\s*.{0,30}profit\s*.{0,20}(pharmaceutical|pharma|big\s+pharma)",
        r"\b(mpox|monkeypox)\s*.{0,30}bill\s+gates?\b",
        r"\bpharma\s*.{0,30}(mpox|monkeypox)\s*.{0,30}(conspiracy|plan|scheme)",
    ],
}

# ── Nigerian state name mentions ────────────────────────────────
NIGERIA_STATES = {
    "abia": 1, "adamawa": 2, "akwa ibom": 3, "anambra": 4, "bauchi": 5,
    "bayelsa": 6, "benue": 7, "borno": 8, "cross river": 9, "delta": 10,
    "ebonyi": 11, "edo": 12, "ekiti": 13, "enugu": 14, "fct": 37,
    "abuja": 37, "gombe": 15, "imo": 16, "jigawa": 17, "kaduna": 18,
    "kano": 19, "katsina": 20, "kebbi": 21, "kogi": 22, "kwara": 23,
    "lagos": 24, "nasarawa": 25, "niger": 26, "ogun": 27, "ondo": 28,
    "osun": 29, "oyo": 30, "plateau": 31, "rivers": 32, "sokoto": 33,
    "taraba": 34, "yobe": 35, "zamfara": 36,
}

# Compile everything once
_compiled = {
    "en":  [re.compile(p, re.IGNORECASE) for p in EN_MPOX + EN_CONTEXT],
    "en_strong": [re.compile(p, re.IGNORECASE) for p in EN_MPOX],
    "pcm": [re.compile(p, re.IGNORECASE) for p in PCM_MPOX],
    "pcm_markers": [re.compile(p, re.IGNORECASE) for p in PCM_MARKERS],
    "ha":  [re.compile(p, re.IGNORECASE) for p in HA_MPOX],
    "ha_markers": [re.compile(p, re.IGNORECASE) for p in HA_MARKERS],
    "yo":  [re.compile(p, re.IGNORECASE) for p in YO_MPOX],
    "yo_markers": [re.compile(p, re.IGNORECASE) for p in YO_MARKERS],
    "ig":  [re.compile(p, re.IGNORECASE) for p in IG_MPOX],
    "ig_markers": [re.compile(p, re.IGNORECASE) for p in IG_MARKERS],
    "fr":  [re.compile(p, re.IGNORECASE) for p in FR_MPOX],
    "misinfo": {k: [re.compile(p, re.IGNORECASE) for p in pats]
                for k, pats in MISINFO_THEMES.items()},
    "states": {k: re.compile(r'\b' + re.escape(k) + r'\b', re.IGNORECASE)
               for k in NIGERIA_STATES},
}


def detect_language(text: str) -> str:
    """Heuristic language detection based on marker words."""
    t = text.lower()
    scores = {"en": 0, "pcm": 0, "ha": 0, "yo": 0, "ig": 0, "fr": 0}

    # French strong markers
    if any(p in t for p in ["variole du singe", "de la", "les", "est", "pour"]):
        scores["fr"] += 2

    # Pidgin markers
    pcm_hits = sum(1 for p in _compiled["pcm_markers"] if p.search(text))
    scores["pcm"] += pcm_hits

    # Hausa markers
    ha_hits = sum(1 for p in _compiled["ha_markers"] if p.search(text))
    scores["ha"] += ha_hits

    # Yoruba markers
    yo_hits = sum(1 for p in _compiled["yo_markers"] if p.search(text))
    scores["yo"] += yo_hits

    # Igbo markers
    ig_hits = sum(1 for p in _compiled["ig_markers"] if p.search(text))
    scores["ig"] += ig_hits

    # English is default if no other markers dominate
    scores["en"] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > scores["en"] or best == "en" else "en"


def detect_mpox_keywords(text: str) -> Tuple[bool, List[str], str]:
    """
    Run multilingual keyword pre-filter.

    Returns:
      (is_candidate, matched_keywords, detected_language)
    """
    if not text:
        return False, [], "unknown"

    lang = detect_language(text)
    matched = []

    # Always run English strong patterns
    for pat in _compiled["en_strong"]:
        m = pat.search(text)
        if m:
            matched.append(m.group(0).lower().strip())

    # Run language-specific patterns
    lang_key = lang if lang in _compiled else "en"
    if lang_key in ("pcm", "ha", "yo", "ig", "fr"):
        for pat in _compiled[lang_key]:
            m = pat.search(text)
            if m:
                matched.append(m.group(0).lower().strip())

    # Deduplicate
    matched = list(dict.fromkeys(matched))
    return len(matched) > 0, matched, lang


def detect_misinformation(text: str) -> List[str]:
    """Return list of misinformation theme keys found in text."""
    flags = []
    for theme, pats in _compiled["misinfo"].items():
        for pat in pats:
            if pat.search(text):
                flags.append(theme)
                break
    return flags


def detect_geo_mentions(text: str) -> Tuple[List[str], int | None]:
    """
    Return (list_of_state_names_mentioned, primary_state_id).
    primary_state_id is the first/only state if unambiguous, else None.
    """
    found = {}
    for state_name, state_id in NIGERIA_STATES.items():
        if _compiled["states"][state_name].search(text):
            found[state_name] = state_id

    names = list(found.keys())
    # Resolve "Niger" vs "Niger state" — if only Niger found and no 'niger state', it might be country
    if "niger" in found and len(found) == 1:
        # ambiguous: could be Nigeria's Niger state or Republic of Niger
        if not re.search(r'\bniger\s+state\b', text, re.IGNORECASE):
            names = []
            found = {}

    primary = list(found.values())[0] if len(found) == 1 else None
    return names, primary


def analyse(title: str, body: str, forced_lang: str = None) -> dict:
    """Full analysis of a single article/post. Returns dict for DB insertion."""
    text = f"{title}\n\n{body}"
    is_candidate, keywords, lang = detect_mpox_keywords(text)
    # Override auto-detected language when feed declares it explicitly
    if forced_lang:
        lang = forced_lang
    misinfo = detect_misinformation(text) if is_candidate else []
    geo_names, state_id = detect_geo_mentions(text)

    # Simple sentiment heuristic
    neg_words = re.compile(
        r'\b(death|dead|died|kill|surge|spike|alarming|outbreak|emergency|crisis|fatal|danger)\b',
        re.IGNORECASE)
    pos_words = re.compile(
        r'\b(recover|contain|success|declin|drop|fell|low|safe|prevent|vaccin)\b',
        re.IGNORECASE)
    neg_count = len(neg_words.findall(text))
    pos_count = len(pos_words.findall(text))
    if neg_count > pos_count + 1:
        sentiment = "negative"
    elif pos_count > neg_count + 1:
        sentiment = "positive"
    elif is_candidate:
        sentiment = "neutral"
    else:
        sentiment = "unknown"

    return {
        "detected_language":    lang,
        "is_mpox_relevant":     is_candidate,
        "relevance_score":      min(1.0, len(keywords) * 0.25) if is_candidate else 0.0,
        "keyword_matched":      keywords,
        "misinformation_flags": misinfo,
        "geo_mentions":         geo_names,
        "state_id":             state_id,
        "sentiment":            sentiment,
    }


if __name__ == "__main__":
    # Quick self-test
    samples = [
        ("Mpox cases surge in Lagos, NCDC confirms clade I",
         "The Nigeria Centre for Disease Control confirmed 12 new mpox cases in Lagos state this week."),
        ("Health warning issued", "Authorities urge residents in Kano and Borno to report any skin lesions."),
        ("Mpox na fake disease", "Dem say mpox no dey real, na government agenda wey dey deceive people."),
        ("Variole du singe au Cameroun", "Le ministère de la santé a confirmé des cas de variole du singe."),
        ("Vaccines cause monkeypox spread", "The covid vaccine is causing people to get monkeypox everywhere."),
    ]
    for title, body in samples:
        result = analyse(title, body)
        print(f"\nTitle: {title[:60]}")
        print(f"  lang={result['detected_language']} relevant={result['is_mpox_relevant']} "
              f"score={result['relevance_score']:.2f} "
              f"keywords={result['keyword_matched']} "
              f"misinfo={result['misinformation_flags']} "
              f"geo={result['geo_mentions']}")
