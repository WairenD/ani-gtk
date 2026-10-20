import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, Gtk
from mpris import LocalVideoWatcher
from ui import UI


def on_activate(app):
  window = Gtk.ApplicationWindow(
    application=app, title="AniList Library Explorer"
  )
  quit_action = Gio.SimpleAction.new("quit", None)
  quit_action.connect("activate", lambda *_: app.quit())
  app.add_action(quit_action)
  app.set_accels_for_action("app.quit", ["<Primary>q"])

  window.ui = UI(window)
  window.ui.install_notification_actions(app)
  if not hasattr(app, "video_watcher"):
    app.video_watcher = LocalVideoWatcher(
      app,
      window.ui.on_local_video_complete,
      window.ui.completion_threshold,
    )
    window.ui.video_watcher = app.video_watcher
    app.video_watcher.set_enabled(window.ui.video_detection_enabled)
    app.video_watcher.start()
  window.present()


if __name__ == "__main__":
  app = Gtk.Application(application_id="com.example.anilist")
  app.connect("activate", on_activate)
  app.run(None)