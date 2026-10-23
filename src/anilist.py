# anilist.py
import requests

class AniListAPI:
    def __init__(self, access_token=None):
        self.base_url = "https://graphql.anilist.co"
        self.access_token = access_token

    def _post(self, query, variables):
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        response = requests.post(
            self.base_url,
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"][0].get("message", "AniList API error"))
        return payload["data"]

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
                        }
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