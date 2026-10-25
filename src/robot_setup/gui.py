"""GTK 4 setup wizard for the touchscreen. A thin layer over the same engine as the CLI.

Pages: welcome -> check -> features -> configure -> review -> install -> verify -> finish
(plus remove). Long work runs in a worker thread; the UI is updated with GLib.idle_add.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import checks, engine  # noqa: E402
from .config import (FEATURE_TITLES, PROFILES, Paths, Request, SetupError,  # noqa: E402
                     resolve_features)
from .modules.luna import find_luna_dirs  # noqa: E402
from .state import State  # noqa: E402
from .system import detect_device, device_checks  # noqa: E402

APP_ID = "io.github.darshanadinushal.RobotSetup"
MARKS = {"passed": "\u2714", "failed": "\u2718", "warning": "!", "skipped": "\u2013",
         "pending": "\u2026"}
CSS = b"""
window { font-size: 17px; }
button { min-height: 48px; min-width: 96px; }
.title { font-size: 26px; font-weight: bold; }
.muted { opacity: 0.7; }
.log { font-family: monospace; font-size: 13px; }
"""
OPTIONAL = {
    "voice": "Offline voice commands (microphone)",
    "piper": "Clearer English and Swedish voices, female and male (about 310 MB)",
    "ai": "Online AI conversation (needs an OpenAI API key)",
    "startup": "Start Luna automatically and restart her after a crash",
    "autologin": "Sign in automatically at power-on (anyone with the robot can use it)",
}


def in_background(work, done):
    """Run work() in a thread, then done(result, error) on the UI thread."""
    def target():
        result, error = None, None
        try:
            result = work()
        except SetupError as exc:
            error = str(exc)
        except Exception as exc:  # report the type only; details may be sensitive
            error = f"Unexpected problem: {type(exc).__name__}"

        def finish():
            done(result, error)
            return False
        GLib.idle_add(finish)
    threading.Thread(target=target, daemon=True).start()


def clear(box):
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def label(text, css=None, wrap=True):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap)
    if css:
        widget.add_css_class(css)
    return widget


def vbox(spacing=10):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


class Wizard(Gtk.ApplicationWindow):
    def __init__(self, app, allow_unsupported):
        super().__init__(application=app, title="Robot Setup")
        self.set_default_size(800, 480)
        self.allow_unsupported = allow_unsupported
        self.paths = Paths.for_user()
        self.saved = State(self.paths.state_file).load().data.get("request")
        self.device = None
        self.ctx = None
        self.updating = False
        self.stack = Gtk.Stack(vexpand=True,
                               transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.set_child(self.stack)
        for name in ("welcome", "check", "features", "configure", "review", "install",
                     "verify", "finish", "remove"):
            self.stack.add_named(getattr(self, f"build_{name}")(), name)
        self.show_page("welcome")

    # Helpers
    def page(self, title, body, buttons):
        box = vbox(12)
        for side in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{side}")(20)
        box.append(label(title, "title"))
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(body)
        box.append(scroller)
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        for button in buttons:
            bar.append(button)
        box.append(bar)
        return box

    def button(self, text, handler, primary=False):
        widget = Gtk.Button(label=text)
        widget.connect("clicked", lambda *_: handler())
        if primary:
            widget.add_css_class("suggested-action")
        return widget

    def show_page(self, name):
        self.stack.set_visible_child_name(name)
        hook = getattr(self, f"enter_{name}", None)
        if hook:
            hook()

    def log_line(self, text):
        def append():
            buffer = self.log_view.get_buffer()
            buffer.insert(buffer.get_end_iter(), text + "\n")
            self.log_view.scroll_to_mark(buffer.get_insert(), 0, False, 0, 0)
            return False
        GLib.idle_add(append)

    def saved_context(self):
        return engine.open_context(None, paths=self.paths, device=self.device,
                                   log=self.log_line, allow_unsupported=self.allow_unsupported)

    # Welcome
    def build_welcome(self):
        self.welcome_body = vbox()
        self.welcome_bar = Gtk.Box(spacing=12, halign=Gtk.Align.END)
        box = self.page("Robot Setup", self.welcome_body, [])
        box.remove(box.get_last_child())
        box.append(self.welcome_bar)
        return box

    def enter_welcome(self):
        clear(self.welcome_body)
        clear(self.welcome_bar)
        if self.saved:
            names = ", ".join(FEATURE_TITLES[f] for f in self.saved["features"])
            self.welcome_body.append(label(f"Installed: {names}"))
            self.welcome_body.append(label(f"Luna folder: {self.saved['luna_dir']}", "muted"))
            self.welcome_body.append(label("Add features, repair the setup, or check that "
                                           "everything works after a restart."))
            self.welcome_bar.append(self.button("Remove", lambda: self.show_page("remove")))
            self.welcome_bar.append(self.button("Check", lambda: self.show_page("verify")))
            self.welcome_bar.append(self.button("Add or repair",
                                                lambda: self.show_page("check"), True))
        else:
            self.welcome_body.append(label(
                "This sets up Luna on your Raspberry Pi. It takes about 10 minutes and needs "
                "the internet for downloads."))
            self.welcome_body.append(label(
                "When it is done, Luna runs on her own. Open Robot Setup again any time to "
                "repair the setup or add features.", "muted"))
            self.welcome_bar.append(self.button("Start", lambda: self.show_page("check"), True))

    # System check
    def build_check(self):
        self.check_list = vbox(6)
        self.check_next = self.button("Next", lambda: self.show_page("features"), True)
        return self.page("System check", self.check_list,
                         [self.button("Back", lambda: self.show_page("welcome")),
                          self.check_next])

    def enter_check(self):
        clear(self.check_list)
        self.check_list.append(label("Checking this device..."))
        self.check_next.set_sensitive(False)

        def done(device, error):
            clear(self.check_list)
            if error:
                self.check_list.append(label(error))
                return
            self.device = device
            items = device_checks(device, self.allow_unsupported)
            for item in items:
                detail = f"  ({item.detail})" if item.detail else ""
                self.check_list.append(label(f"{MARKS[item.status]}  {item.label}{detail}"))
            blocked = any(item.blocking for item in items)
            if blocked:
                self.check_list.append(label("This device is not supported.", "title"))
            self.check_next.set_sensitive(not blocked)
        in_background(lambda: detect_device(self.paths), done)

    # Features
    def build_features(self):
        body = vbox()
        body.append(label("Choose a profile. You can change single features below."))
        self.profile_buttons = {}
        group = None
        for name, text in (("minimal", "Minimal: Luna's face only"),
                           ("standard", "Standard (recommended): voice and automatic startup"),
                           ("custom", "Advanced: choose features yourself")):
            radio = Gtk.CheckButton(label=text)
            if group:
                radio.set_group(group)
            group = group or radio
            radio.connect("toggled", self.on_profile, name)
            self.profile_buttons[name] = radio
            body.append(radio)
        body.append(label("Features", "title"))
        body.append(label("\u2714  Luna (always included)"))
        self.feature_buttons = {}
        for name, text in OPTIONAL.items():
            toggle = Gtk.CheckButton(label=f"{FEATURE_TITLES[name]}: {text}")
            toggle.connect("toggled", self.on_feature)
            self.feature_buttons[name] = toggle
            body.append(toggle)
        features = self.saved["features"] if self.saved else PROFILES["standard"]
        profile = self.saved["profile"] if self.saved else "standard"
        self.set_features(features)
        self.profile_buttons[profile if profile in PROFILES else "custom"].set_active(True)
        return self.page("Features", body,
                         [self.button("Back", lambda: self.show_page("check")),
                          self.button("Next", lambda: self.show_page("configure"), True)])

    def set_features(self, features):
        self.updating = True
        for name, toggle in self.feature_buttons.items():
            toggle.set_active(name in features)
        self.updating = False

    def on_profile(self, radio, name):
        if radio.get_active() and name in PROFILES and not self.updating:
            self.set_features(PROFILES[name])

    def on_feature(self, toggle):
        if self.updating:
            return
        self.updating = True
        if toggle is self.feature_buttons["autologin"] and toggle.get_active():
            self.feature_buttons["startup"].set_active(True)
        if not self.feature_buttons["startup"].get_active():
            self.feature_buttons["autologin"].set_active(False)
        self.profile_buttons["custom"].set_active(True)
        self.updating = False

    def selected_features(self):
        chosen = [name for name, toggle in self.feature_buttons.items() if toggle.get_active()]
        return resolve_features(("luna", *chosen))

    def selected_profile(self):
        for name, radio in self.profile_buttons.items():
            if radio.get_active():
                return name
        return "custom"

    # Configure
    def build_configure(self):
        body = vbox()
        body.append(label("Luna folder (contains main.py)"))
        row = Gtk.Box(spacing=8)
        self.luna_entry = Gtk.Entry(hexpand=True)
        found = find_luna_dirs(self.paths.home)
        default = (self.saved or {}).get("luna_dir") or str(found[0] if found
                                                              else self.paths.home / "luna")
        self.luna_entry.set_text(default)
        row.append(self.luna_entry)
        row.append(self.button("Browse", self.choose_folder))
        body.append(row)
        self.ai_box = vbox()
        self.ai_box.append(label("Online AI", "title"))
        self.ai_box.append(label("OpenAI API key (saved privately in ~/.luna.env). "
                                 "Leave empty to keep the saved key."))
        self.key_entry = Gtk.PasswordEntry(show_peek_icon=True)
        self.ai_box.append(self.key_entry)
        self.ai_box.append(label("AI model name"))
        self.model_entry = Gtk.Entry()
        self.model_entry.set_text((self.saved or {}).get("ai_model", ""))
        self.ai_box.append(self.model_entry)
        self.validate_button = Gtk.CheckButton(label="Check the key and model online")
        self.validate_button.set_active((self.saved or {}).get("validate_ai", True))
        self.ai_box.append(self.validate_button)
        body.append(self.ai_box)
        return self.page("Settings", body,
                         [self.button("Back", lambda: self.show_page("features")),
                          self.button("Next", lambda: self.show_page("review"), True)])

    def enter_configure(self):
        self.ai_box.set_visible("ai" in self.selected_features())

    def choose_folder(self):
        dialog = Gtk.FileDialog(title="Choose the Luna folder")

        def chosen(source, result):
            try:
                folder = source.select_folder_finish(result)
            except GLib.Error:
                return
            if folder is not None:
                self.luna_entry.set_text(folder.get_path())
        dialog.select_folder(self, None, chosen)

    # Review
    def build_review(self):
        self.review_label = label("", wrap=True)
        self.review_label.set_selectable(True)
        self.install_button = self.button("Install", lambda: self.show_page("install"), True)
        return self.page("Review changes", self.review_label,
                         [self.button("Back", lambda: self.show_page("configure")),
                          self.install_button])

    def enter_review(self):
        self.review_label.set_text("Preparing the plan...")
        self.install_button.set_sensitive(False)
        features = self.selected_features()
        request = Request(features=features,
                          luna_dir=str(Path(self.luna_entry.get_text().strip()).expanduser()),
                          profile=self.selected_profile(),
                          ai_model=self.model_entry.get_text().strip() if "ai" in features else "",
                          validate_ai=self.validate_button.get_active())
        secrets = {}
        if "ai" in features and self.key_entry.get_text().strip():
            secrets["openai_api_key"] = self.key_entry.get_text().strip()

        def work():
            ctx = engine.open_context(request, paths=self.paths, device=self.device,
                                      log=self.log_line, secrets=secrets,
                                      allow_unsupported=self.allow_unsupported)
            return ctx, engine.build_plan(ctx)

        def done(result, error):
            if error:
                self.review_label.set_text(error)
                return
            self.ctx, plan = result
            self.review_label.set_text("\n".join(engine.describe_plan(self.ctx, plan)))
            self.install_button.set_label("Install" if plan.pending else "Continue")
            self.install_button.set_sensitive(not plan.blockers)
        in_background(work, done)

    # Install
    def build_install(self):
        body = vbox()
        self.install_status = label("")
        self.spinner = Gtk.Spinner()
        body.append(self.spinner)
        body.append(self.install_status)
        self.log_view = Gtk.TextView(editable=False, cursor_visible=False, vexpand=True,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.log_view.add_css_class("log")
        body.append(self.log_view)
        self.retry_button = self.button("Try again", lambda: self.enter_install())
        self.after_install = self.button("Next", lambda: self.show_page("verify"), True)
        return self.page("Installing", body, [self.retry_button, self.after_install])

    def enter_install(self):
        self.retry_button.set_visible(False)
        self.after_install.set_sensitive(False)
        self.spinner.start()
        self.install_status.set_text("Working. This can take several minutes...")
        ctx = self.ctx

        def work():
            outcome = engine.install(ctx)
            results = engine.run_verify(ctx) if outcome.ok else []
            return outcome, results

        def done(result, error):
            self.spinner.stop()
            if error or not result[0].ok:
                self.install_status.set_text(error or result[0].message)
                self.retry_button.set_visible(True)
                return
            ctx.secrets.clear()
            self.key_entry.set_text("")
            self.saved = ctx.request.to_dict()
            outcome, results = result
            self.install_status.set_text(outcome.message)
            for item in results:
                detail = f" ({item.detail})" if item.detail else ""
                self.log_line(f"{MARKS[item.status]} {item.label}{detail}")
            self.after_install.set_sensitive(True)
        in_background(work, done)

    # Verify
    def build_verify(self):
        self.verify_body = vbox(14)
        return self.page("Check that everything works", self.verify_body,
                         [self.button("Finish", lambda: self.show_page("finish"), True)])

    def enter_verify(self):
        clear(self.verify_body)
        try:
            self.ctx = self.saved_context() if self.device else None
        except SetupError as error:
            self.verify_body.append(label(str(error)))
            return
        if self.ctx is None:
            self.verify_body.append(label("Checking this device..."))

            def found(device, error):
                if device:
                    self.device = device
                    self.enter_verify()
            in_background(lambda: detect_device(self.paths), found)
            return
        automatic = vbox(4)
        automatic.append(label("Automatic checks...", "muted"))
        self.verify_body.append(automatic)

        def shown(results, error):
            clear(automatic)
            for item in results or []:
                detail = f" ({item.detail})" if item.detail else ""
                automatic.append(label(f"{MARKS[item.status]}  {item.label}{detail}"))
            if error:
                automatic.append(label(error))
        in_background(lambda: engine.run_verify(self.ctx), shown)
        for check in checks.applicable(self.ctx):
            self.verify_body.append(self.user_check_row(check))

    def user_check_row(self, check):
        frame = vbox(6)
        frame.append(label(check.question, "title"))
        frame.append(label(check.instructions, "muted"))
        status = label("")
        row = Gtk.Box(spacing=8)
        if check.test:
            def test():
                status.set_text("Testing...")
                in_background(lambda: check.test(self.ctx),
                              lambda message, error: status.set_text(error or message))
            row.append(self.button("Test", test))
        for answer, text in (("yes", "Yes"), ("no", "No"), ("skip", "Skip")):
            def record(answer=answer, text=text):
                checks.record_answer(self.ctx, check, answer)
                status.set_text(f"Saved: {text}")
            row.append(self.button(text, record))
        frame.append(row)
        frame.append(status)
        return frame

    # Finish
    def build_finish(self):
        body = vbox()
        body.append(label("Setup is complete. Luna runs on her own; Robot Setup is no longer "
                          "needed until you want to repair or add something."))
        body.append(label("If you chose automatic startup, restart now. Afterwards open Robot "
                          "Setup and press Check to confirm Luna started by herself.", "muted"))
        return self.page("Done", body,
                         [self.button("Restart now", self.restart),
                          self.button("Close", self.close, True)])

    def restart(self):
        subprocess.Popen(["systemctl", "reboot"])

    # Remove
    def build_remove(self):
        self.remove_label = label(
            "This removes Luna's automatic startup, and turns auto-login off if Robot Setup "
            "turned it on. Luna, her settings, your API key, the voice models and installed "
            "packages are kept.")
        self.remove_button = self.button("Remove", self.do_remove, True)
        return self.page("Remove setup", self.remove_label,
                         [self.button("Back", lambda: self.show_page("welcome")),
                          self.remove_button])

    def do_remove(self):
        self.remove_button.set_sensitive(False)
        self.remove_label.set_text("Removing...")

        def work():
            device = self.device or detect_device(self.paths, check_online=False)
            self.device = device
            return engine.remove_all(self.saved_context())

        def done(lines, error):
            self.remove_label.set_text(error or "\n".join(lines))
            if not error:
                self.saved = None
        in_background(work, done)


def run(allow_unsupported=False):
    app = Gtk.Application(application_id=APP_ID)

    def activate(application):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Wizard(application, allow_unsupported).present()

    app.connect("activate", activate)
    return app.run([])
