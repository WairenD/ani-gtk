import configparser
import json
import os
from pathlib import Path

from titles import clean_video_title, normalize_title


_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_SESSION_CONFIG_PATH = _CONFIG_HOME / "ani-gtk" / "session.ini"
_PREFERENCES_CONFIG_PATH = _CONFIG_HOME / "ani-gtk" / "settings.ini"
_folder_cache = {}
_folder_cache_mtime = 0


def session_config_path():
    return _SESSION_CONFIG_PATH


def preferences_config_path():
    return _PREFERENCES_CONFIG_PATH


def _preload_folder_cache():
    global _folder_cache, _folder_cache_mtime
    path = _PREFERENCES_CONFIG_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _folder_cache = {}
        _folder_cache_mtime = 0
        return
    if mtime != _folder_cache_mtime:
        _folder_cache_mtime = mtime
        config = configparser.ConfigParser()
        try:
            config.read(path)
        except (OSError, configparser.Error):
            _folder_cache = {}
            return
        _folder_cache = (
            dict(config.items("PREFERENCES"))
            if config.has_section("PREFERENCES")
            else {}
        )


def _get_cached_anime_folder(anime_id):
    return _folder_cache.get(f"anime_folder_{anime_id}", "")


def _get_cached_anime_video_titles(anime_id):
    raw_titles = _folder_cache.get(f"anime_video_titles_{anime_id}", "[]")
    try:
        titles = json.loads(raw_titles)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(titles, list):
        return []
    return [title for title in titles if isinstance(title, str) and title]


def _read_anime_folder_cached(anime_id):
    _preload_folder_cache()
    return _get_cached_anime_folder(anime_id)


def read_video_detection_setting():
    return read_preference("video_detection_enabled", True, "getboolean")


def read_completion_threshold():
    try:
        return max(1, min(read_preference("completion_threshold", 70, "getint"), 100))
    except (TypeError, ValueError):
        return 70


def read_confirm_episode_updates():
    return read_preference("confirm_episode_updates", False, "getboolean")


def anime_folder_preference_key(anime_id):
    return f"anime_folder_{anime_id}"


def read_anime_folder(anime_id):
    return read_preference(anime_folder_preference_key(anime_id), "", "get")


def save_anime_folder(anime_id, folder):
    key = anime_folder_preference_key(anime_id)
    save_preference(key, folder)
    _folder_cache[key] = str(folder)


def save_anime_video_title(anime_id, video_title):
    alias = clean_video_title(video_title)
    if not normalize_title(alias):
        return ""
    key = f"anime_video_titles_{anime_id}"
    titles = _get_cached_anime_video_titles(anime_id)
    normalized_titles = {normalize_title(title) for title in titles}
    if normalize_title(alias) not in normalized_titles:
        titles.append(alias)
        serialized = json.dumps(titles, ensure_ascii=True)
        save_preference(key, serialized)
        _folder_cache[key] = serialized
    return alias


def read_preference(key, fallback, getter):
    config = configparser.ConfigParser()
    try:
        config.read(preferences_config_path())
        return getattr(config, getter)("PREFERENCES", key, fallback=fallback)
    except (OSError, configparser.Error):
        return fallback


def save_preference(key, value):
    path = preferences_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config.read(path)
    if "PREFERENCES" not in config:
        config["PREFERENCES"] = {}
    config["PREFERENCES"][key] = str(value)
    with path.open("w") as settings_file:
        config.write(settings_file)
    path.chmod(0o600)


def save_video_detection_setting(enabled):
    save_preference("video_detection_enabled", bool(enabled))


def save_completion_threshold(threshold):
    save_preference("completion_threshold", max(1, min(int(threshold), 100)))


def save_confirm_episode_updates(enabled):
    save_preference("confirm_episode_updates", bool(enabled))


def read_saved_token():
    config = configparser.ConfigParser()
    try:
        config.read(session_config_path())
        return config.get("AUTH", "access_token", fallback="")
    except (OSError, configparser.Error):
        return ""


def save_token(token):
    path = session_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser()
    config["AUTH"] = {"access_token": token}
    with path.open("w") as session_file:
        config.write(session_file)
    path.chmod(0o600)


def clear_saved_token():
    try:
        session_config_path().unlink(missing_ok=True)
    except OSError:
        pass