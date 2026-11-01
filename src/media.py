from preferences import _get_cached_anime_video_titles, _preload_folder_cache


STATUS_OPTIONS = (
    ("CURRENT", "Watching"),
    ("REPEATING", "Rewatching"),
    ("COMPLETED", "Completed"),
    ("DROPPED", "Dropped"),
    ("PLANNING", "Plan to Watch"),
    ("PAUSED", "Paused"),
)
STATUS_TO_CATEGORY = {
    "CURRENT": "CURRENTLY WATCHING",
    "REPEATING": "REWATCHING",
    "COMPLETED": "COMPLETED",
    "DROPPED": "DROPPED",
    "PLANNING": "PLAN TO WATCH",
    "PAUSED": "PAUSED",
}
CATEGORY_TO_STATUS = {category: status for status, category in STATUS_TO_CATEGORY.items()}


def prepare_media_entries(media_lists):
    _preload_folder_cache()
    mapping = {
        "Dropped": "DROPPED",
        "Completed": "COMPLETED",
        "Planning": "PLAN TO WATCH",
        "Plan to Watch": "PLAN TO WATCH",
        "Paused": "PAUSED",
        "Rewatching": "REWATCHING",
        "Watching": "CURRENTLY WATCHING",
        "Current": "CURRENTLY WATCHING",
    }
    prepared = []
    for media_list in media_lists:
        category = mapping.get(media_list.get("name", ""))
        if not category:
            continue
        for entry in media_list.get("entries", []):
            media = entry.get("media", {})
            title_data = media.get("title", {})
            title = (
                title_data.get("english")
                or title_data.get("romaji")
                or title_data.get("native")
                or "Unknown"
            )
            aliases = list(
                dict.fromkeys(
                    value
                    for value in (
                        title_data.get("english"),
                        title_data.get("romaji"),
                        title_data.get("native"),
                        *media.get("synonyms", []),
                        *_get_cached_anime_video_titles(str(media.get("id"))),
                    )
                    if value
                )
            )
            episodes = media.get("episodes") or 0
            progress = entry.get("progress", 0) or 0
            fraction = (
                min(max(progress / episodes, 0.0), 1.0)
                if episodes > 0
                else 1.0
            )
            prepared.append(
                (
                    category,
                    {
                        "title": title,
                        "title_aliases": "\n".join(aliases),
                        "progress": (
                            f"{progress}/{episodes}"
                            if episodes > 0
                            else f"{progress}/?"
                        ),
                        "progress_fraction": fraction,
                        "score": int(entry.get("score", 0) or 0),
                        "status": entry.get("status") or CATEGORY_TO_STATUS[category],
                        "format_type": str(media.get("format", "N/A")),
                        "anime_id": str(media.get("id")),
                        "episodes_total": episodes,
                        "watched_episodes": progress,
                    },
                )
            )
    return prepared