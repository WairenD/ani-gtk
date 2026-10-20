import configparser
import gi
import json
import os
import queue
import re
import unicodedata
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import secrets
from threading import Thread
from urllib.parse import unquote, urlencode, urlparse

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from anilist import AniListAPI
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk
from mpris import VIDEO_EXTENSIONS, extract_episode_info, extract_episode_number
import pyperclip
import requests
from utils import format_date, interpret_html


class AnimeItem(GObject.GObject):
    title = GObject.Property(type=str, default="")
    title_aliases = GObject.Property(type=str, default="")
    progress = GObject.Property(type=str, default="")
    progress_fraction = GObject.Property(type=float, default=0.0)
    score = GObject.Property(type=int, default=0)
    format_type = GObject.Property(type=str, default="")
    anime_id = GObject.Property(type=str, default="")
    episodes_total = GObject.Property(type=int, default=0)
    watched_episodes = GObject.Property(type=int, default=0)

    def __init__(self, **kw):
        super().__init__(**kw)


class UI:

    def __init__(self, root):
        self.root = root
        self.access_token = read_saved_token()
        self.video_detection_enabled = read_video_detection_setting()
        self.completion_threshold = read_completion_threshold()
        self.confirm_episode_updates = read_confirm_episode_updates()
        self.pending_episode_updates = {}
        self.current_user = ""
        self.anilist_api = AniListAPI(self.access_token)
        self.list_update_queue = queue.Queue()
        Thread(target=self.process_list_update_queue, daemon=True).start()
        self.root.set_title("AniList Library Explorer")
        self.root.set_default_size(1600, 900)
        self.root.set_size_request(1200, 675)

        self.apply_styles()

        self.create_ui_components()
        if self.access_token:
            self.load_authenticated_user()

    def apply_styles(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            window { background-color: #2b2b2b; }
            label { font-family: Helvetica; font-size: 15px; }
            .title-label { font-size: 28px; font-weight: bold; color: #f0f0f0; }
            button { border-radius: 6px; padding: 6px 12px; font-size: 14px; font-weight: bold; }
            .auth-button { min-height: 32px; }
            .library-stat-value { font-size: 18px; font-weight: bold; color: #ffffff; }
            .library-stat-label { font-size: 11px; color: #e0e0e0; }
            separator { background-color: #484850; min-width: 1px; min-height: 1px; }
            .account-banner-tint { background-color: rgba(20, 20, 24, 0.48); border-radius: 10px; }
            .account-banner-stats { background-color: rgba(20, 20, 24, 0.42); border-radius: 0 0 10px 10px; }
            .account-banner { border-radius: 10px; }
            .profile-avatar { border-radius: 8px; }
            .cover-image { border-radius: 10px; }
            .startup-screen { background-color: #202127; }
            .startup-status { color: #b8b8c0; }
            frame { border-radius: 8px; }
            entry { background-color: #3c3c3c; color: #ffffff; caret-color: #ffffff; font-size: 15px; padding: 10px; border-radius: 8px; border: 1px solid #555; }
            .library-table-scroll { border-radius: 8px; background-color: #292a33; }
            columnview { border-radius: 8px; }
            textview { border-radius: 8px; background-color: #292a33; }
            
            columnview { font-size: 16px; }
            columnview header button { font-weight: bold; font-size: 15px; padding: 10px; }
            
            notebook tab { padding: 12px 20px; font-size: 15px; }
            notebook tab:checked { font-size: 17px; font-weight: bold; border-bottom: 3px solid #4a90e2; }
            
            progressbar trough { min-height: 10px; border-radius: 8px; background-color: rgba(128, 128, 128, 0.2); }
            progressbar progress { min-height: 10px; border-radius: 8px; }
            
            .pbar-btn { border-radius: 6px; padding: 2px 6px; font-size: 12px; min-height: 20px; min-width: 24px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def create_ui_components(self):
        self.main_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        self.install_window_actions()
        self.install_titlebar_menu()

        self.account_banner = Gtk.Overlay()
        self.account_banner.add_css_class("account-banner")
        self.account_banner.set_overflow(Gtk.Overflow.HIDDEN)
        self.account_banner.set_hexpand(True)
        self.account_banner.set_size_request(-1, 96)
        self.main_container.append(self.account_banner)

        self.banner_picture = Gtk.Picture()
        self.banner_picture.set_can_shrink(True)
        self.banner_picture.set_size_request(-1, 96)
        self.banner_picture.set_content_fit(Gtk.ContentFit.COVER)
        self.account_banner.set_child(self.banner_picture)

        banner_tint = Gtk.Box()
        banner_tint.add_css_class("account-banner-tint")
        self.account_banner.add_overlay(banner_tint)

        banner_foreground = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True,
            vexpand=True,
        )
        self.account_banner.add_overlay(banner_foreground)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.set_hexpand(True)
        header.set_margin_start(12)
        header.set_margin_end(12)
        header.set_margin_top(8)
        banner_foreground.append(header)

        banner_spacer = Gtk.Box(vexpand=True)
        banner_foreground.append(banner_spacer)

        self.avatar_stack = Gtk.Stack()
        self.avatar_stack.add_css_class("profile-avatar")
        self.avatar_stack.set_overflow(Gtk.Overflow.HIDDEN)
        self.avatar_stack.set_size_request(40, 40)
        self.avatar_stack.set_halign(Gtk.Align.START)
        self.avatar_stack.set_valign(Gtk.Align.CENTER)
        self.avatar_stack.add_named(
            Gtk.Image.new_from_icon_name("avatar-default-symbolic"), "default"
        )
        self.avatar_picture = Gtk.Picture()
        self.avatar_picture.set_size_request(40, 40)
        self.avatar_picture.set_can_shrink(True)
        self.avatar_picture.set_halign(Gtk.Align.CENTER)
        self.avatar_picture.set_valign(Gtk.Align.CENTER)
        self.avatar_picture.set_content_fit(Gtk.ContentFit.COVER)
        self.avatar_stack.add_named(self.avatar_picture, "profile")
        header.append(self.avatar_stack)

        header_spacer = Gtk.Box(hexpand=True)
        header.append(header_spacer)

        self.auth_controls = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=12
        )
        self.auth_controls.set_valign(Gtk.Align.CENTER)
        header.append(self.auth_controls)

        self.login_button = Gtk.Button(label="Login with AniList")
        self.login_button.add_css_class("auth-button")
        self.login_button.set_valign(Gtk.Align.CENTER)
        self.login_button.connect("clicked", lambda *_: self.start_login())
        self.auth_controls.append(self.login_button)

        self.logout_button = Gtk.Button(label="Log out")
        self.logout_button.add_css_class("auth-button")
        self.logout_button.set_valign(Gtk.Align.CENTER)
        self.logout_button.connect("clicked", lambda *_: self.logout())
        self.auth_controls.append(self.logout_button)

        self.auth_progress = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self.auth_spinner = Gtk.Spinner()
        self.auth_spinner.set_size_request(20, 20)
        self.auth_progress.append(self.auth_spinner)
        self.library_progress_bar = Gtk.ProgressBar(show_text=True)
        self.library_progress_bar.set_size_request(180, -1)
        self.library_progress_bar.set_visible(False)
        self.auth_progress.append(self.library_progress_bar)
        self.auth_status_label = Gtk.Label(label="Signing in...")
        self.auth_progress.append(self.auth_status_label)
        self.auth_controls.append(self.auth_progress)
        self.auth_progress.set_visible(False)
        self.update_auth_controls()

        self.split_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16,
            vexpand=True,
            hexpand=True,
        )
        self.main_container.append(self.split_box)

        self.left_frame = Gtk.Frame()
        self.left_frame.add_css_class("surface-frame")
        self.left_frame.set_overflow(Gtk.Overflow.HIDDEN)
        self.left_frame.set_vexpand(True)
        self.left_frame.set_hexpand(True)
        self.split_box.append(self.left_frame)

        self.left_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            margin_top=10,
            margin_bottom=10,
            margin_start=10,
            margin_end=10,
            vexpand=True,
            hexpand=True,
        )
        self.left_frame.set_child(self.left_box)

        self.summary_strip = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=0,
            margin_start=8,
            margin_end=8,
            margin_top=2,
            margin_bottom=2,
        )
        self.summary_values = {}
        for index, (key, caption) in enumerate(
            (("watching", "WATCHING"), ("completed", "COMPLETED"), ("total", "IN LIBRARY"))
        ):
            if index:
                self.summary_strip.append(
                    Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
                )
            stat = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2,
                hexpand=True,
                margin_start=14,
                margin_end=14,
                margin_top=4,
                margin_bottom=4,
            )
            value_label = Gtk.Label(label="0", xalign=0)
            value_label.add_css_class("library-stat-value")
            caption_label = Gtk.Label(label=caption, xalign=0)
            caption_label.add_css_class("library-stat-label")
            stat.append(value_label)
            stat.append(caption_label)
            self.summary_strip.append(stat)
            self.summary_values[key] = value_label

        self.summary_strip.add_css_class("account-banner-stats")
        self.summary_strip.set_halign(Gtk.Align.FILL)
        banner_foreground.append(self.summary_strip)

        self.left_box.append(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        )

        self.tab_control = Gtk.Notebook(vexpand=True, hexpand=True)
        self.tab_control.set_overflow(Gtk.Overflow.HIDDEN)
        self.left_box.append(self.tab_control)

        self.create_details_panel()

        self.category_views = {}
        categories = [
            "CURRENTLY WATCHING",
            "COMPLETED",
            "DROPPED",
            "PLAN TO WATCH",
            "PAUSED",
            "REWATCHING",
        ]

        for cat in categories:
            store = Gio.ListStore(item_type=AnimeItem)
            sort_model = Gtk.SortListModel.new(model=store, sorter=None)
            selection_model = Gtk.SingleSelection(model=sort_model)
            selection_model.connect(
                "notify::selected-item",
                lambda sel, *_: self.on_anime_selected(sel.get_selected_item()),
            )

            col_view = Gtk.ColumnView(
                model=selection_model, vexpand=True, hexpand=True
            )
            col_view.set_show_column_separators(True)
            sort_model.set_sorter(col_view.get_sorter())

            title_expression = Gtk.PropertyExpression.new(AnimeItem, None, "title")
            title_sorter = Gtk.StringSorter.new(expression=title_expression)

            type_expression = Gtk.PropertyExpression.new(
                AnimeItem, None, "format_type"
            )
            type_sorter = Gtk.StringSorter.new(expression=type_expression)

            score_expression = Gtk.PropertyExpression.new(AnimeItem, None, "score")
            score_sorter = Gtk.NumericSorter.new(expression=score_expression)

            def compare_progress(item1, item2, *args):
                return (item1.progress_fraction > item2.progress_fraction) - (
                    item1.progress_fraction < item2.progress_fraction
                )

            progress_sorter = Gtk.CustomSorter.new(compare_progress, None)

            columns_data = [
                ("Title", "title", 240, True, title_sorter),
                ("Progress", "progress_fraction", 170, False, progress_sorter),
                ("Score", "score", 90, False, score_sorter),
                ("Type", "format_type", 120, False, type_sorter),
            ]

            ui_instance = self
            for col_title, prop, width, expand_val, sorter in columns_data:
                factory = Gtk.SignalListItemFactory()
                if prop == "progress_fraction":

                    def setup_progress(factory, item):
                        box = Gtk.Box(
                            orientation=Gtk.Orientation.HORIZONTAL,
                            spacing=4,
                            valign=Gtk.Align.CENTER,
                            margin_start=4,
                            margin_end=4,
                        )
                        btn_minus = Gtk.Button(label="-")
                        btn_minus.add_css_class("pbar-btn")

                        pbar = Gtk.ProgressBar(
                            show_text=True, vexpand=True, hexpand=True
                        )

                        btn_plus = Gtk.Button(label="+")
                        btn_plus.add_css_class("pbar-btn")

                        box.append(btn_minus)
                        box.append(pbar)
                        box.append(btn_plus)

                        box.pbar = pbar

                        btn_minus.connect(
                            "clicked",
                            lambda _: hasattr(box, "anime")
                            and box.anime
                            and ui_instance.adjust_progress(box.anime, -1, box),
                        )
                        btn_plus.connect(
                            "clicked",
                            lambda _: hasattr(box, "anime")
                            and box.anime
                            and ui_instance.adjust_progress(box.anime, 1, box),
                        )

                        item.set_child(box)

                    def bind_progress(factory, item):
                        box = item.get_child()
                        previous_anime = getattr(box.pbar, "bound_anime", None)
                        for handler_id in getattr(
                            box.pbar, "progress_handlers", []
                        ):
                            if previous_anime:
                                previous_anime.disconnect(handler_id)
                        anime = item.get_item()
                        box.anime = anime
                        box.pbar.bound_anime = anime
                        box.pbar.progress_handlers = []
                        if anime:
                            box.pbar.set_fraction(anime.progress_fraction)
                            box.pbar.set_text(anime.progress)

                            def refresh_progress(*_args):
                                if box.pbar.bound_anime is anime:
                                    box.pbar.set_fraction(anime.progress_fraction)
                                    box.pbar.set_text(anime.progress)

                            box.pbar.progress_handlers = [
                                anime.connect(
                                    "notify::progress", refresh_progress
                                ),
                                anime.connect(
                                    "notify::progress-fraction", refresh_progress
                                ),
                            ]

                    factory.connect("setup", setup_progress)
                    factory.connect("bind", bind_progress)

                elif prop == "score":

                    def setup_score(factory, item):
                        score_model = Gtk.StringList.new([str(i) for i in range(101)])
                        dropdown = Gtk.DropDown(
                            model=score_model,
                            valign=Gtk.Align.CENTER,
                            hexpand=True,
                        )

                        dropdown.connect(
                            "notify::selected",
                            lambda dropdown_obj, _: ui_instance.on_score_changed(
                                dropdown_obj
                            ),
                        )
                        item.set_child(dropdown)

                    def bind_score(factory, item):
                        dropdown = item.get_child()
                        anime = item.get_item()
                        dropdown.anime = anime
                        if anime:
                            dropdown.is_binding = True
                            dropdown.set_selected(
                                min(max(int(anime.score), 0), 100)
                            )
                            dropdown.is_binding = False

                    factory.connect("setup", setup_score)
                    factory.connect("bind", bind_score)
                else:
                    factory.connect(
                        "setup",
                        lambda _, item: item.set_child(
                            Gtk.Label(xalign=0, margin_start=10)
                        ),
                    )
                    factory.connect(
                        "bind",
                        lambda _, item, p=prop: item.get_child().set_text(
                            str(getattr(item.get_item(), p, ""))
                        ),
                    )

                col = Gtk.ColumnViewColumn(title=col_title, factory=factory)
                col.set_fixed_width(width)
                col.set_resizable(False)
                col.set_expand(expand_val)
                col.set_sorter(sorter)
                col_view.append_column(col)

            scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
            scroll.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
            )
            scroll.add_css_class("library-table-scroll")
            scroll.set_overflow(Gtk.Overflow.HIDDEN)
            scroll.set_child(col_view)
            self.tab_control.append_page(scroll, Gtk.Label(label=cat))
            self.category_views[cat] = store
        self.update_library_summary()

        self.root_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=350,
            hexpand=True,
            vexpand=True,
        )
        self.root_stack.add_named(self.main_container, "main")
        self.root_stack.add_named(self.create_startup_screen(), "startup")
        self.root_stack.set_visible_child_name(
            "startup" if self.access_token else "main"
        )
        self.root.set_child(self.root_stack)

    def create_startup_screen(self):
        startup_screen = Gtk.Overlay(hexpand=True, vexpand=True)
        startup_screen.add_css_class("startup-screen")

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        startup_screen.set_child(content)

        spinner = Gtk.Spinner()
        spinner.set_size_request(36, 36)
        spinner.start()
        content.append(spinner)

        self.startup_status = Gtk.Label(label="Restoring your session...")
        self.startup_status.add_css_class("startup-status")
        content.append(self.startup_status)
        return startup_screen

    def show_main_content(self):
        if self.root_stack.get_visible_child_name() != "main":
            self.root_stack.set_visible_child_name("main")

    def install_window_actions(self):
        login_action = Gio.SimpleAction.new("login", None)
        login_action.connect("activate", lambda *_: self.start_login())
        self.root.add_action(login_action)

        logout_action = Gio.SimpleAction.new("logout", None)
        logout_action.connect("activate", lambda *_: self.logout())
        self.root.add_action(logout_action)

        shortcuts_action = Gio.SimpleAction.new("shortcuts", None)
        shortcuts_action.connect("activate", lambda *_: self.show_shortcuts())
        self.root.add_action(shortcuts_action)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", lambda *_: self.show_about())
        self.root.add_action(about_action)
        settings_action = Gio.SimpleAction.new("settings", None)
        settings_action.connect("activate", lambda *_: self.show_settings())
        self.root.add_action(settings_action)
        application = self.root.get_application()
        application.set_accels_for_action("win.login", ["<Primary>l"])
        application.set_accels_for_action(
            "win.shortcuts", ["<Primary>question"]
        )

        self.login_action = login_action
        self.logout_action = logout_action

    def install_notification_actions(self, application):
        confirm_action = Gio.SimpleAction.new(
            "confirm-episode", GLib.VariantType.new("s")
        )
        confirm_action.connect(
            "activate", lambda _action, parameter: self.confirm_episode_update(
                parameter.get_string()
            )
        )
        application.add_action(confirm_action)

        skip_action = Gio.SimpleAction.new("skip-episode", GLib.VariantType.new("s"))
        skip_action.connect(
            "activate", lambda _action, parameter: self.skip_episode_update(
                parameter.get_string()
            )
        )
        application.add_action(skip_action)

    def create_menu_model(self):
        menu_model = Gio.Menu()

        file_menu = Gio.Menu()
        file_menu.append("Quit", "app.quit")
        menu_model.append_submenu("File", file_menu)

        account_menu = Gio.Menu()
        account_menu.append("Login with AniList", "win.login")
        account_menu.append("Log out", "win.logout")
        menu_model.append_submenu("Account", account_menu)

        help_menu = Gio.Menu()
        help_menu.append("Keyboard Shortcuts", "win.shortcuts")
        help_menu.append("About AniList Library Explorer", "win.about")
        menu_model.append_submenu("Help", help_menu)

        settings_menu = Gio.Menu()
        menu_model.append("Settings", "win.settings")

        return menu_model

    def install_titlebar_menu(self):
        titlebar = Gtk.HeaderBar()
        titlebar.set_show_title_buttons(True)
        titlebar.set_title_widget(
            Gtk.Label(label=self.root.get_title() or "AniList Library Explorer")
        )

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(self.create_menu_model())
        menu_button.set_tooltip_text("Application menu")
        titlebar.pack_start(menu_button)
        self.root.set_titlebar(titlebar)

    def show_shortcuts(self):
        shortcuts_window = Gtk.ShortcutsWindow(transient_for=self.root, modal=True)
        section = Gtk.ShortcutsSection()
        group = Gtk.ShortcutsGroup(title="General")
        group.add_shortcut(
            Gtk.ShortcutsShortcut(title="Quit", accelerator="<Primary>q")
        )
        group.add_shortcut(
            Gtk.ShortcutsShortcut(
                title="Log in with AniList", accelerator="<Primary>l"
            )
        )
        group.add_shortcut(
            Gtk.ShortcutsShortcut(
                title="Show keyboard shortcuts", accelerator="<Primary>question"
            )
        )
        section.add_group(group)
        shortcuts_window.add_section(section)
        shortcuts_window.present()

    def show_about(self):
        about_dialog = Gtk.AboutDialog(transient_for=self.root, modal=True)
        about_dialog.set_program_name("AniList Library Explorer")
        about_dialog.set_version("0.1.0")
        about_dialog.set_comments(
            "Browse and manage your AniList anime library. Update episode "
            "progress and scores, view anime details, and optionally detect "
            "local video playback to advance currently watching episodes."
        )
        about_dialog.set_website("https://anilist.co/")
        about_dialog.set_website_label("AniList")
        about_dialog.set_authors(["AniList Library Explorer contributors"])
        about_dialog.present()

    def show_settings(self):
        if getattr(self, "settings_window", None):
            self.settings_window.present()
            return

        settings_window = Gtk.Window(
            title="Settings",
            transient_for=self.root,
            modal=True,
            resizable=False,
        )
        settings_window.set_default_size(480, 320)
        settings_window.set_child(self.create_settings_page())
        settings_window.connect(
            "close-request", lambda *_: self.clear_settings_window()
        )
        self.settings_window = settings_window
        settings_window.present()

    def create_settings_page(self):
        page = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
        )
        heading = Gtk.Label(label="Playback", xalign=0)
        heading.add_css_class("title-label")
        page.append(heading)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        row.append(
            Gtk.Label(
                label="Detect local video playback",
                xalign=0,
                hexpand=True,
            )
        )
        self.video_detection_switch = Gtk.Switch(
            active=self.video_detection_enabled,
            valign=Gtk.Align.CENTER,
        )
        self.video_detection_switch.connect(
            "notify::active", self.on_video_detection_toggled
        )
        row.append(self.video_detection_switch)
        page.append(row)

        threshold_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=16
        )
        threshold_label = Gtk.Label(
            label="Episode completion threshold", xalign=0, hexpand=True
        )
        threshold_row.append(threshold_label)
        adjustment = Gtk.Adjustment(
            value=self.completion_threshold,
            lower=1,
            upper=100,
            step_increment=1,
            page_increment=10,
        )
        self.completion_threshold_spin = Gtk.SpinButton(
            adjustment=adjustment, climb_rate=1, digits=0, numeric=True
        )
        self.completion_threshold_spin.connect(
            "value-changed", self.on_completion_threshold_changed
        )
        threshold_row.append(self.completion_threshold_spin)
        threshold_row.append(Gtk.Label(label="%"))
        page.append(threshold_row)

        confirmation_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=16
        )
        confirmation_row.append(
            Gtk.Label(
                label="Ask before updating episode progress",
                xalign=0,
                hexpand=True,
            )
        )
        self.confirm_episode_switch = Gtk.Switch(
            active=self.confirm_episode_updates,
            valign=Gtk.Align.CENTER,
        )
        self.confirm_episode_switch.connect(
            "notify::active", self.on_confirm_episode_toggled
        )
        confirmation_row.append(self.confirm_episode_switch)
        page.append(confirmation_row)
        page.append(
            Gtk.Label(
                label="Detects local videos from compatible media players and can "
                "update episode progress in AniList.",
                wrap=True,
                xalign=0,
            )
        )
        return page

    def on_video_detection_toggled(self, switch, _property):
        self.video_detection_enabled = switch.get_active()
        save_video_detection_setting(self.video_detection_enabled)
        watcher = getattr(self, "video_watcher", None)
        if watcher:
            watcher.set_enabled(self.video_detection_enabled)

    def on_completion_threshold_changed(self, spin_button):
        self.completion_threshold = spin_button.get_value_as_int()
        save_completion_threshold(self.completion_threshold)
        watcher = getattr(self, "video_watcher", None)
        if watcher:
            watcher.set_completion_threshold(self.completion_threshold)

    def on_confirm_episode_toggled(self, switch, _property):
        self.confirm_episode_updates = switch.get_active()
        save_confirm_episode_updates(self.confirm_episode_updates)

    def clear_settings_window(self):
        self.settings_window = None
        return False

    def update_library_summary(self):
        watching = self.category_views.get("CURRENTLY WATCHING")
        completed = self.category_views.get("COMPLETED")
        self.summary_values["watching"].set_text(
            str(watching.get_n_items() if watching else 0)
        )
        self.summary_values["completed"].set_text(
            str(completed.get_n_items() if completed else 0)
        )
        self.summary_values["total"].set_text(
            str(sum(model.get_n_items() for model in self.category_views.values()))
        )

    def update_auth_controls(self):
        signed_in = bool(self.access_token)
        busy = getattr(self, "auth_busy", False)
        self.login_button.set_visible(not signed_in)
        self.login_button.set_sensitive(not busy)
        self.logout_button.set_visible(signed_in)
        self.logout_button.set_sensitive(not busy)
        if hasattr(self, "login_action"):
            self.login_action.set_enabled(not signed_in and not busy)
            self.logout_action.set_enabled(signed_in and not busy)
        if not signed_in:
            self.avatar_stack.set_visible_child_name("default")

    def start_login(self):
        client_id = read_client_id()
        if not client_id:
            return self.show_error(
                "AniList setup required",
                "Set client_id under [ANILIST] in the workspace config.ini, or set "
                "ANILIST_CLIENT_ID in the environment. "
                "Register the app and redirect URI in AniList developer settings first.",
            )

        dialog = Gtk.Dialog(
            title="Connect AniList", transient_for=self.root, modal=True
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        content = dialog.get_content_area()
        content.set_spacing(10)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)
        content.append(Gtk.Label(
            label=(
                "AniList will open in your default browser. Sign in if needed, "
                "then approve access for this app."
            ),
            wrap=True,
            xalign=0,
        ))
        dialog.connect("response", lambda *_: self.stop_oauth_callback())
        self.oauth_dialog = dialog

        redirect_uri = "http://127.0.0.1:8765/callback"
        oauth_state = secrets.token_urlsafe(32)
        try:
            self.oauth_server = OAuthCallbackServer(
                self.on_oauth_token, redirect_uri, oauth_state
            )
            self.oauth_server.start()
            auth_url = "https://anilist.co/api/v2/oauth/authorize?" + urlencode(
                {
                    "client_id": client_id,
                    "response_type": "token",
                    "state": oauth_state,
                }
            )
            self.open_auth_url(auth_url)
        except Exception as error:
            self.stop_oauth_callback()
            return self.show_error(
                "Login Error",
                f"Could not start the AniList callback: {error}. "
                "Make sure the redirect URI is registered as "
                "http://127.0.0.1:8765/callback.",
            )
        dialog.present()

    def open_auth_url(self, auth_url):
        Gio.AppInfo.launch_default_for_uri(auth_url, None)

    def on_oauth_token(self, token):
        self.stop_oauth_callback()
        if self.oauth_dialog:
            self.oauth_dialog.destroy()
            self.oauth_dialog = None
        self.authenticate(token)
        return GLib.SOURCE_REMOVE

    def stop_oauth_callback(self):
        server = getattr(self, "oauth_server", None)
        if server:
            self.oauth_server = None
            server.stop()
        dialog = getattr(self, "oauth_dialog", None)
        if dialog:
            self.oauth_dialog = None
            dialog.destroy()

    def authenticate(self, token, startup=False):
        self.startup_session_loading = startup
        self.auth_busy = True
        self.auth_progress.set_visible(True)
        self.auth_spinner.start()
        self.auth_status_label.set_text(
            "Restoring session..."
            if self.startup_session_loading
            else "Signing in..."
        )
        if startup:
            self.startup_status.set_text("Restoring your session...")
        self.update_auth_controls()

        def load_account():
            try:
                api = AniListAPI(token)
                viewer = api.fetch_viewer()
                media_lists = api.fetch_media_list(viewer["id"])
                library_entries = prepare_media_entries(media_lists)
                avatar_data = None
                avatar_url = viewer.get("avatar", {}).get("large")
                if avatar_url:
                    try:
                        avatar_response = requests.get(avatar_url, timeout=15)
                        avatar_response.raise_for_status()
                        avatar_data = avatar_response.content
                    except requests.RequestException:
                        pass
                banner_data = None
                banner_url = viewer.get("bannerImage")
                if banner_url:
                    try:
                        banner_response = requests.get(banner_url, timeout=15)
                        banner_response.raise_for_status()
                        banner_data = banner_response.content
                    except requests.RequestException:
                        pass
                result = (
                    api,
                    viewer,
                    library_entries,
                    avatar_data,
                    banner_data,
                    None,
                )
            except Exception as error:
                result = (None, None, None, None, None, error)
            GLib.idle_add(self.finish_authentication, token, result)

        Thread(target=load_account, daemon=True).start()

    def finish_authentication(self, token, result):
        api, viewer, library_entries, avatar_data, banner_data, error = result
        if error:
            if self.startup_session_loading:
                self.show_main_content()
            self.auth_busy = False
            self.auth_spinner.stop()
            self.auth_progress.set_visible(False)
            self.anilist_api.access_token = None
            self.access_token = None
            self.current_user = ""
            self.update_auth_controls()
            if token == read_saved_token():
                self.logout()
                self.show_error("Session Expired", f"Please log in again. {error}")
            else:
                self.show_error("Login Error", f"AniList authentication failed: {error}")
            return GLib.SOURCE_REMOVE

        self.anilist_api = api
        self.access_token = token
        self.current_user = viewer["name"]
        if avatar_data:
            try:
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(avatar_data)
                loader.close()
                self.avatar_picture.set_paintable(
                    Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
                )
                self.avatar_stack.set_visible_child_name("profile")
            except Exception:
                self.avatar_stack.set_visible_child_name("default")
        self.banner_picture.set_paintable(
            banner_texture_from_image_data(banner_data, 1600, 96)
            if banner_data
            else None
        )
        save_token(token)
        self.update_auth_controls()
        if self.startup_session_loading:
            self.startup_status.set_text("Loading your library...")
            self.library_progress_bar.set_visible(False)
        else:
            self.auth_status_label.set_text("Loading library...")
            self.auth_spinner.stop()
            self.auth_spinner.set_visible(False)
            self.library_progress_bar.set_fraction(0.0)
            self.library_progress_bar.set_text("0%")
            self.library_progress_bar.set_visible(True)
        for model in self.category_views.values():
            model.remove_all()
        self.pending_library_entries = library_entries
        self.library_load_index = 0
        GLib.idle_add(self.append_library_batch)
        return GLib.SOURCE_REMOVE

    def append_library_batch(self):
        batch_size = 40
        batch_end = min(
            self.library_load_index + batch_size,
            len(self.pending_library_entries),
        )
        for category, item_data in self.pending_library_entries[
            self.library_load_index:batch_end
        ]:
            model = self.category_views.get(category)
            if model is not None:
                model.append(AnimeItem(**item_data))
        self.library_load_index = batch_end
        total_entries = len(self.pending_library_entries)
        fraction = batch_end / total_entries if total_entries else 1.0
        if not getattr(self, "startup_session_loading", False):
            self.library_progress_bar.set_fraction(fraction)
            self.library_progress_bar.set_text(f"{round(fraction * 100)}%")

        if batch_end < total_entries:
            return GLib.SOURCE_CONTINUE

        self.library_progress_bar.set_visible(False)
        self.auth_spinner.set_visible(True)
        self.auth_spinner.stop()
        self.auth_progress.set_visible(False)
        self.pending_library_entries = []
        if self.startup_session_loading:
            self.show_main_content()
        self.startup_session_loading = False
        self.auth_busy = False
        self.update_library_summary()
        self.update_auth_controls()
        return GLib.SOURCE_REMOVE

    def load_authenticated_user(self):
        self.authenticate(self.access_token, startup=True)

    def on_local_video_complete(self, media_title, media_url):
        episode_number = extract_episode_number(media_title) or extract_episode_number(
            media_url
        )
        if not episode_number:
            print(f"Episode update skipped: no episode number in {media_title}")
            return False

        candidates = [media_title, media_url]
        normalized_candidates = {
            normalize_title(clean_video_title(candidate))
            for candidate in candidates
        }
        matches = []
        watching_store = self.category_views.get("CURRENTLY WATCHING")
        if watching_store is None or watching_store.get_n_items() == 0:
            print("Episode update deferred: Currently Watching list is empty")
            return False
        for index in range(watching_store.get_n_items()):
            anime = watching_store.get_item(index)
            aliases = [anime.title, *anime.title_aliases.split("\n")]
            matched_alias_lengths = []
            for alias in aliases:
                normalized_alias = normalize_title(alias)
                if len(normalized_alias) < 4:
                    continue
                alias_tokens = normalized_alias.split()
                for candidate in normalized_candidates:
                    candidate_tokens = candidate.split()
                    if normalized_alias in candidate or all(
                        token in candidate_tokens for token in alias_tokens
                    ):
                        matched_alias_lengths.append(len(normalized_alias))
                        break
                    if (
                        alias_tokens[-1].isdigit()
                        and int(alias_tokens[-1]) == episode_number
                        and candidate_tokens == alias_tokens[:-1]
                    ):
                        matched_alias_lengths.append(
                            len(" ".join(alias_tokens[:-1]))
                        )
                        break
            if matched_alias_lengths:
                matches.append((max(matched_alias_lengths), anime))

        if not matches:
            considered = [
                watching_store.get_item(index).title
                for index in range(watching_store.get_n_items())
            ]
            print(
                f"Episode update skipped: no Currently Watching title matched "
                f"'{media_title}' (episode {episode_number}); titles: {considered}"
            )
            return False
        best_length = max(length for length, _anime in matches)
        best_matches = [anime for length, anime in matches if length == best_length]
        if len(best_matches) != 1:
            print(
                f"Episode update skipped: title '{media_title}' matched multiple "
                "Currently Watching entries"
            )
            return False

        anime = best_matches[0]
        new_progress = max(anime.watched_episodes, episode_number)
        if anime.episodes_total > 0:
            new_progress = min(new_progress, anime.episodes_total)
        if new_progress <= anime.watched_episodes:
            print(
                f"Episode update skipped: {anime.title} is already at episode "
                f"{anime.watched_episodes} (video episode {episode_number})"
            )
            return True

        if self.confirm_episode_updates:
            request_id = secrets.token_urlsafe(12)
            self.pending_episode_updates[request_id] = {
                "anime": anime,
                "new_progress": new_progress,
                "old_progress": anime.watched_episodes,
                "media_url": media_url,
            }
            self.send_episode_confirmation(request_id, anime.title, new_progress)
            print(
                f"Episode update awaiting confirmation: {anime.title} "
                f"episode {new_progress}"
            )
            return True

        old_progress = anime.watched_episodes
        anime.watched_episodes = new_progress
        anime.progress = (
            f"{new_progress}/{anime.episodes_total}"
            if anime.episodes_total > 0
            else f"{new_progress}/?"
        )
        anime.progress_fraction = (
            min(new_progress / anime.episodes_total, 1.0)
            if anime.episodes_total > 0
            else 1.0
        )
        self.queue_list_update(
            anime,
            "progress",
            new_progress,
            old_progress,
            None,
            notify=True,
            retry_video_url=media_url,
        )
        print(
            f"Episode update queued: {anime.title} -> episode {new_progress}"
        )
        return True

    def send_episode_confirmation(self, request_id, anime_title, episode_count):
        notification = Gio.Notification.new("Update AniList episode progress?")
        notification.set_body(f"{anime_title}: mark episode {episode_count} watched?")
        notification.set_icon(Gio.ThemedIcon.new("video-x-generic"))
        notification.add_button(
            "Update", f"app.confirm-episode('{request_id}')"
        )
        notification.add_button("Skip", f"app.skip-episode('{request_id}')")
        application = self.root.get_application()
        if application:
            application.send_notification(
                f"episode-confirm-{request_id}", notification
            )

    def confirm_episode_update(self, request_id):
        pending = self.pending_episode_updates.pop(request_id, None)
        if not pending:
            return
        anime = pending["anime"]
        new_progress = pending["new_progress"]
        old_progress = pending["old_progress"]
        anime.watched_episodes = new_progress
        anime.progress = (
            f"{new_progress}/{anime.episodes_total}"
            if anime.episodes_total > 0
            else f"{new_progress}/?"
        )
        anime.progress_fraction = (
            min(new_progress / anime.episodes_total, 1.0)
            if anime.episodes_total > 0
            else 1.0
        )
        self.queue_list_update(
            anime,
            "progress",
            new_progress,
            old_progress,
            None,
            notify=True,
            retry_video_url=pending["media_url"],
        )

    def skip_episode_update(self, request_id):
        self.pending_episode_updates.pop(request_id, None)

    def logout(self):
        self.access_token = None
        self.current_user = ""
        self.anilist_api.access_token = None
        clear_saved_token()
        for model in self.category_views.values():
            model.remove_all()
        self.update_library_summary()
        self.avatar_picture.set_paintable(None)
        self.banner_picture.set_paintable(None)
        self.avatar_stack.set_visible_child_name("default")
        self.update_auth_controls()
        self.metadata_label.set_text("")
        self.right_cover_picture.set_paintable(None)
        self.set_details_text("Select an anime from the list to view its details here.")

    def adjust_progress(self, anime, delta, box):
        previous = anime.watched_episodes
        new_watched = max(0, anime.watched_episodes + delta)
        if anime.episodes_total > 0:
            if new_watched > anime.episodes_total:
                new_watched = anime.episodes_total
            anime.progress_fraction = min(
                max(new_watched / anime.episodes_total, 0.0), 1.0
            )
            anime.progress = f"{new_watched}/{anime.episodes_total}"
        else:
            anime.progress_fraction = 1.0
            anime.progress = f"{new_watched}/?"

        anime.watched_episodes = new_watched
        box.pbar.set_fraction(anime.progress_fraction)
        box.pbar.set_text(anime.progress)
        self.queue_list_update(
            anime, "progress", new_watched, previous, box
        )

    def on_score_changed(self, dropdown):
        if getattr(dropdown, "is_binding", False):
            return
        anime = getattr(dropdown, "anime", None)
        if not anime:
            return
        previous = anime.score
        new_score = dropdown.get_selected()
        if new_score == previous:
            return
        anime.score = new_score
        self.queue_list_update(anime, "score", new_score, previous, dropdown)

    def queue_list_update(
        self,
        anime,
        field,
        value,
        previous,
        widget,
        notify=False,
        retry_video_url=None,
    ):
        token = self.access_token
        if not token:
            self.restore_list_value(anime, field, previous, widget)
            self.show_error("Not signed in", "Log in to save list changes.")
            return
        self.list_update_queue.put(
            (
                token,
                str(anime.anime_id),
                anime,
                field,
                value,
                previous,
                widget,
                notify,
                retry_video_url,
            )
        )

    def process_list_update_queue(self):
        while True:
            (
                token,
                media_id,
                anime,
                field,
                value,
                previous,
                widget,
                notify,
                retry_video_url,
            ) = self.list_update_queue.get()
            try:
                api = AniListAPI(token)
                if field == "progress":
                    api.update_progress(media_id, value)
                else:
                    api.update_score(media_id, value)
                error = None
                if notify:
                    print(
                        f"AniList confirmed episode progress: {anime.title} "
                        f"episode {value}",
                        flush=True,
                    )
            except Exception as exception:
                error = exception
                if notify:
                    print(
                        f"AniList rejected episode progress for {anime.title}: "
                        f"{exception}",
                        flush=True,
                    )
            if error:
                GLib.idle_add(
                    self.on_list_update_failed,
                    token,
                    anime,
                    field,
                    value,
                    previous,
                    widget,
                    str(error),
                    retry_video_url,
                )
            elif notify:
                GLib.idle_add(
                    self.notify_episode_progress,
                    token,
                    anime.title,
                    value,
                )
            self.list_update_queue.task_done()

    def notify_episode_progress(self, token, anime_title, episode_count):
        if token != self.access_token:
            return GLib.SOURCE_REMOVE
        notification = Gio.Notification.new("Episode progress updated")
        notification.set_body(f"{anime_title}: episode {episode_count}")
        notification.set_icon(Gio.ThemedIcon.new("video-x-generic"))
        application = self.root.get_application()
        if application:
            application.send_notification(
                f"episode-progress-{anime_title}", notification
            )
            print(
                f"Episode progress notification sent: {anime_title} episode "
                f"{episode_count}",
                flush=True,
            )
        return GLib.SOURCE_REMOVE

    def on_list_update_failed(
        self,
        token,
        anime,
        field,
        value,
        previous,
        widget,
        message,
        retry_video_url=None,
    ):
        if token == self.access_token:
            self.restore_list_value(anime, field, previous, widget)
            if retry_video_url and getattr(self, "video_watcher", None):
                self.video_watcher.retry_video(retry_video_url)
            self.show_error("Save Failed", f"AniList did not save the change: {message}")
        return GLib.SOURCE_REMOVE

    def restore_list_value(self, anime, field, value, widget):
        if field == "progress":
            anime.watched_episodes = value
            if anime.episodes_total > 0:
                anime.progress_fraction = min(
                    max(value / anime.episodes_total, 0.0), 1.0
                )
                anime.progress = f"{value}/{anime.episodes_total}"
            else:
                anime.progress = f"{value}/?"
                anime.progress_fraction = 1.0
            if getattr(widget, "anime", None) is anime:
                widget.pbar.set_fraction(anime.progress_fraction)
                widget.pbar.set_text(anime.progress)
        else:
            anime.score = value
            if getattr(widget, "anime", None) is anime:
                widget.is_binding = True
                widget.set_selected(min(max(int(value), 0), 100))
                widget.is_binding = False

    def create_details_panel(self):
        right_frame = Gtk.Frame(label="Anime Details")
        right_frame.add_css_class("surface-frame")
        right_frame.set_overflow(Gtk.Overflow.HIDDEN)
        right_frame.set_vexpand(True)
        right_frame.set_size_request(450, -1)

        right_main_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
            vexpand=True,
            hexpand=True,
        )
        right_frame.set_child(right_main_box)
        self.split_box.append(right_frame)

        top_info_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=14,
            vexpand=False,
            hexpand=True,
        )
        right_main_box.append(top_info_box)

        image_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            vexpand=False,
            hexpand=False,
            valign=Gtk.Align.START,
        )
        image_box.set_size_request(160, -1)

        self.right_cover_picture = Gtk.Picture.new_for_paintable(None)
        self.right_cover_picture.add_css_class("cover-image")
        self.right_cover_picture.set_overflow(Gtk.Overflow.HIDDEN)
        self.right_cover_picture.set_size_request(150, 210)
        self.right_cover_picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.right_cover_picture.set_halign(Gtk.Align.CENTER)
        self.right_cover_picture.set_valign(Gtk.Align.START)
        image_box.append(self.right_cover_picture)
        top_info_box.append(image_box)

        self.metadata_label = Gtk.Label(
            xalign=0, yalign=0, wrap=True, hexpand=True, vexpand=False
        )
        self.metadata_label.set_use_markup(True)
        top_info_box.append(self.metadata_label)

        right_main_box.append(
            Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        )

        desc_frame = Gtk.Frame(label="Description")
        desc_frame.add_css_class("surface-frame")
        desc_frame.set_overflow(Gtk.Overflow.HIDDEN)
        desc_frame.set_vexpand(True)
        desc_frame.set_hexpand(True)
        right_main_box.append(desc_frame)

        self.right_text_view = Gtk.TextView(
            editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD
        )
        self.right_text_view.set_overflow(Gtk.Overflow.HIDDEN)
        self.right_text_buffer = self.right_text_view.get_buffer()
        self.right_text_buffer.create_tag("desc_font", font="Helvetica 16")

        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        scroll.set_overflow(Gtk.Overflow.HIDDEN)
        scroll.set_child(self.right_text_view)
        desc_frame.set_child(scroll)

        self.set_details_text(
            "\n   Select an anime from the list to view its details here."
        )

    def set_details_text(self, text):
        self.right_text_buffer.set_text(text)
        self.right_text_buffer.apply_tag_by_name(
            "desc_font",
            self.right_text_buffer.get_start_iter(),
            self.right_text_buffer.get_end_iter(),
        )

    def on_anime_selected(self, item):
        if not item or not hasattr(item, "anime_id"):
            return
        try:
            info = self.anilist_api.fetch_anime_info(item.anime_id)

            if cover_url := info.get("coverImage", {}).get("large"):
                try:
                    response = requests.get(cover_url)
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(response.content)
                    loader.close()
                    self.right_cover_picture.set_paintable(
                        Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
                    )
                except Exception:
                    self.right_cover_picture.set_paintable(None)
            else:
                self.right_cover_picture.set_paintable(None)

            title_obj = info.get("title", {})
            title = (
                title_obj.get("english")
                or title_obj.get("romaji")
                or title_obj.get("native")
                or "Unknown"
            )

            metadata_markup = (
                f"<span size='xx-large' weight='bold'>{title}</span>\n\n"
                f"<span size='large'>"
                f"Format: {info.get('format', 'N/A')}\n"
                f"Episodes: {info.get('episodes') or 'N/A'}\n"
                f"Duration: {info.get('duration') or 'N/A'} min\n"
                f"Start: {format_date(info.get('startDate'))}\n"
                f"End: {format_date(info.get('endDate'))}\n"
                f"Score: {info.get('averageScore') or 'N/A'} / 100\n"
                f"Popularity: {info.get('popularity') or 'N/A'}"
                f"</span>"
            )
            self.metadata_label.set_markup(metadata_markup)

            desc_html = (
                interpret_html(info.get("description"))
                if info.get("description")
                else "N/A"
            )
            self.set_details_text(desc_html)

        except Exception as e:
            print(f"Failed to load anime info: {e}")

    def show_error(self, title, message):
        try:
            dialog = Gtk.AlertDialog(message=message, detail=title)
            dialog.set_buttons(["Close", "Copy Error"])
            dialog.choose(
                self.root,
                None,
                lambda d, r: (
                    pyperclip.copy(message)
                    if dialog.choose_finish(r) == 1
                    else None
                ),
            )
        except Exception:
            dialog = Gtk.MessageDialog(
                transient_for=self.root,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text=title,
            )
            dialog.format_secondary_text(message)
            dialog.connect("response", lambda d, *_: d.destroy())
            dialog.present()


class OAuthCallbackServer:
    def __init__(
        self, on_token, redirect_uri, state, host="127.0.0.1", port=8765
    ):
        self.on_token = on_token
        self.redirect_uri = redirect_uri
        self.state = state
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def send_text(self, status, body, content_type="text/plain; charset=utf-8"):
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                if self.path.split("?", 1)[0] != "/callback":
                    return self.send_text(404, "Not found")
                page = """<!doctype html><html><head><meta charset="utf-8">
<title>AniList sign-in</title></head><body>
<p id="status">Completing AniList sign-in...</p>
<script>
const expectedState = __EXPECTED_STATE__;
const token = new URLSearchParams(location.hash.slice(1)).get('access_token');
const state = new URLSearchParams(location.hash.slice(1)).get('state');
history.replaceState(null, '', location.pathname);
if (!token || state !== expectedState) {
  document.getElementById('status').textContent =
        'AniList authorization could not be verified. Please retry.';
} else {
  fetch('/token', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({access_token: token, state: state})})
    .then(response => {
      document.getElementById('status').textContent = response.ok
        ? 'AniList connected. You can close this tab.'
        : 'Could not pass the token to the app. Please retry.';
    })
    .catch(() => document.getElementById('status').textContent =
      'Could not reach the app. Please retry.');
}
</script></body></html>"""
                page = page.replace("__EXPECTED_STATE__", json.dumps(callback.state))
                self.send_text(200, page, "text/html; charset=utf-8")

            def do_POST(self):
                if self.path != "/token":
                    return self.send_text(404, "Not found")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 8192:
                        raise ValueError("Invalid request size")
                    payload = json.loads(self.rfile.read(length))
                    token = payload.get("access_token", "")
                    state = payload.get("state", "")
                    if (
                        not isinstance(token, str)
                        or not token
                        or state != callback.state
                    ):
                        raise ValueError("Missing access token")
                except (ValueError, json.JSONDecodeError):
                    return self.send_text(400, "Invalid token payload")
                self.send_text(200, "Token received")
                GLib.idle_add(callback.on_token, token)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def read_client_id():
    if client_id := os.environ.get("ANILIST_CLIENT_ID", "").strip():
        return client_id
    config_paths = (
        Path("/etc/ani-gtk/config.ini"),
        Path(__file__).resolve().parent.parent / "config.ini",
    )
    for config_path in config_paths:
        config = configparser.ConfigParser()
        try:
            config.read(config_path)
            client_id = config.get("ANILIST", "client_id", fallback="").strip()
            if client_id:
                return client_id
        except (OSError, configparser.Error):
            continue
    return ""


def prepare_media_entries(media_lists):
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
                            f"{progress}/{episodes}" if episodes > 0 else f"{progress}/?"
                        ),
                        "progress_fraction": fraction,
                        "score": int(entry.get("score", 0) or 0),
                        "format_type": str(media.get("format", "N/A")),
                        "anime_id": str(media.get("id")),
                        "episodes_total": episodes,
                        "watched_episodes": progress,
                    },
                )
            )
    return prepared


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


def texture_from_image_data(image_data, max_width, max_height):
    loader = GdkPixbuf.PixbufLoader.new()
    loader.write(image_data)
    loader.close()
    pixbuf = loader.get_pixbuf()
    source_width = pixbuf.get_width()
    source_height = pixbuf.get_height()
    scale = min(max_width / source_width, max_height / source_height, 1.0)
    if scale < 1.0:
        pixbuf = pixbuf.scale_simple(
            max(1, round(source_width * scale)),
            max(1, round(source_height * scale)),
            GdkPixbuf.InterpType.BILINEAR,
        )
    return Gdk.Texture.new_for_pixbuf(pixbuf)


def banner_texture_from_image_data(image_data, width, height):
    loader = GdkPixbuf.PixbufLoader.new()
    loader.write(image_data)
    loader.close()
    source = loader.get_pixbuf()
    scale = max(width / source.get_width(), height / source.get_height())
    scaled_width = max(width, round(source.get_width() * scale))
    scaled_height = max(height, round(source.get_height() * scale))
    scaled = source.scale_simple(
        scaled_width, scaled_height, GdkPixbuf.InterpType.HYPER
    )
    crop_x = (scaled_width - width) // 2
    crop_y = (scaled_height - height) // 2
    cropped = scaled.new_subpixbuf(crop_x, crop_y, width, height)
    return Gdk.Texture.new_for_pixbuf(cropped)


def session_config_path():
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "ani-gtk" / "session.ini"


def preferences_config_path():
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "ani-gtk" / "settings.ini"


def read_video_detection_setting():
    return read_preference("video_detection_enabled", True, "getboolean")


def read_completion_threshold():
    try:
        return max(1, min(read_preference("completion_threshold", 70, "getint"), 100))
    except (TypeError, ValueError):
        return 70


def read_confirm_episode_updates():
    return read_preference("confirm_episode_updates", False, "getboolean")


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