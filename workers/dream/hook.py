"""Hook title for a Dream Short: a big two-colour title card burned in during the first ~1.8 s of the Finish.

Up to 3 lines of white outlined text with ONE emphasised word (larger, red by default), centred in the upper-middle
third with safe margins for the phone UI, fading in and out. Drawn with libass (an .ass file), like the captions.
The text is plain: ``THE CITY *FORGOT* HOW TO STAND`` (the word between asterisks is the emphasised one).
Pure helpers (parse_hook, hook_lines, hook_ass) are unit-tested. dream/finish.py burns the .ass files in with filter_for.
"""

from __future__ import annotations

import re

HOOK_SECONDS = 1.8
FADE_IN_MS, FADE_OUT_MS = 250, 350
MAX_LINES = 3
MAX_WORDS = 6
SIDE_MARGIN = 0.10      # fraction of the width kept free on each side
CENTRE_Y = 0.30         # title centre, as a fraction of the height: upper-middle, clear of the top and caption zones
NORMAL_PCT, BIG_PCT = 0.052, 0.092   # font size as a fraction of the height (1920 high: 100 px / 177 px)
CHAR_WIDTH = 0.60       # average bold glyph width as a fraction of the font size (a safe over-estimate)
COLOURS = {"red": "FF2020", "yellow": "FFD400", "orange": "FF8A00", "pink": "FF4FA3", "cyan": "20E0FF", "white": "FFFFFF"}


def parse_hook(text: str) -> list[tuple[str, bool]]:
    """'the city *forgot* how to stand' -> [(word, emphasised)...] (upper case, at most MAX_WORDS words, one emphasised).
    No asterisks: the longest word is the emphasised one."""
    words: list[tuple[str, bool]] = []
    for token in re.findall(r"\*[^*]+\*|\S+", (text or "").replace("\n", " ")):
        starred = token.startswith("*") and token.endswith("*") and len(token) > 2
        clean = re.sub(r"[*{}\\]", "", token).strip().upper()
        for part in clean.split():
            words.append((part, starred))
    words = words[:MAX_WORDS]
    if not words:
        return []
    marked = [i for i, (_, e) in enumerate(words) if e]
    pick = marked[0] if marked else max(range(len(words)), key=lambda i: len(words[i][0]))
    return [(w, i == pick) for i, (w, _) in enumerate(words)]


def hook_lines(words: list[tuple[str, bool]]) -> list[list[tuple[str, bool]]]:
    """At most 3 lines: the words before the emphasised word, the emphasised word alone, the words after it."""
    if not words:
        return []
    i = next(k for k, (_, e) in enumerate(words) if e)
    lines = [words[:i], [words[i]], words[i + 1:]]
    return [ln for ln in lines if ln][:MAX_LINES]


def _colour(name: str | None) -> str:
    """ASS colour (&HBBGGRR&) from a name or RRGGBB hex; red when unknown."""
    key = (name or "red").strip().lstrip("#")
    hexa = COLOURS.get(key.lower(), key)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", hexa):
        hexa = COLOURS["red"]
    return f"&H{hexa[4:6]}{hexa[2:4]}{hexa[0:2]}&".upper()


def _ts(sec: float) -> str:
    cs = round(sec * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def font_sizes(lines: list[list[tuple[str, bool]]], width: int, height: int) -> tuple[int, int]:
    """(normal, big) font sizes in px, shrunk together until the widest line fits inside the side margins."""
    normal, big = height * NORMAL_PCT, height * BIG_PCT
    room = width * (1 - 2 * SIDE_MARGIN)
    widest = max((sum(len(w) * CHAR_WIDTH * (big if e else normal) + (CHAR_WIDTH * normal if k else 0)
                      for k, (w, e) in enumerate(ln)) for ln in lines), default=0)
    if widest > room:
        scale = room / widest
        normal, big = normal * scale, big * scale
    return round(normal), round(big)


def hook_ass(text: str, width: int, height: int, color: str | None = None, seconds: float = HOOK_SECONDS) -> str | None:
    """The .ass file for a hook title, or None when there is no text."""
    lines = hook_lines(parse_hook(text))
    if not lines:
        return None
    normal, big = font_sizes(lines, width, height)
    accent = _colour(color)
    body = r"\N".join(" ".join((rf"{{\fs{big}\c{accent}}}{w}{{\fs{normal}\c&HFFFFFF&}}" if e else w) for w, e in ln) for ln in lines)
    outline, shadow = max(3, normal // 12), max(2, normal // 22)
    style = (f"Style: Hook,Segoe UI,{normal},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,"
             f"{outline},{shadow},5,{round(width * SIDE_MARGIN)},{round(width * SIDE_MARGIN)},0,1")
    pos = rf"{{\pos({width // 2},{round(height * CENTRE_Y)})\fad({FADE_IN_MS},{FADE_OUT_MS})}}"
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            f"MarginL, MarginR, MarginV, Encoding\n{style}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,{_ts(0)},{_ts(seconds)},Hook,,0,0,0,,{pos}{body}\n")


def filter_for(ass_name: str) -> str:
    """The FFmpeg filter that burns the .ass in. `ass_name` is a bare file name: FFmpeg runs in the file's folder, so no
    Windows drive letter ever reaches a filter option."""
    return rf"subtitles=filename='{ass_name}':fontsdir='C\:/Windows/Fonts'"


# ---------------------------------------------------------------------------------------------- "Choose the path" end card

END_SECONDS = 2.0
END_CENTRE_Y = 0.42     # a little lower than the hook: the question sits mid-frame, still clear of the caption zone
END_PROMPT = "comment A or B"
ARROW_A, ARROW_B = "←", "→"   # left / right arrows (Segoe UI has both)


def _path_words(text: str) -> str:
    """One option for the card: upper case, at most 4 words, no ASS control characters."""
    return " ".join(re.sub(r"[*{}\\]", "", text or "").upper().split()[:4])


def end_card_ass(path_a: str, path_b: str, width: int, height: int, length: float, color: str | None = None,
                 prompt: str = END_PROMPT, seconds: float = END_SECONDS) -> str | None:
    """The .ass file for the end card over the last `seconds` of a `length`-second Short: WHICH WAY? (big, coloured), the
    two options with arrows (A left, B right), and a small prompt to comment. Same style, fonts and safe margins as the
    hook title. None when either option is missing (the card is off)."""
    a, b = _path_words(path_a), _path_words(path_b)
    if not a or not b:
        return None
    rows = [[("WHICH", False), ("WAY?", True)], [(f"{ARROW_A} A: {a}", False)], [(f"B: {b} {ARROW_B}", False)]]
    normal, big = font_sizes(rows, width, height)
    small = max(1, round(normal * 0.62))
    accent = _colour(color)
    body = (rf"{{\fs{big}\c{accent}}}WHICH WAY?{{\fs{normal}\c&HFFFFFF&}}\N{ARROW_A} A: {a}\N"
            rf"B: {b} {ARROW_B}\N{{\fs{small}}}{_path_words(prompt) or END_PROMPT.upper()}")
    outline, shadow = max(3, normal // 12), max(2, normal // 22)
    style = (f"Style: End,Segoe UI,{normal},&H00FFFFFF,&H00FFFFFF,&H00101010,&H80000000,-1,0,0,0,100,100,0,0,1,"
             f"{outline},{shadow},5,{round(width * SIDE_MARGIN)},{round(width * SIDE_MARGIN)},0,1")
    start = max(0.0, length - seconds)
    pos = rf"{{\pos({width // 2},{round(height * END_CENTRE_Y)})\fad({FADE_IN_MS},0)}}"
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            f"MarginL, MarginR, MarginV, Encoding\n{style}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,{_ts(start)},{_ts(length + 1)},End,,0,0,0,,{pos}{body}\n")
