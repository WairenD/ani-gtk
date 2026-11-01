import gi
import queue
from collections import OrderedDict
from pathlib import Path
import secrets
from threading import Thread
from urllib.parse import unquote, urlencode, urlparse

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from anilist import AniListAPI
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk
from media import (
    CATEGORY_TO_STATUS,
    STATUS_OPTIONS,
    STATUS_TO_CATEGORY,
    prepare_media_entries,
)
from mpris import VIDEO_EXTENSIONS, extract_episode_info, extract_episode_number
from oauth import OAuthCallbackServer, read_client_id
import pyperclip
from preferences import (
    _get_cached_anime_folder,
    _preload_folder_cache,
    clear_saved_token,
    read_anime_folder,
    read_completion_threshold,
    read_confirm_episode_updates,
    read_saved_token,
    read_video_detection_setting,
    save_anime_folder,
    save_anime_video_title,
    save_completion_threshold,
    save_confirm_episode_updates,
    save_token,
    save_video_detection_setting,
)
from titles import clean_video_title, normalize_title
from utils import format_date, interpret_html


ANIME_INFO_CACHE_LIMIT = 24
COVER_TEXTURE_CACHE_LIMIT = 12
COVER_BYTES_CACHE_LIMIT = 4
LIBRARY_INSERT_BATCH_SIZE = 8
LIBRARY_BATCH_INTERVAL_MS = 16


class AnimeItem(GObject.GObject):
    title = GObject.Property(type=str, default="")
    title_aliases = GObject.Property(type=str, default="")
    progress = GObject.Property(type=str, default="")
    progress_fraction = GObject.Property(type=float, default=0.0)
    score = GObject.Property(type=int, default=0)
    status = GObject.Property(type=str, default="PLANNING")
    format_type = GObject.Property(type=str, default="")
    anime_id = GObject.Property(type=str, default="")
    list_entry_id = GObject.Property(type=int, default=0)
    folder_path = GObject.Property(type=str, default="")
    episodes_total = GObject.Property(type=int, default=0)
    episodes_aired = GObject.Property(type=int, default=0)
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
        self._anime_info_cache = OrderedDict()
        self._cover_texture_cache = OrderedDict()
        self._cover_bytes_cache = OrderedDict()
        self._details_request_id = 0
        self._detail_load_source_id = 0
        self._library_insert_generation = 0
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
        self.score_options_model = Gtk.StringList.new(
            [str(score) for score in range(101)]
        )
        self.status_options_model = Gtk.StringList.new(
            [label for _, label in STATUS_OPTIONS]
        )
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
        self.category_sort_models = {}
        categories = [
            "CURRENTLY WATCHING",
            "REWATCHING",
            "COMPLETED",
            "DROPPED",
            "PLAN TO WATCH",
            "PAUSED",
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
            col_view.set_single_click_activate(False)
            col_view.connect(
                "activate",
                lambda _view, position, model=selection_model: self.play_anime(
                    model.get_item(position)
                ),
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

            def compare_folder(item1, item2, *args):
                has_folder_1 = bool(item1.folder_path)
                has_folder_2 = bool(item2.folder_path)
                return (has_folder_1 > has_folder_2) - (has_folder_1 < has_folder_2)

            folder_sorter = Gtk.CustomSorter.new(compare_folder, None)

            columns_data = [
                ("", "folder", 36, False, folder_sorter),
                ("Title", "title", 240, True, title_sorter),
                ("Progress", "progress_fraction", 170, False, progress_sorter),
                ("Status", "status", 130, False, None),
                ("Score", "score", 90, False, score_sorter),
                ("Type", "format_type", 120, False, type_sorter),
            ]

            ui_instance = self
            for col_title, prop, width, expand_val, sorter in columns_data:
                factory = Gtk.SignalListItemFactory()
                if prop == "folder":

                    def setup_folder(factory, item):
                        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
                        icon.set_halign(Gtk.Align.CENTER)
                        item.set_child(icon)

                    def bind_folder(factory, item):
                        icon = item.get_child()
                        previous_anime = getattr(icon, "bound_anime", None)
                        previous_handler = getattr(icon, "folder_handler", None)
                        if previous_anime and previous_handler:
                            previous_anime.disconnect(previous_handler)
                        anime = item.get_item()
                        icon.bound_anime = anime

                        def update_folder_icon(*_args):
                            icon.set_visible(bool(anime.folder_path))
                            icon.set_tooltip_text(anime.folder_path or None)

                        icon.folder_handler = anime.connect(
                            "notify::folder-path", update_folder_icon
                        )
                        update_folder_icon()

                    def unbind_folder(factory, item):
                        icon = item.get_child()
                        anime = getattr(icon, "bound_anime", None)
                        handler = getattr(icon, "folder_handler", None)
                        if anime and handler:
                            anime.disconnect(handler)
                        icon.bound_anime = None
                        icon.folder_handler = None

                    factory.connect("setup", setup_folder)
                    factory.connect("bind", bind_folder)
                    factory.connect("unbind", unbind_folder)

                elif prop == "progress_fraction":

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
                        dropdown = Gtk.DropDown(
                            model=ui_instance.score_options_model,
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
                elif prop == "status":

                    def setup_status(factory, item):
                        dropdown = Gtk.DropDown(
                            model=ui_instance.status_options_model,
                            valign=Gtk.Align.CENTER,
                            hexpand=True,
                        )
                        dropdown.connect(
                            "notify::selected",
                            lambda dropdown_obj, _: ui_instance.on_status_changed(
                                dropdown_obj
                            ),
                        )
                        item.set_child(dropdown)

                    def bind_status(factory, item):
                        dropdown = item.get_child()
                        anime = item.get_item()
                        dropdown.anime = anime
                        if anime:
                            dropdown.is_binding = True
                            dropdown.set_selected(
                                next(
                                    (
                                        index
                                        for index, (status, _) in enumerate(
                                            STATUS_OPTIONS
                                        )
                                        if status == anime.status
                                    ),
                                    0,
                                )
                            )
                            dropdown.is_binding = False

                    factory.connect("setup", setup_status)
                    factory.connect("bind", bind_status)
                else:
                    def setup_label(factory, item, item_prop=prop):
                        label = Gtk.Label(xalign=0, margin_start=10)
                        if item_prop == "title":
                            click = Gtk.GestureClick(button=3)
                            click.connect(
                                "pressed",
                                lambda gesture, _count, x, y, list_item=item: ui_instance.show_anime_context_menu(
                                    label,
                                    list_item.get_item(),
                                    x,
                                    y,
                                ),
                            )
                            label.add_controller(click)
                        item.set_child(label)

                    factory.connect("setup", setup_label)
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
            self.category_sort_models[cat] = (sort_model, col_view)
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

        refresh_action = Gio.SimpleAction.new("refresh", None)
        refresh_action.connect("activate", lambda *_: self.refresh_library())
        self.root.add_action(refresh_action)

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
        application.set_accels_for_action("win.refresh", ["<Primary>r"])
        application.set_accels_for_action(
            "win.shortcuts", ["<Primary>question"]
        )

        self.login_action = login_action
        self.logout_action = logout_action
        self.refresh_action = refresh_action

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
        account_menu.append("Refresh Library", "win.refresh")
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

        self.add_anime_button = Gtk.Button()
        self.add_anime_button.set_icon_name("list-add-symbolic")
        self.add_anime_button.set_tooltip_text("Add anime to your list")
        self.add_anime_button.connect(
            "clicked", lambda *_: self.show_add_anime_dialog()
        )
        titlebar.pack_start(self.add_anime_button)

        self.refresh_button = Gtk.Button()
        self.refresh_button.set_icon_name("view-refresh-symbolic")
        self.refresh_button.set_tooltip_text("Refresh library (Ctrl+R)")
        self.refresh_button.connect("clicked", lambda *_: self.refresh_library())
        titlebar.pack_start(self.refresh_button)

        self.refresh_spinner = Gtk.Spinner()
        self.refresh_spinner.set_size_request(20, 20)
        self.refresh_spinner.set_visible(False)
        titlebar.pack_start(self.refresh_spinner)

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
                title="Refresh library", accelerator="<Primary>r"
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
        refreshing = getattr(self, "refresh_busy", False)
        self.login_button.set_visible(not signed_in)
        self.login_button.set_sensitive(not busy)
        self.add_anime_button.set_visible(signed_in)
        self.add_anime_button.set_sensitive(signed_in and not busy and not refreshing)
        self.logout_button.set_visible(signed_in)
        self.logout_button.set_sensitive(not busy and not refreshing)
        if hasattr(self, "login_action"):
            self.login_action.set_enabled(not signed_in and not busy)
            self.logout_action.set_enabled(signed_in and not busy and not refreshing)
        if hasattr(self, "refresh_action"):
            self.refresh_action.set_enabled(signed_in and not busy and not refreshing)
        if hasattr(self, "refresh_button"):
            self.refresh_button.set_sensitive(signed_in and not busy and not refreshing)
        if not signed_in:
            self.avatar_stack.set_visible_child_name("default")

    def show_add_anime_dialog(self):
        if not self.access_token:
            self.show_error("Not signed in", "Log in to add anime to your list.")
            return

        dialog = Gtk.Dialog(
            title="Add Anime to List", transient_for=self.root, modal=True
        )
        dialog.set_default_size(760, 640)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add to List", Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_entry = Gtk.Entry(placeholder_text="Search anime titles")
        search_entry.set_hexpand(True)
        search_button = Gtk.Button(label="Search")
        search_row.append(search_entry)
        search_row.append(search_button)
        content.append(search_row)

        results_list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            vexpand=True,
            hexpand=True,
        )
        results_list.add_css_class("anime-search-results")
        results_scroll = Gtk.ScrolledWindow(
            vexpand=True,
            hexpand=True,
            min_content_height=360,
        )
        results_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        results_scroll.set_child(results_list)
        content.append(results_scroll)

        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        status_row.append(Gtk.Label(label="Add to", xalign=0))
        status_dropdown = Gtk.DropDown(
            model=self.status_options_model, hexpand=True
        )
        status_dropdown.set_selected(
            next(
                index
                for index, (status, _) in enumerate(STATUS_OPTIONS)
                if status == "PLANNING"
            )
        )
        status_row.append(status_dropdown)
        content.append(status_row)

        status_label = Gtk.Label(label="", xalign=0, wrap=True)
        content.append(status_label)

        self._add_anime_dialog_state = {
            "dialog": dialog,
            "request_id": 0,
            "results": [],
            "search_entry": search_entry,
            "results_list": results_list,
            "status_dropdown": status_dropdown,
            "status_label": status_label,
            "search_button": search_button,
        }
        search_button.connect(
            "clicked", lambda *_: self.search_anime_for_dialog(dialog)
        )
        search_entry.connect(
            "activate", lambda *_: self.search_anime_for_dialog(dialog)
        )
        dialog.connect("response", self.on_add_anime_dialog_response)
        dialog.present()

    def search_anime_for_dialog(self, dialog):
        state = getattr(self, "_add_anime_dialog_state", None)
        if not state or state["dialog"] is not dialog:
            return
        search = state["search_entry"].get_text().strip()
        if len(search) < 2:
            state["status_label"].set_text("Enter at least two characters to search.")
            return

        state["request_id"] += 1
        request_id = state["request_id"]
        state["status_label"].set_text("Searching AniList…")
        state["search_button"].set_sensitive(False)
        api = self.anilist_api

        def search_anime():
            try:
                result = (api.search_anime(search), None)
            except Exception as error:
                result = (None, error)
            GLib.idle_add(
                self.finish_anime_search,
                dialog,
                request_id,
                result,
            )

        Thread(target=search_anime, daemon=True).start()

    def finish_anime_search(self, dialog, request_id, result):
        state = getattr(self, "_add_anime_dialog_state", None)
        if (
            not state
            or state["dialog"] is not dialog
            or state["request_id"] != request_id
        ):
            return GLib.SOURCE_REMOVE
        results, error = result
        state["search_button"].set_sensitive(True)
        if error:
            state["status_label"].set_text(f"Search failed: {error}")
            return GLib.SOURCE_REMOVE
        state["results"] = results or []
        results_list = state["results_list"]
        while child := results_list.get_first_child():
            results_list.remove(child)
        for media in state["results"]:
            title_data = media.get("title", {})
            title = (
                title_data.get("english")
                or title_data.get("romaji")
                or title_data.get("native")
                or "Unknown"
            )
            label = Gtk.Label(
                label=f"{title} - {media.get('format') or 'Anime'}",
                xalign=0,
                margin_start=12,
                margin_end=12,
                margin_top=10,
                margin_bottom=10,
                hexpand=True,
            )
            row_content = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=12,
                margin_start=8,
                margin_end=8,
                margin_top=6,
                margin_bottom=6,
            )
            picture = Gtk.Picture()
            picture.set_size_request(48, 64)
            picture.set_can_shrink(True)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.add_css_class("cover-image")
            row_content.append(picture)
            row_content.append(label)
            row = Gtk.ListBoxRow(child=row_content)
            row.cover_picture = picture
            results_list.append(row)
        if state["results"]:
            results_list.select_row(results_list.get_row_at_index(0))
        state["status_label"].set_text(
            f"{len(state['results'])} results"
            if state["results"]
            else "No matching anime found."
        )
        Thread(
            target=self.load_anime_search_covers,
            args=(dialog, request_id, list(state["results"]), self.anilist_api),
            daemon=True,
        ).start()
        return GLib.SOURCE_REMOVE

    def load_anime_search_covers(self, dialog, request_id, results, api):
        for index, media in enumerate(results):
            cover_url = media.get("coverImage", {}).get("medium")
            if not cover_url:
                continue
            try:
                image_bytes = api.download(cover_url)
            except Exception:
                continue
            GLib.idle_add(
                self.set_anime_search_cover,
                dialog,
                request_id,
                index,
                image_bytes,
            )

    def set_anime_search_cover(self, dialog, request_id, index, image_bytes):
        state = getattr(self, "_add_anime_dialog_state", None)
        if (
            not state
            or state["dialog"] is not dialog
            or state["request_id"] != request_id
            or index >= len(state["results"])
        ):
            return GLib.SOURCE_REMOVE
        row = state["results_list"].get_row_at_index(index)
        if not row:
            return GLib.SOURCE_REMOVE
        try:
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write(image_bytes)
            loader.close()
            texture = Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
            row.cover_picture.set_paintable(texture)
        except Exception as error:
            print(f"Could not display anime search cover: {error}", flush=True)
        return GLib.SOURCE_REMOVE

    def on_add_anime_dialog_response(self, dialog, response):
        state = getattr(self, "_add_anime_dialog_state", None)
        if not state or state["dialog"] is not dialog:
            dialog.close()
            return
        if response != Gtk.ResponseType.ACCEPT:
            state["request_id"] += 1
            self._add_anime_dialog_state = None
            dialog.close()
            return

        selected_row = state["results_list"].get_selected_row()
        selected = selected_row.get_index() if selected_row else -1
        if selected < 0 or selected >= len(state["results"]):
            state["status_label"].set_text("Search and select an anime first.")
            return
        status_index = state["status_dropdown"].get_selected()
        if status_index >= len(STATUS_OPTIONS):
            state["status_label"].set_text("Select a list status.")
            return

        media = state["results"][selected]
        status = STATUS_OPTIONS[status_index][0]
        state["request_id"] += 1
        request_id = state["request_id"]
        state["status_label"].set_text("Adding anime…")
        state["search_button"].set_sensitive(False)
        api = self.anilist_api

        def add_anime():
            try:
                result = (api.update_status(media["id"], status), None)
            except Exception as error:
                result = (None, error)
            GLib.idle_add(
                self.finish_add_anime,
                dialog,
                request_id,
                media,
                status,
                result,
            )

        Thread(target=add_anime, daemon=True).start()

    def finish_add_anime(self, dialog, request_id, media, status, result):
        state = getattr(self, "_add_anime_dialog_state", None)
        if (
            not state
            or state["dialog"] is not dialog
            or state["request_id"] != request_id
        ):
            return GLib.SOURCE_REMOVE
        saved_entry, error = result
        if error:
            state["search_button"].set_sensitive(True)
            state["status_label"].set_text(f"Could not add anime: {error}")
            return GLib.SOURCE_REMOVE

        anime_id = str(media["id"])
        existing = next(
            (
                store.get_item(index)
                for store in self.category_views.values()
                for index in range(store.get_n_items())
                if store.get_item(index).anime_id == anime_id
            ),
            None,
        )
        if existing:
            existing.list_entry_id = int(saved_entry.get("id") or existing.list_entry_id)
            existing.status = status
            self.move_anime_to_status(existing, status)
        else:
            status_to_list_name = {
                "CURRENT": "Watching",
                "REPEATING": "Rewatching",
                "COMPLETED": "Completed",
                "DROPPED": "Dropped",
                "PLANNING": "Planning",
                "PAUSED": "Paused",
            }
            entries = prepare_media_entries(
                [
                    {
                        "name": status_to_list_name[status],
                        "entries": [
                            {
                                "media": media,
                                "progress": 0,
                                "score": 0,
                                "status": status,
                            }
                        ],
                    }
                ]
            )
            if entries:
                category, item_data = entries[0]
                item_data["list_entry_id"] = int(saved_entry.get("id") or 0)
                item_data["folder_path"] = _get_cached_anime_folder(anime_id)
                self.category_views[category].append(AnimeItem(**item_data))
        self.update_library_summary()
        self._add_anime_dialog_state = None
        dialog.close()
        return GLib.SOURCE_REMOVE

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
                result = (api, viewer, library_entries, None)
            except Exception as error:
                result = (None, None, None, error)
            GLib.idle_add(self.finish_authentication, token, result)

        Thread(target=load_account, daemon=True).start()

    def finish_authentication(self, token, result):
        api, viewer, library_entries, error = result
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

        self._details_request_id += 1
        self.anilist_api = api
        self.access_token = token
        self.current_user = viewer["name"]
        Thread(
            target=self.load_profile_images,
            args=(token, viewer),
            daemon=True,
        ).start()
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
        self._replace_library(library_entries)
        return GLib.SOURCE_REMOVE

    def _replace_library(self, library_entries):
        """Clear and repopulate list models in short GTK-thread batches."""
        _preload_folder_cache()
        self._library_insert_generation += 1
        generation = self._library_insert_generation
        self._library_insert_entries = library_entries
        self._library_insert_index = 0
        self._library_clear_existing = True
        for sort_model, _view in self.category_sort_models.values():
            sort_model.set_sorter(None)
        GLib.timeout_add(
            LIBRARY_BATCH_INTERVAL_MS,
            self._insert_library_batch,
            generation,
        )

    def _insert_library_batch(self, generation):
        if generation != self._library_insert_generation:
            return GLib.SOURCE_REMOVE
        if self._library_clear_existing:
            remaining = LIBRARY_INSERT_BATCH_SIZE
            for store in self.category_views.values():
                remove_count = min(remaining, store.get_n_items())
                if remove_count:
                    store.splice(0, remove_count, [])
                    remaining -= remove_count
                if remaining == 0:
                    break
            has_more_to_clear = any(
                store.get_n_items() for store in self.category_views.values()
            )
            if has_more_to_clear:
                GLib.timeout_add(
                    LIBRARY_BATCH_INTERVAL_MS,
                    self._insert_library_batch,
                    generation,
                )
                return GLib.SOURCE_REMOVE
            self._library_clear_existing = False
            GLib.timeout_add(
                LIBRARY_BATCH_INTERVAL_MS,
                self._insert_library_batch,
                generation,
            )
            return GLib.SOURCE_REMOVE

        entries = self._library_insert_entries
        start = self._library_insert_index
        end = min(start + LIBRARY_INSERT_BATCH_SIZE, len(entries))
        buckets = {category: [] for category in self.category_views}
        for category, item_data in entries[start:end]:
            if category in buckets:
                buckets[category].append(
                    AnimeItem(
                        **item_data,
                        folder_path=_get_cached_anime_folder(item_data["anime_id"]),
                    )
                )
        for category, items in buckets.items():
            if items:
                self.category_views[category].splice(
                    self.category_views[category].get_n_items(), 0, items
                )
        self._library_insert_index = end
        if end < len(entries):
            GLib.timeout_add(
                LIBRARY_BATCH_INTERVAL_MS,
                self._insert_library_batch,
                generation,
            )
            return GLib.SOURCE_REMOVE

        self._library_insert_entries = None
        for sort_model, view in self.category_sort_models.values():
            sort_model.set_sorter(view.get_sorter())
        if getattr(self, "library_population_refresh", False):
            self.library_population_refresh = False
            self.refresh_busy = False
            self.refresh_spinner.stop()
            self.refresh_spinner.set_visible(False)
        self.library_progress_bar.set_visible(False)
        self.auth_spinner.set_visible(True)
        self.auth_spinner.stop()
        self.auth_progress.set_visible(False)
        if getattr(self, "startup_session_loading", False):
            self.show_main_content()
        self.startup_session_loading = False
        self.auth_busy = False
        self.update_library_summary()
        self.update_auth_controls()

    def load_profile_images(self, token, viewer):
        api = self.anilist_api
        image_data = []
        for image_name, url in (
            ("avatar", viewer.get("avatar", {}).get("large")),
            ("banner", viewer.get("bannerImage")),
        ):
            try:
                image_data.append(api.download(url) if url else None)
            except Exception as error:
                print(f"Could not download AniList {image_name}: {error}", flush=True)
                image_data.append(None)
        GLib.idle_add(self.finish_profile_images, token, *image_data)

    def finish_profile_images(self, token, avatar_data, banner_data):
        if token != self.access_token:
            return GLib.SOURCE_REMOVE
        if avatar_data:
            try:
                loader = GdkPixbuf.PixbufLoader.new()
                loader.write(avatar_data)
                loader.close()
                self.avatar_picture.set_paintable(
                    Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
                )
                self.avatar_stack.set_visible_child_name("profile")
            except Exception as error:
                print(f"Could not display AniList avatar: {error}", flush=True)
                self.avatar_picture.set_paintable(None)
                self.avatar_stack.set_visible_child_name("default")
        else:
            self.avatar_picture.set_paintable(None)
            self.avatar_stack.set_visible_child_name("default")
        try:
            self.banner_picture.set_paintable(
                banner_texture_from_image_data(banner_data, 1600, 96)
                if banner_data
                else None
            )
        except Exception as error:
            print(f"Could not display AniList banner: {error}", flush=True)
            self.banner_picture.set_paintable(None)
        return GLib.SOURCE_REMOVE

    def refresh_library(self):
        if not self.access_token:
            self.show_error("Not signed in", "Log in to refresh your library.")
            return
        if getattr(self, "auth_busy", False) or getattr(self, "refresh_busy", False):
            return
        self.refresh_busy = True
        self.refresh_button.set_sensitive(False)
        self.refresh_spinner.set_visible(True)
        self.refresh_spinner.start()
        self.update_auth_controls()
        token = self.access_token

        def do_refresh():
            try:
                api = self.anilist_api
                viewer = api.fetch_viewer()
                media_lists = api.fetch_media_list(viewer["id"])
                library_entries = prepare_media_entries(media_lists)
                result = (library_entries, None)
            except Exception as error:
                result = (None, error)
            GLib.idle_add(self.finish_refresh, token, result)

        Thread(target=do_refresh, daemon=True).start()

    def finish_refresh(self, token, result):
        library_entries, error = result
        if token != self.access_token:
            self.refresh_spinner.stop()
            self.refresh_spinner.set_visible(False)
            self.refresh_busy = False
            self.update_auth_controls()
            return GLib.SOURCE_REMOVE
        if error:
            self.refresh_spinner.stop()
            self.refresh_spinner.set_visible(False)
            self.refresh_busy = False
            self.update_auth_controls()
            self.show_error("Refresh Failed", f"Could not refresh your library: {error}")
            return GLib.SOURCE_REMOVE
        self._details_request_id += 1
        self._anime_info_cache.clear()
        self._cover_texture_cache.clear()
        self._cover_bytes_cache.clear()
        _preload_folder_cache()
        self.refresh_busy = True
        self.refresh_spinner.set_visible(True)
        self.refresh_spinner.start()
        self.refresh_button.set_sensitive(False)
        self.library_population_refresh = True
        self._replace_library(library_entries)
        return GLib.SOURCE_REMOVE

    def load_authenticated_user(self):
        self.authenticate(self.access_token, startup=True)

    def find_watching_anime_match(self, media_title, media_url):
        episode_number = extract_episode_number(media_title) or extract_episode_number(
            media_url
        )
        candidates = [media_title, media_url]
        normalized_candidates = {
            normalize_title(clean_video_title(candidate))
            for candidate in candidates
        }
        matches = []
        
        stores_to_check = [
            self.category_views.get("CURRENTLY WATCHING"),
            self.category_views.get("REWATCHING"),
        ]
        
        for store in stores_to_check:
            if store is None:
                continue
            for index in range(store.get_n_items()):
                anime = store.get_item(index)
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
                            episode_number
                            and alias_tokens[-1].isdigit()
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
            return "unmatched", None, episode_number
        best_length = max(length for length, _anime in matches)
        best_matches = [anime for length, anime in matches if length == best_length]
        if len(best_matches) != 1:
            return "ambiguous", None, episode_number
        return "matched", best_matches[0], episode_number

    def on_local_video_started(self, media_title, media_url):
        match_status, _anime, _episode_number = self.find_watching_anime_match(
            media_title, media_url
        )
        if match_status == "unmatched":
            self.show_unmatched_video_dialog(media_title, media_url)

    def on_local_video_complete(self, media_title, media_url):
        match_status, anime, episode_number = self.find_watching_anime_match(
            media_title, media_url
        )
        if match_status != "matched":
            return True
        return self.queue_episode_progress_update(anime, episode_number, media_url)

    def show_unmatched_video_dialog(self, media_title, media_url):
        anime_options = []
        seen_ids = set()
        for category in ("CURRENTLY WATCHING", "REWATCHING"):
            store = self.category_views.get(category)
            if not store:
                continue
            for index in range(store.get_n_items()):
                anime = store.get_item(index)
                if anime.anime_id not in seen_ids:
                    seen_ids.add(anime.anime_id)
                    anime_options.append(anime)

        if not anime_options:
            self.show_error(
                "No anime to link",
                "Sign in and refresh your AniList library before linking this video.",
            )
            return True

        status_labels = dict(STATUS_OPTIONS)
        options = [
            f"{anime.title} ({status_labels.get(anime.status, anime.status)})"
            for anime in anime_options
        ]
        dialog = Gtk.Dialog(
            title="Link video to anime", transient_for=self.root, modal=True
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Link", Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.append(
            Gtk.Label(
                label=(
                    f"No Watching or Rewatching anime matched this video:\n"
                    f"{Path(unquote(urlparse(media_url).path)).name or media_title}\n\n"
                    "Choose the anime this title belongs to. The title will be "
                    "remembered, and episode progress will update at completion."
                ),
                wrap=True,
                xalign=0,
            )
        )
        dropdown = Gtk.DropDown(model=Gtk.StringList.new(options), hexpand=True)
        content.append(dropdown)
        dialog.connect(
            "response",
            self.on_unmatched_video_response,
            dropdown,
            anime_options,
            media_title,
            media_url,
        )
        dialog.present()
        return True

    def on_unmatched_video_response(
        self, dialog, response, dropdown, anime_options, media_title, media_url
    ):
        if response == Gtk.ResponseType.ACCEPT:
            selected = dropdown.get_selected()
            if selected < len(anime_options):
                anime = anime_options[selected]
                try:
                    alias = save_anime_video_title(anime.anime_id, media_title)
                except OSError as error:
                    self.show_error("Title Link Failed", str(error))
                else:
                    if alias:
                        anime.title_aliases = "\n".join(
                            dict.fromkeys(
                                [
                                    *filter(None, anime.title_aliases.split("\n")),
                                    alias,
                                ]
                            )
                        )
                        watcher = getattr(self, "video_watcher", None)
                        if watcher:
                            watcher.retry_video(media_url)
        dialog.destroy()

    def queue_episode_progress_update(self, anime, episode_number, media_url):
        if not episode_number:
            print(f"Episode update skipped: no episode number for {anime.title}")
            return True
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
        self.set_anime_progress(anime, new_progress)
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
        new_progress = self.set_anime_progress(anime, new_progress)
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
        self._library_insert_generation += 1
        self._library_insert_entries = None
        self.library_population_refresh = False
        for sort_model, view in self.category_sort_models.values():
            sort_model.set_sorter(view.get_sorter())
        self.refresh_busy = False
        self.refresh_spinner.stop()
        self.refresh_spinner.set_visible(False)
        self._details_request_id += 1
        self.access_token = None
        self.current_user = ""
        self.anilist_api.access_token = None
        self._anime_info_cache.clear()
        self._cover_texture_cache.clear()
        self._cover_bytes_cache.clear()
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
        new_watched = self.set_anime_progress(
            anime, anime.watched_episodes + delta
        )
        box.pbar.set_fraction(anime.progress_fraction)
        box.pbar.set_text(anime.progress)
        self.queue_list_update(
            anime, "progress", new_watched, previous, box
        )

    def set_anime_progress(self, anime, watched):
        watched = max(0, int(watched))
        if anime.episodes_total > 0:
            watched = min(watched, anime.episodes_total)
            anime.progress_fraction = watched / anime.episodes_total
            anime.progress = f"{watched}/{anime.episodes_total}"
        elif anime.episodes_aired > 0:
            denominator = max(anime.episodes_aired, watched)
            anime.progress_fraction = min(watched / denominator, 1.0)
            anime.progress = f"{watched}/?"
        else:
            anime.progress_fraction = 0.0
            anime.progress = f"{watched}/?"
        anime.watched_episodes = watched
        return watched

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

    def on_status_changed(self, dropdown):
        if getattr(dropdown, "is_binding", False):
            return
        anime = getattr(dropdown, "anime", None)
        if not anime:
            return
        selected = dropdown.get_selected()
        if selected >= len(STATUS_OPTIONS):
            return
        previous = anime.status
        new_status = STATUS_OPTIONS[selected][0]
        if new_status == previous:
            return
        anime.status = new_status
        self.move_anime_to_status(anime, new_status)
        self.queue_list_update(anime, "status", new_status, previous, dropdown)

    def move_anime_to_status(self, anime, status):
        target_category = STATUS_TO_CATEGORY.get(status)
        if not target_category:
            return
        target_store = self.category_views.get(target_category)
        if not target_store:
            return
        source_store = next(
            (
                store
                for store in self.category_views.values()
                if any(
                    store.get_item(index) is anime
                    for index in range(store.get_n_items())
                )
            ),
            None,
        )
        if source_store is target_store:
            return
        if source_store:
            for index in range(source_store.get_n_items()):
                if source_store.get_item(index) is anime:
                    source_store.remove(index)
                    break
        target_store.append(anime)

    def play_anime(self, anime):
        if not anime:
            return
        folder = read_anime_folder(anime.anime_id)
        if folder and Path(folder).is_dir():
            self.play_next_episode(anime, Path(folder))
            return
        self.choose_anime_folder(anime, play_after_selection=True)

    def choose_anime_folder(self, anime, play_after_selection=False):
        chooser = Gtk.FileChooserNative(
            title=f"Select folder for {anime.title}",
            transient_for=self.root,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            accept_label="Select",
            cancel_label="Cancel",
        )
        chooser.connect(
            "response", self.on_anime_folder_selected, anime, play_after_selection
        )
        chooser.show()

    def open_anime_folder(self, anime):
            if not anime:
                return
            folder = anime.folder_path or read_anime_folder(anime.anime_id)
            if folder and Path(folder).is_dir():
                try:
                    Gio.AppInfo.launch_default_for_uri(Path(folder).as_uri(), None)
                except GLib.Error as error:
                    self.show_error("Folder Error", str(error))
            else:
                self.choose_anime_folder(anime, play_after_selection=False)

    def on_anime_folder_selected(
        self, chooser, response, anime, play_after_selection
    ):
        if response == Gtk.ResponseType.ACCEPT:
            selected = chooser.get_file()
            folder = selected.get_path() if selected else None
            if folder:
                save_anime_folder(anime.anime_id, folder)
                anime.folder_path = folder
                if play_after_selection:
                    self.play_next_episode(anime, Path(folder))
        chooser.destroy()

    def clear_anime_folder(self, anime):
        save_anime_folder(anime.anime_id, "")
        anime.folder_path = ""

    def show_anime_context_menu(self, widget, anime, x, y):
        if not anime:
            return
        popover = Gtk.Popover()
        actions = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        for label, callback in (
            (
                "Open anime folder",
                lambda: self.open_anime_folder(anime),
            ),
            (
                "Select anime folder…",
                lambda: self.choose_anime_folder(anime),
            ),
            (
                "Clear anime folder",
                lambda: self.clear_anime_folder(anime),
            ),
            (
                "Remove from AniList",
                lambda: self.confirm_remove_anime(anime),
            ),
            ("Play next episode", lambda: self.play_anime(anime)),
        ):
            button = Gtk.Button(label=label, has_frame=False, hexpand=True)
            if label == "Clear anime folder":
                button.set_sensitive(
                    bool(anime.folder_path or read_anime_folder(anime.anime_id))
                )
            button.connect("clicked", lambda _button, run=callback: run())
            actions.append(button)
        popover.set_child(actions)
        popover.set_parent(widget)
        pointing = Gdk.Rectangle()
        pointing.x = int(x)
        pointing.y = int(y)
        pointing.width = 1
        pointing.height = 1
        popover.set_pointing_to(pointing)
        popover.connect("closed", lambda current: current.unparent())
        popover.popup()

    def confirm_remove_anime(self, anime):
        if not anime.list_entry_id:
            self.show_error(
                "Cannot remove anime",
                "This entry has no AniList list ID. Refresh your library and try again.",
            )
            return
        dialog = Gtk.Dialog(
            title="Remove anime from AniList",
            transient_for=self.root,
            modal=True,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Remove", Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_spacing(12)
        content.set_margin_top(16)
        content.set_margin_bottom(16)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.append(
            Gtk.Label(
                label=f"Remove {anime.title} from your AniList library?",
                wrap=True,
                xalign=0,
            )
        )
        dialog.connect(
            "response", self.on_remove_anime_response, anime
        )
        dialog.present()

    def on_remove_anime_response(self, dialog, response, anime):
        if response != Gtk.ResponseType.ACCEPT:
            dialog.close()
            return
        dialog.set_sensitive(False)

        def delete_entry():
            try:
                result = (self.anilist_api.delete_media_list_entry(anime.list_entry_id), None)
            except Exception as error:
                result = (None, error)
            GLib.idle_add(self.finish_remove_anime, dialog, anime, result)

        Thread(target=delete_entry, daemon=True).start()

    def finish_remove_anime(self, dialog, anime, result):
        deletion, error = result
        dialog.close()
        if error:
            self.show_error("Remove Failed", f"AniList did not remove the anime: {error}")
            return GLib.SOURCE_REMOVE
        if not deletion or not deletion.get("deleted"):
            self.show_error("Remove Failed", "AniList did not delete this list entry.")
            return GLib.SOURCE_REMOVE
        for store in self.category_views.values():
            for index in range(store.get_n_items()):
                if store.get_item(index) is anime:
                    store.remove(index)
                    self.update_library_summary()
                    return GLib.SOURCE_REMOVE
        return GLib.SOURCE_REMOVE

    def play_next_episode(self, anime, folder):
        next_episode = anime.watched_episodes + 1
        Thread(
            target=self.find_episode_file,
            args=(anime, folder, next_episode),
            daemon=True,
        ).start()

    def find_episode_file(self, anime, folder, episode_number):
        numbered_files = []
        try:
            for path in folder.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                episode_info = extract_episode_info(path.name)
                if episode_info:
                    season, number = episode_info
                    numbered_files.append((season or 1, number, path))
        except OSError as error:
            GLib.idle_add(self.show_error, "Folder Scan Error", str(error))
            return
        numbered_files.sort(
            key=lambda item: (item[0], item[1], item[2].name.casefold())
        )
        episode_file = next(
            (path for _season, number, path in numbered_files if number == episode_number),
            None,
        )
        if episode_file is None and any(season > 1 for season, _number, _path in numbered_files):
            if episode_number <= len(numbered_files):
                episode_file = numbered_files[episode_number - 1][2]
        GLib.idle_add(
            self.finish_episode_search, anime, folder, episode_number, episode_file
        )

    def finish_episode_search(self, anime, folder, episode_number, episode_file):
        if not episode_file:
            self.show_error(
                "Episode Not Found",
                f"Could not find episode {episode_number} in {folder}.",
            )
            return GLib.SOURCE_REMOVE
        try:
            Gio.AppInfo.launch_default_for_uri(episode_file.as_uri(), None)
        except GLib.Error as error:
            self.show_error("Playback Error", str(error))
        return GLib.SOURCE_REMOVE

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
                api = self.anilist_api
                if field == "progress":
                    api.update_progress(media_id, value)
                elif field == "status":
                    api.update_status(media_id, value)
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
            self.set_anime_progress(anime, value)
            if getattr(widget, "anime", None) is anime:
                widget.pbar.set_fraction(anime.progress_fraction)
                widget.pbar.set_text(anime.progress)
        elif field == "status":
            anime.status = value
            self.move_anime_to_status(anime, value)
            if getattr(widget, "anime", None) is anime:
                widget.is_binding = True
                widget.set_selected(
                    next(
                        (
                            index
                            for index, (status, _) in enumerate(STATUS_OPTIONS)
                            if status == value
                        ),
                        0,
                    )
                )
                widget.is_binding = False
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
        self.right_text_view.set_left_margin(16)
        self.right_text_view.set_right_margin(16)
        self.right_text_view.set_top_margin(12)
        self.right_text_view.set_bottom_margin(12)
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
        if self._detail_load_source_id:
            GLib.source_remove(self._detail_load_source_id)
            self._detail_load_source_id = 0
        self._details_request_id += 1
        request_id = self._details_request_id
        if not item or not hasattr(item, "anime_id"):
            return
        anime_id = str(item.anime_id)

        cached_info = self._cache_get(self._anime_info_cache, anime_id)
        if cached_info:
            self._apply_anime_details(cached_info, anime_id)
            cover_url = cached_info.get("coverImage", {}).get("large", "")
            if cover_url:
                cached_texture = self._cache_get(
                    self._cover_texture_cache, cover_url
                )
                cached_bytes = self._cache_get(self._cover_bytes_cache, cover_url)
                if not cached_texture and not cached_bytes:
                    Thread(
                        target=self._fetch_cached_cover,
                        args=(
                            request_id,
                            anime_id,
                            cached_info,
                            cover_url,
                            self.anilist_api,
                        ),
                        daemon=True,
                    ).start()
            return

        self.metadata_label.set_markup(
            "<span size='large'>Loading...</span>"
        )
        self.set_details_text("")
        self.right_cover_picture.set_paintable(None)

        self._detail_load_source_id = GLib.timeout_add(
            250,
            self._start_anime_details_fetch,
            request_id,
            anime_id,
            self.anilist_api,
        )

    def _start_anime_details_fetch(self, request_id, anime_id, api):
        self._detail_load_source_id = 0

        def fetch():
            if request_id != self._details_request_id:
                return
            try:
                info = api.fetch_anime_info(anime_id)
                if request_id != self._details_request_id:
                    return
                cover_url = info.get("coverImage", {}).get("large", "")
                cover_bytes = None
                if cover_url:
                    try:
                        cover_bytes = api.download(cover_url)
                    except Exception:
                        cover_bytes = None
                if request_id != self._details_request_id:
                    return
                GLib.idle_add(
                    self._finish_anime_details,
                    request_id,
                    anime_id,
                    info,
                    cover_url,
                    cover_bytes,
                    None,
                )
            except Exception as error:
                GLib.idle_add(
                    self._finish_anime_details,
                    request_id,
                    anime_id,
                    None,
                    None,
                    None,
                    error,
                )

        if request_id == self._details_request_id:
            Thread(target=fetch, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _fetch_cached_cover(self, request_id, anime_id, info, cover_url, api):
        if request_id != self._details_request_id:
            return
        try:
            cover_bytes = api.download(cover_url)
        except Exception as error:
            print(f"Could not reload anime cover: {error}", flush=True)
            return
        if request_id == self._details_request_id:
            GLib.idle_add(
                self._finish_anime_details,
                request_id,
                anime_id,
                info,
                cover_url,
                cover_bytes,
                None,
            )

    @staticmethod
    def _cache_get(cache, key):
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value

    @staticmethod
    def _cache_set(cache, key, value, limit):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)

    def _finish_anime_details(
        self, request_id, anime_id, info, cover_url, cover_bytes, error
    ):
        if request_id != self._details_request_id:
            return GLib.SOURCE_REMOVE
        if error or not info:
            self.metadata_label.set_markup(
                "<span size='large'>Failed to load details</span>"
            )
            self.set_details_text(str(error) if error else "")
            return GLib.SOURCE_REMOVE
        self._cache_set(
            self._anime_info_cache, anime_id, info, ANIME_INFO_CACHE_LIMIT
        )
        if cover_url and cover_bytes:
            self._cache_set(
                self._cover_bytes_cache,
                cover_url,
                cover_bytes,
                COVER_BYTES_CACHE_LIMIT,
            )
        self._apply_anime_details(info, anime_id)
        return GLib.SOURCE_REMOVE

    def _apply_anime_details(self, info, anime_id):
        cover_url = info.get("coverImage", {}).get("large", "")
        if cover_url:
            cached_texture = self._cache_get(
                self._cover_texture_cache, cover_url
            )
            if cached_texture:
                self.right_cover_picture.set_paintable(cached_texture)
            else:
                cover_bytes = self._cache_get(
                    self._cover_bytes_cache, cover_url
                )
                if cover_bytes:
                    try:
                        loader = GdkPixbuf.PixbufLoader.new()
                        loader.write(cover_bytes)
                        loader.close()
                        texture = Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
                        self._cache_set(
                            self._cover_texture_cache,
                            cover_url,
                            texture,
                            COVER_TEXTURE_CACHE_LIMIT,
                        )
                        self.right_cover_picture.set_paintable(texture)
                    except Exception:
                        self.right_cover_picture.set_paintable(None)
                else:
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
            f"<span size='xx-large' weight='bold'>{GLib.markup_escape_text(title)}</span>\n\n"
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
