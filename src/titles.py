import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

from mpris import VIDEO_EXTENSIONS, extract_episode_info


def normalize_title(value):
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore")
    return " ".join(re.findall(r"[a-z0-9]+", normalized.decode("ascii").lower()))


def clean_video_title(value):
    if "://" in value:
        name = Path(unquote(urlparse(value).path)).stem
    else:
        file_path = Path(value)
        name = (
            file_path.stem
            if file_path.suffix.lower() in VIDEO_EXTENSIONS
            else value
        )
    episode_info = extract_episode_info(name)
    season = episode_info[0] if episode_info else None
    season_label = f" season {season} " if season else " "
    name = re.sub(r"(?i)[ ._-]*s\s*\d{1,2}[ ._-]*e\s*\d{1,3}\b", " ", name)
    name = re.sub(r"(?i)[ ._-]*\d{1,2}\s*x\s*\d{1,3}\b", " ", name)
    name = re.sub(
        r"(?i)[ ._-]*season[ ._-]*\d{1,2}\s*(?:(?:episode|ep)[ ._-]*)?\d{1,3}\b",
        " ",
        name,
    )
    name = re.sub(r"(?i)[ ._-]*(?:episode|ep)[ ._-]*\d{1,3}\b", " ", name)
    name = re.sub(r"(?i)[ ._-]+e[ ._-]*\d{1,3}\b", " ", name)
    name = re.sub(r"(?i)[ ._-]+\d{1,3}(?=[ ._-]|$)", " ", name)
    name = re.sub(
        r"(?i)[ ._-]+(?:\d{3,4}p|web[ ._-]?dl|bluray|h\.?26[45]|x26[45]).*$",
        " ",
        name,
    )
    return (name.replace(".", " ").replace("_", " ") + season_label).strip()