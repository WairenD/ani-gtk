from pathlib import Path
import re
from urllib.parse import unquote, urlparse

from gi.repository import Gio, GLib


VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogv",
    ".ts",
    ".webm",
    ".wmv",
}
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"


def is_local_video_url(url):
    parsed = urlparse(url or "")
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return False
    return Path(unquote(parsed.path)).suffix.lower() in VIDEO_EXTENSIONS


class LocalVideoWatcher:
    def __init__(self, application, on_video_complete=None):
        self.application = application
        self.on_video_complete = on_video_complete
        self.connection = None
        self.players = {}
        self.owners = {}
        self.signal_ids = []

    def start(self):
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.signal_ids.append(
                self.connection.signal_subscribe(
                    "org.freedesktop.DBus",
                    "org.freedesktop.DBus",
                    "NameOwnerChanged",
                    "/org/freedesktop/DBus",
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self.on_name_owner_changed,
                )
            )
            self.signal_ids.append(
                self.connection.signal_subscribe(
                    None,
                    PROPERTIES_INTERFACE,
                    "PropertiesChanged",
                    MPRIS_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    self.on_properties_changed,
                )
            )
            names = self.connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListNames",
                None,
                GLib.VariantType.new("(as)"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            ).unpack()[0]
            for name in names:
                if name.startswith(MPRIS_PREFIX):
                    self.add_player(name)
            GLib.timeout_add_seconds(5, self.poll_positions)
        except GLib.Error as error:
            print(f"MPRIS video detection unavailable: {error.message}")

    def add_player(self, name):
        if name in self.players or not self.connection:
            return
        try:
            owner = self.connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetNameOwner",
                GLib.Variant("(s)", (name,)),
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            ).unpack()[0]
            properties = self.connection.call_sync(
                name,
                MPRIS_PATH,
                "org.freedesktop.DBus.Properties",
                "GetAll",
                GLib.Variant("(s)", (PLAYER_INTERFACE,)),
                GLib.VariantType.new("(a{sv})"),
                Gio.DBusCallFlags.NONE,
                3000,
                None,
            ).unpack()[0]
            self.owners[owner] = name
            self.players[name] = {
                "owner": owner,
                "status": properties.get("PlaybackStatus", "Stopped"),
                "metadata": properties.get("Metadata", {}),
                "notified": False,
                "position_pending": False,
                "completed_url": "",
            }
        except GLib.Error:
            return

    def on_name_owner_changed(
        self, _connection, _sender, _path, _interface, _signal, parameters
    ):
        name, old_owner, new_owner = parameters.unpack()
        if not name.startswith(MPRIS_PREFIX):
            return
        if old_owner:
            self.owners.pop(old_owner, None)
            self.players.pop(name, None)
        if new_owner:
            self.add_player(name)

    def on_properties_changed(
        self, _connection, sender, _path, _interface, _signal, parameters
    ):
        player_name = self.owners.get(sender)
        if not player_name:
            return
        changed_interface, changed, _invalidated = parameters.unpack()
        if changed_interface != PLAYER_INTERFACE:
            return

        player = self.players.get(player_name)
        if not player:
            return
        if "PlaybackStatus" in changed:
            player["status"] = changed["PlaybackStatus"]
            if player["status"] != "Playing":
                player["notified"] = False
        if "Metadata" in changed:
            player["metadata"] = changed["Metadata"]
            current_url = unpack_value(player["metadata"].get("xesam:url", ""))
            if current_url != player["completed_url"]:
                player["notified"] = False

        if player["status"] != "Playing" or player["notified"]:
            return
        metadata = player["metadata"]
        url = unpack_value(metadata.get("xesam:url", ""))
        if not is_local_video_url(url):
            return

        title = unpack_value(metadata.get("xesam:title", "")) or Path(
            unquote(urlparse(url).path)
        ).name
        notification = Gio.Notification.new("Local video started")
        notification.set_body(str(title))
        notification.set_icon(Gio.ThemedIcon.new("video-x-generic"))
        self.application.send_notification(
            f"local-video-{player_name}", notification
        )
        player["notified"] = True

    def poll_positions(self):
        if not self.connection:
            return GLib.SOURCE_REMOVE
        for name, player in list(self.players.items()):
            metadata = player["metadata"]
            url = unpack_value(metadata.get("xesam:url", ""))
            duration = unpack_value(metadata.get("mpris:length", 0))
            if (
                player["status"] != "Playing"
                or not is_local_video_url(url)
                or not isinstance(duration, int)
                or duration <= 0
                or player["position_pending"]
            ):
                continue
            player["position_pending"] = True
            self.connection.call(
                name,
                MPRIS_PATH,
                PROPERTIES_INTERFACE,
                "Get",
                GLib.Variant("(ss)", (PLAYER_INTERFACE, "Position")),
                GLib.VariantType.new("(v)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
                self.on_position_received,
                (name, url, duration),
            )
        return GLib.SOURCE_CONTINUE

    def on_position_received(self, connection, result, user_data):
        name, url, duration = user_data
        player = self.players.get(name)
        if not player:
            return
        player["position_pending"] = False
        try:
            position = unpack_value(connection.call_finish(result).unpack()[0])
        except GLib.Error:
            return
        latest_url = unpack_value(
            player["metadata"].get("xesam:url", "")
        )
        latest_duration = unpack_value(
            player["metadata"].get("mpris:length", 0)
        )
        if latest_url == url and latest_duration == duration and duration > 0:
            percent = max(0.0, position / duration * 100)
            metadata = player["metadata"]
            title = unpack_value(metadata.get("xesam:title", "")) or Path(
                unquote(urlparse(url).path)
            ).name
            print(f"Video playback: {title} | {percent:.1f}%", flush=True)
        if (
            player["status"] != "Playing"
            or latest_url != url
            or latest_duration != duration
            or position < duration * 0.7
            or player["completed_url"] == url
        ):
            return

        metadata = player["metadata"]
        title = unpack_value(metadata.get("xesam:title", "")) or Path(
            unquote(urlparse(url).path)
        ).name
        if self.on_video_complete:
            handled = self.on_video_complete(str(title), url)
            print(
                f"Episode completion check: {title} | "
                f"{'accepted' if handled else 'not handled'}",
                flush=True,
            )
            if handled:
                player["completed_url"] = url
                player["notified"] = True

    def retry_video(self, url):
        for player in self.players.values():
            if player["completed_url"] == url:
                player["completed_url"] = ""
                player["notified"] = False


def unpack_value(value):
    while hasattr(value, "unpack"):
        value = value.unpack()
    return value


def extract_episode_number(value):
    episode_info = extract_episode_info(value)
    return episode_info[1] if episode_info else None


def extract_episode_info(value):
    text = Path(unquote(urlparse(value).path)).name if "://" in value else value
    patterns = (
        (r"(?i)\bs\s*(\d{1,2})\s*[._ -]*e\s*(\d{1,3})\b", "season"),
        (r"(?i)\b(\d{1,2})\s*x\s*(\d{1,3})\b", "season"),
        (
            r"(?i)\bseason[._ -]*(\d{1,2})[._ -]*(?:episode|ep)[._ -]*(\d{1,3})\b",
            "season",
        ),
        (
            r"(?i)\bseason[._ -]*(\d{1,2})[._ -]+(\d{1,3})\b",
            "season",
        ),
        (r"(?i)\b(?:episode|ep)\s*[.# -]*\s*(\d{1,3})\b", "episode"),
    )
    for pattern, kind in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if kind == "season":
            season, episode = (int(value) for value in match.groups())
        else:
            season, episode = None, int(match.group(1))
        if episode > 0:
            return season, episode

    # Common fansub naming: "Show - 01 - Episode title". Require a delimiter
    # on both sides and reject release-resolution/codec-style tokens.
    match = re.search(r"(?:^|[ ._-])[([]?(\d{1,3})[)\]]?(?=[ ._-])", text)
    if match and int(match.group(1)) > 0:
        return None, int(match.group(1))
    return None