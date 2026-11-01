# anilist.py
import threading
import time

import requests


_REQUEST_LOCK = threading.Lock()
_MIN_REQUEST_INTERVAL = 1.0
_last_request_at = 0.0
_rate_limited_until = 0.0


class AniListAPI:
    def __init__(self, access_token=None):
        self.base_url = "https://graphql.anilist.co"
        self.access_token = access_token
        self._session = requests.Session()
        self._download_session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        if access_token:
            self._session.headers["Authorization"] = f"Bearer {access_token}"

    def _post(self, query, variables):
        global _last_request_at, _rate_limited_until
        with _REQUEST_LOCK:
            for attempt in range(2):
                now = time.monotonic()
                delay = max(
                    _last_request_at + _MIN_REQUEST_INTERVAL - now,
                    _rate_limited_until - now,
                    0,
                )
                if delay:
                    time.sleep(delay)
                _last_request_at = time.monotonic()
                response = self._session.post(
                    self.base_url,
                    json={"query": query, "variables": variables},
                    timeout=30,
                )
                if response.status_code != 429:
                    response.raise_for_status()
                    break

                retry_after = response.headers.get("Retry-After", "60")
                try:
                    retry_delay = max(1.0, float(retry_after))
                except (TypeError, ValueError):
                    retry_delay = 60.0
                _rate_limited_until = time.monotonic() + retry_delay
                if attempt == 1:
                    response.raise_for_status()
                time.sleep(retry_delay)

        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"][0].get("message", "AniList API error"))
        return payload["data"]

    def download(self, url, timeout=15):
        """Download a public resource without sending AniList credentials."""
        response = self._download_session.get(url, timeout=timeout)
        response.raise_for_status()
        return response.content

    def fetch_viewer(self):
        query = """
        query {
            Viewer {
                id
                name
                avatar {
                    large
                }
                bannerImage
            }
        }
        """
        return self._post(query, {})["Viewer"]

    def fetch_user_info(self, username):
        query = '''
        query ($username: String) {
            User(name: $username) {
                id
                name
                about
                siteUrl
                avatar {
                    large
                }
            }
        }
        '''
        variables = {
            "username": username
        }
        return self._post(query, variables)["User"]

    def fetch_media_list(self, user_id):
        query = '''
        query ($userId: Int) {
            MediaListCollection(userId: $userId, type: ANIME) {
                lists {
                    name
                    entries {
                        id
                        media {
                            id
                            title {
                                english
                                romaji
                                native
                            }
                            synonyms
                            format
                            episodes
                            nextAiringEpisode {
                                episode
                            }
                        }
                        status
                        progress
                        score
                    }
                }
            }
        }
        '''
        variables = {
            "userId": user_id
        }
        return self._post(query, variables)["MediaListCollection"]["lists"]

    def search_anime(self, search):
        query = """
        query ($search: String) {
            Page(perPage: 20) {
                media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
                    id
                    title {
                        english
                        romaji
                        native
                    }
                    coverImage {
                        medium
                    }
                    synonyms
                    format
                    episodes
                    nextAiringEpisode {
                        episode
                    }
                }
            }
        }
        """
        return self._post(query, {"search": search})["Page"]["media"]

    def update_progress(self, media_id, progress):
        query = """
        mutation ($mediaId: Int!, $progress: Int!) {
            SaveMediaListEntry(mediaId: $mediaId, progress: $progress) {
                id
                progress
            }
        }
        """
        variables = {"mediaId": int(media_id), "progress": int(progress)}
        return self._post(query, variables)["SaveMediaListEntry"]

    def update_score(self, media_id, score):
        query = """
        mutation ($mediaId: Int!, $score: Float!) {
            SaveMediaListEntry(mediaId: $mediaId, score: $score) {
                id
                score
            }
        }
        """
        variables = {"mediaId": int(media_id), "score": float(score)}
        return self._post(query, variables)["SaveMediaListEntry"]

    def update_status(self, media_id, status):
        query = """
        mutation ($mediaId: Int!, $status: MediaListStatus!) {
            SaveMediaListEntry(mediaId: $mediaId, status: $status) {
                id
                status
            }
        }
        """
        variables = {"mediaId": int(media_id), "status": status}
        return self._post(query, variables)["SaveMediaListEntry"]

    def delete_media_list_entry(self, entry_id):
        query = """
        mutation ($entryId: Int!) {
            DeleteMediaListEntry(id: $entryId) {
                deleted
            }
        }
        """
        variables = {"entryId": int(entry_id)}
        return self._post(query, variables)["DeleteMediaListEntry"]

    def fetch_anime_info(self, anime_id):
        query = '''
        query ($animeId: Int) {
            Media(id: $animeId, type: ANIME) {
                id
                title {
                    english
                    romaji
                }
                format
                episodes
                duration
                startDate {
                    year
                    month
                    day
                }
                endDate {
                    year
                    month
                    day
                }
                averageScore
                popularity
                description
                coverImage {
                    large
                }
            }
        }
        '''
        variables = {
            "animeId": anime_id
        }
        return self._post(query, variables)["Media"]