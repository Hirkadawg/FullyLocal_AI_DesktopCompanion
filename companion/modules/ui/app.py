"""Assembles the GUI: window, tray icon, global hotkey, worker thread."""

from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractNativeEventFilter, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.config import AppConfig
from core.logging import get_logger
from core.ratings import Rated, RatingStore
from modules.ui.hotkey import (
    HOTKEY_MOVE_AVATAR,
    HOTKEY_REMARK,
    HOTKEY_TALK,
    HOTKEY_TOGGLE,
    WM_HOTKEY,
    GlobalHotkey,
    HotkeyError,
)
from modules.ui.listen import HandsFreeListener
from modules.ui.talk import PushToTalk
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker

log = get_logger(__name__)


class _HotkeyFilter(QAbstractNativeEventFilter):
    """Routes WM_HOTKEY out of the Windows message loop into a callback."""

    def __init__(self, on_pressed) -> None:
        super().__init__()
        self._on_pressed = on_pressed

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                import ctypes
                from ctypes import wintypes

                class MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", wintypes.HWND),
                        ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM),
                        ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD),
                        ("pt_x", wintypes.LONG),
                        ("pt_y", wintypes.LONG),
                    ]

                msg = MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    # wParam carries the id, so one filter serves both hotkeys.
                    self._on_pressed(int(msg.wParam))
                    return True, 0
            except Exception:  # pragma: no cover
                log.debug("hotkey filter error", exc_info=True)
        return False, 0


def _save_settings(config: AppConfig, base: AppConfig) -> OSError | None:
    """Save what differs from config.yaml to settings.yaml; the error, if any."""
    from core.settings import SettingsStore, overlay

    try:
        SettingsStore(config.root / config.settings_file).save(overlay(config, base))
    except OSError as exc:
        log.warning("could not save settings", exc_info=True)
        return exc
    return None




class CompanionApp:
    """Owns the Qt application and wires the pieces together."""

    def __init__(self, config: AppConfig, image_path: str | None = None) -> None:
        self.config = config
        self.qt = QApplication(sys.argv)
        self.qt.setQuitOnLastWindowClosed(False)  # closing the window ≠ quitting

        self.window = ChatWindow(config)
        self.worker = CompanionWorker(config, image_path=image_path)
        from modules.ui.icon import app_icon

        self.icon = app_icon()
        # Every window's icon too -- the settings page and message boxes had none.
        self.qt.setWindowIcon(self.icon)
        self.hotkey: GlobalHotkey | None = None
        self.talk_hotkey: GlobalHotkey | None = None
        self.remark_hotkey: GlobalHotkey | None = None
        self.move_hotkey: GlobalHotkey | None = None
        self.ratings = (
            RatingStore(config.root / config.ratings.file)
            if config.ratings.enabled
            else None
        )
        self._quiet_action = None
        #: The Live2D avatar's window, when shown (modules/ui/avatar.py).
        self.avatar = None
        self._avatar_move_action = None
        self._avatar_hide_action = None
        #: While the chat window is hidden: where the avatar stood before it
        #: glided into the window's place, and where it glided to.
        self._avatar_home = None
        self._avatar_slid_to = None
        #: The relationship level last shown (core/relationship.py), to notice a new one.
        self._level = None
        #: Mic-button previews: which recording they belong to, and whether one
        #: is being transcribed (only one at a time, so they never pile up).
        self._preview_session = 0
        self._preview_waiting = False
        self.talk: PushToTalk | None = None
        self.listener: HandsFreeListener | None = None
        #: The unplugged microphone the window last said was missing, so it is
        #: said once, not at every recording.
        self._missing_microphone: str | None = None
        self._idle_status = "starting…"

        self._wire_window()
        self._wire_worker()
        self._build_tray()

    def _wire_window(self) -> None:
        self.window.ask_requested.connect(self._on_ask)
        self.window.cancel_requested.connect(self.worker.cancel)
        self.window.escape_pressed.connect(self._on_escape)
        self.window.quiet_toggled.connect(self._on_toggle_mute)
        self.window.remark_requested.connect(self._on_remark_now)
        self.window.rated.connect(self._on_rated)
        self.window.mic_clicked.connect(self._on_mic_clicked)
        self.window.listen_toggled.connect(self._set_listening)
        self.window.settings_requested.connect(self._open_settings)
        self.window.language_changed.connect(self._on_language_changed)
        self.window.clear_requested.connect(self._on_clear)
        self.window.hide_requested.connect(self.window.hide)
        self.window.hidden.connect(self._on_window_hidden)
        self.window.shown.connect(self._on_window_shown)
        self.window.quit_requested.connect(self.quit)

    def _wire_worker(self) -> None:
        self.worker.ready.connect(self._on_ready)
        self.worker.watching_screen.connect(self._on_watching_screen)
        self.worker.thinking.connect(lambda: self.window.set_status("thinking…"))
        self.worker.observed.connect(self.window.set_status)
        self.worker.chunk.connect(self.window.add_chunk)
        # Only refresh the idle status line: overwriting mid-answer would hide
        # what the answer was actually grounded in.
        self.worker.watching.connect(self._on_watching)
        self.worker.voice_unavailable.connect(
            lambda msg: self.window.add_notice(
                f"Voice unavailable, continuing silently. {msg}", "#e6a15c"
            )
        )
        self.worker.failed.connect(lambda msg: self.window.add_notice(msg))
        # Note: NOT finished_answer -> set_answering(False). That fires when the
        # tokens stop, while speech is still playing, which used to leave Esc
        # meaning "hide the window" for the whole time it was talking.
        self.worker.busy_changed.connect(self._on_busy_changed)
        self.worker.heard.connect(self._on_heard)
        self.worker.timer_fired.connect(self._on_timer_fired)
        self.worker.remarked.connect(self._on_remark)
        self.worker.remark_declined.connect(self._on_remark_declined)
        self.worker.answered.connect(self._on_answered)
        self.worker.previewed.connect(self._on_previewed)
        self.worker.mood.connect(self._on_mood)
        self.worker.speech_unavailable.connect(
            lambda msg: self.window.add_notice(
                f"Push-to-talk unavailable, typing still works. {msg}", "#e6a15c"
            )
        )

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("Companion")
        menu = QMenu()

        show = QAction("Show / hide", menu)
        show.triggered.connect(self.toggle)
        menu.addAction(show)

        clear = QAction("Clear conversation", menu)
        clear.triggered.connect(self._on_clear)
        menu.addAction(clear)

        remark_now = QAction("Say something about this", menu)
        remark_now.triggered.connect(self._on_remark_now)
        menu.addAction(remark_now)

        settings = QAction("Settings…", menu)
        settings.triggered.connect(self._open_settings)
        menu.addAction(settings)

        # Clicks pass through the avatar, so moving it is a mode: tick, drag,
        # untick. Shown only while there is an avatar.
        self._avatar_move_action = QAction("Move avatar (drag it; the wheel resizes it)", menu)
        self._avatar_move_action.setCheckable(True)
        self._avatar_move_action.toggled.connect(self._set_avatar_moving)
        self._avatar_move_action.setVisible(False)
        menu.addAction(self._avatar_move_action)

        # Out of sight but still loaded; the "Hide the avatar" setting, and
        # remembered like it.
        self._avatar_hide_action = QAction("Hide avatar", menu)
        self._avatar_hide_action.setCheckable(True)
        self._avatar_hide_action.setChecked(self.config.avatar.hidden)
        self._avatar_hide_action.toggled.connect(self._set_avatar_hidden)
        self._avatar_hide_action.setVisible(False)
        menu.addAction(self._avatar_hide_action)

        # Also in the window header, since this one lives in the tray's
        # RIGHT-click menu and was reported as impossible to find. Built either
        # way and shown with remarks, since the settings page can switch them on.
        self._quiet_action = QAction("Quiet (no unprompted remarks)", menu)
        self._quiet_action.setCheckable(True)
        self._quiet_action.toggled.connect(self._on_toggle_mute)
        self._quiet_action.setVisible(self.config.proactive.enabled)
        menu.addAction(self._quiet_action)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)

        from modules.ui.icon import ON_LIGHT, button_icon

        for action, icon in ((show, "app-window"), (clear, "eraser"), (remark_now, "message-circle"),
                             (settings, "settings"), (self._avatar_move_action, "move"),
                             (self._avatar_hide_action, "eye-off"), (self._quiet_action, "bell-off"),
                             (quit_action, "power")):
            action.setIcon(button_icon(icon, ON_LIGHT))  # the tray's menu is a light one
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    # -- behaviour ------------------------------------------------------------

    def _on_ask(self, question: str) -> None:
        self.window.set_answering(True)
        self.worker.ask(question)

    def _on_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_REMARK:
            self._on_remark_now()
        elif hotkey_id == HOTKEY_MOVE_AVATAR:
            self._toggle_avatar_moving()
        elif hotkey_id == HOTKEY_TALK and self.talk is not None:
            # Talking over the companion is how you interrupt it. Stopping the
            # answer here is also what keeps the microphone from hearing it.
            if self.worker.is_busy():
                self.worker.cancel()
            self.talk.pressed()
        else:
            self.toggle()

    def _open_settings(self) -> None:
        from PySide6.QtWidgets import QDialog

        from core.settings import current_values
        from modules.ui.settings_dialog import SettingsDialog

        base = self._base_config()
        dialog = SettingsDialog(self.config, defaults=current_values(base),
                                on_reset=self.worker.reset_data)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings(dialog.values(), base)
        # Either way: a ratings reset happens at once, saved or not.
        self._refresh_level()

    def _base_config(self) -> AppConfig:
        """config.yaml alone, without the settings page's choices."""
        source = self.config.source or self.config.root / "config.yaml"
        return AppConfig.load(source, settings=False)

    def _apply_settings(self, values: dict, base: AppConfig) -> list:
        """Save the page's values, and take up what can change while running."""
        from core.settings import apply

        changed = apply(self.config, values)
        problem = _save_settings(self.config, base)
        if problem is not None:
            self.window.add_notice(f"Couldn't save the settings: {problem}", "#e06c6c")
        if not changed:
            return changed

        self.worker.apply_settings()
        if any(s.key == "speech.input_device" for s in changed):
            self._use_microphone()
        if self.listener is not None:
            self.listener.detector.pause_s = self.config.speech.pause_s
        if self.config.ratings.enabled and self.ratings is None:
            self.ratings = RatingStore(self.config.root / self.config.ratings.file)
        if self._quiet_action is not None:
            self._quiet_action.setVisible(self.config.proactive.enabled)
        avatar_keys = {s.key for s in changed if s.key.startswith("avatar.")}
        if avatar_keys & {"avatar.enabled", "avatar.folder"}:
            self._drop_avatar()
            self._build_avatar()
        elif avatar_keys and self.avatar is not None:
            self.avatar.apply_settings()
            self._sync_avatar_visibility()
        self.window.apply_settings(
            redock=any(s.key == "monitor_index" for s in changed)
        )
        later = [s.label for s in changed if not s.live]
        self.window.add_notice(
            "Settings saved."
            + (f" After a restart: {', '.join(later)}." if later else ""),
            "#7d8598",
        )
        return changed

    def _use_microphone(self) -> None:
        """The chosen microphone: from the next recording, and straight away for
        hands-free listening that is on."""
        device = self.config.speech.input_device
        if self.talk is not None:
            self.talk.recorder.device = device
        if self.listener is not None:
            self.listener.recorder.device = device
            if self.listener.is_listening:
                self.listener.stop()
                self.listener.start()

    def _tell_missing_microphone(self, recorder) -> None:
        """Say once that the chosen microphone isn't connected and the default
        is listening; again only after it has been found in between."""
        if recorder.missing and recorder.missing != self._missing_microphone:
            self.window.add_notice(
                f"\"{recorder.missing}\" isn't connected, so the system default microphone "
                "is listening. Choose another in Settings > Voice.", "#e06c6c")
        self._missing_microphone = recorder.missing

    # -- the avatar -------------------------------------------------------------

    def _build_avatar(self) -> None:
        """Show the avatar when it is switched on; say why when it can't be."""
        from modules.ui import avatar as body

        if self.avatar is None and self.config.avatar.enabled:
            trouble = body.problem(self.config)
            if trouble:
                log.warning("avatar: %s", trouble)
                self.window.add_notice(f"{trouble} (Settings → Avatar.)", "#e6a15c")
            else:
                path = body.choose_model(body._full(self.config, self.config.avatar.folder),
                                         self.config.avatar.model)
                self.avatar = body.AvatarWindow(self.config, path, level=self.worker.speech_level,
                                                shape=self.worker.speech_shape)
                self.avatar.failed.connect(self._on_avatar_failed)
                self.avatar.resized.connect(self._on_avatar_resized)
                self.avatar.moving_changed.connect(self._on_avatar_moving_changed)
                self.avatar.place()
                if not self.config.avatar.hidden:
                    self.avatar.show()
                log.info("avatar: %s%s", path.name, " (hidden)" if self.config.avatar.hidden else "")
                if not self.window.isVisible():  # started hidden: the room is free
                    self._on_window_hidden()
        self._sync_avatar_action()

    def _on_mood(self, mood: str) -> None:
        """A mood for what is being said (core/mood.py), shown on the avatar's face."""
        if self.avatar is not None:
            self.avatar.show_mood(mood)

    def _drop_avatar(self) -> None:
        if self.avatar is not None:
            self.avatar.dispose()
            self.avatar = None
        self._avatar_home = self._avatar_slid_to = None
        self._sync_avatar_action()

    def _on_avatar_resized(self, height: int) -> None:
        """Resized with + / - on the avatar: saved like the Avatar height setting."""
        problem = _save_settings(self.config, self._base_config())
        if problem is not None:
            self.window.add_notice(f"Couldn't save the settings: {problem}", "#e06c6c")

    def _on_avatar_failed(self, message: str) -> None:
        self.window.add_notice(message, "#e06c6c")
        self._drop_avatar()

    def _toggle_avatar_moving(self) -> None:
        """The move shortcut: move mode on or off, like the tray's Move avatar."""
        if self.avatar is None or self.config.avatar.hidden:
            self.window.add_notice("There's no avatar showing to move.", "#7d8598")
            return
        self._set_avatar_moving(not self.avatar.moving)

    def _on_avatar_moving_changed(self, on: bool) -> None:
        """Move mode started or ended -- by the shortcut, Esc, the tray or hiding
        it: the tray's tick follows, without toggling move mode again."""
        action = self._avatar_move_action
        if action is not None and action.isChecked() != on:
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)

    def _set_avatar_moving(self, on: bool) -> None:
        if self.avatar is not None:
            self.avatar.set_moving(on)

    def _screen_area(self, rect):
        """The usable area of the screen a rectangle is on."""
        from PySide6.QtWidgets import QApplication

        screen = QApplication.screenAt(rect.center()) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _on_window_hidden(self) -> None:
        """The chat window went away: the avatar glides into its place, when set to."""
        from PySide6.QtCore import QRect

        from modules.ui import avatar as body

        avatar = self.avatar
        if avatar is None or not self.config.avatar.take_window_place:
            return
        window = self.window.geometry()
        area = self._screen_area(window)
        here = QRect(avatar.destination(), avatar.size())
        if not area.contains(here.center()):
            return  # on another screen, nowhere near the window's place
        target = body.into_window_place(here, window, area)
        if target != here.topLeft():
            self._avatar_home, self._avatar_slid_to = here.topLeft(), target
            avatar.slide_to(target)

    def _on_window_shown(self) -> None:
        """The chat window is back: the avatar goes home, or steps just clear of it
        if it was moved in between."""
        from PySide6.QtCore import QRect

        from modules.ui import avatar as body

        avatar, home, slid_to = self.avatar, self._avatar_home, self._avatar_slid_to
        self._avatar_home = self._avatar_slid_to = None
        following = self.config.avatar.take_window_place
        # Put in the window's place earlier, it goes back even if the setting has
        # since been switched off: left there, it would cover the window.
        if avatar is None or (slid_to is None and not following):
            return
        here = avatar.destination()
        start = home if slid_to is not None and here == slid_to else here
        target = start
        if following:
            window = self.window.geometry()
            target = body.clear_of_window(QRect(start, avatar.size()), window, self._screen_area(window))
        if target != here:
            avatar.slide_to(target)

    def _set_avatar_hidden(self, hidden: bool) -> None:
        """The tray's "Hide avatar": the same as the setting, and saved like it."""
        if hidden != self.config.avatar.hidden:
            base = self._base_config()
            self.config.avatar.hidden = hidden
            problem = _save_settings(self.config, base)
            if problem is not None:
                self.window.add_notice(f"Couldn't save the settings: {problem}", "#e06c6c")
        self._sync_avatar_visibility()

    def _sync_avatar_visibility(self) -> None:
        """Shown or hidden as avatar.hidden says. Hidden, it stays loaded and its
        drawing timer stops (AvatarWindow.hideEvent), so it costs no frames."""
        if self.avatar is not None:
            if self.config.avatar.hidden:
                self.avatar.set_moving(False)  # saves where it was left
                self.avatar.hide()
            elif not self.avatar.isVisible():
                self.avatar.show()
        self._sync_avatar_action()

    def _sync_avatar_action(self) -> None:
        """The tray's avatar actions: both while there is an avatar, Move only
        while it can be seen."""
        move = self._avatar_move_action
        if move is not None:
            movable = self.avatar is not None and not self.config.avatar.hidden
            move.setVisible(movable)
            if not movable and move.isChecked():
                move.blockSignals(True)
                move.setChecked(False)
                move.blockSignals(False)
        hide = getattr(self, "_avatar_hide_action", None)
        if hide is not None:
            hide.setVisible(self.avatar is not None)
            hide.blockSignals(True)
            hide.setChecked(self.config.avatar.hidden)
            hide.blockSignals(False)

    def _set_listening(self, on: bool) -> None:
        """Hands-free listening on or off, from the window's listen button."""
        if self.listener is None:
            self.window.set_listening(False)
            if on:
                self.window.add_notice("Speech input is unavailable; typing still works.")
            return
        if on == self.listener.is_listening:
            self.window.set_listening(on)
            return
        if on:
            self.listener.start()
        else:
            self.listener.stop()
        listening = self.listener.is_listening
        self.window.set_listening(listening)
        if listening:
            self.window.add_notice(
                "Listening: just talk, and pause when you're done. Click "
                "● listening to stop.", "#7d8598",
            )
        elif not on:
            self.window.add_notice("Stopped listening.", "#7d8598")

    def _listen_suppressed(self) -> bool:
        """Moments the microphone must not be taken as the user speaking."""
        if self.talk is not None and self.talk.is_recording:
            return True  # the talk key or the mic button has the microphone
        if self.config.speech.mic_hears_speakers:
            if self.worker.is_speaking_recently(self.config.speech.echo_tail_s):
                return True
            if self.worker.sound_playing():
                return True
        return False

    def _on_listen_started(self) -> None:
        # Quiet for remarks while they talk, as with the talk key.
        self.worker.note_user_talking()
        self._tell_missing_microphone(self.listener.recorder)
        if not self.config.speech.mic_hears_speakers and self.worker.is_busy():
            # Talking over it is how you interrupt, as with a person. Only
            # when the microphone can't hear the speakers: otherwise this
            # would be the companion interrupting itself.
            self.worker.cancel()
        self.window.set_status("hearing you…")

    def _on_listen_utterance(self, audio) -> None:
        self.window.set_status("transcribing…")
        self.worker.transcribe(audio, quiet=True)

    def _on_listen_failed(self, message: str) -> None:
        self.window.set_listening(False)
        self.window.add_notice(message, "#e06c6c")

    def _on_mic_clicked(self) -> None:
        """The mic button: start recording, or send what has been recorded."""
        if self.talk is None:
            self.window.set_recording(False)
            self.window.add_notice("Speech input is unavailable; typing still works.")
            return
        if not self.talk.is_recording and self.worker.is_busy():
            # Same as the talk key: speaking over it is how you interrupt, and
            # stopping the answer keeps the microphone from recording it.
            self.worker.cancel()
        self.talk.toggle()
        self.window.set_recording(self.talk.is_recording)

    def _on_talk_failed(self, message: str) -> None:
        self._end_preview()
        self.window.set_recording(False)
        self.window.add_notice(message, "#e06c6c")

    def _on_talk_preview(self, audio) -> None:
        if self.talk is None or self.talk.held or self._preview_waiting:
            return
        self._preview_waiting = True
        self.worker.preview(audio, self._preview_session)

    def _on_previewed(self, session: int, text: str) -> None:
        self._preview_waiting = False
        if (
            session == self._preview_session
            and self.talk is not None
            and self.talk.is_recording
            and not self.talk.held
        ):
            self.window.show_preview(text)

    def _end_preview(self) -> None:
        # A new session number makes any preview still being transcribed stale.
        self._preview_session += 1
        self.window.end_preview()

    def _on_talk_started(self) -> None:
        # First, before anything visible: a remark composed while the key is
        # held would talk over them.
        self.worker.note_user_talking()
        self.window.set_recording(True)
        if self.talk is not None:
            self._tell_missing_microphone(self.talk.recorder)
        if not self.window.isVisible():
            self.show()
        if self.talk is not None and not self.talk.held:
            self._preview_session += 1
            self.window.show_preview("")
        self.window.set_status(
            "listening… (release to send)" if self.talk is not None and self.talk.held
            else "listening… (click the red send arrow when done, Esc to discard)"
        )

    def _on_talk_captured(self, audio) -> None:
        self._end_preview()
        self.window.set_recording(False)
        seconds = len(audio) / 16000 if audio is not None else 0.0
        if seconds < self.config.speech.min_seconds:
            self.window.set_status(self._idle_status)
            return
        self.window.set_status(f"transcribing {seconds:.1f}s…")
        self.worker.transcribe(audio)

    def _on_heard(self, text: str) -> None:
        if not text:
            self.window.add_notice("I didn't catch that.", "#7d8598")
            self.window.set_status(self._idle_status)
            return
        self.window.add_question(text)

    def _on_answered(self, info: dict) -> None:
        """Offer a rating of the answer that just finished."""
        self.window.add_rating(Rated(
            kind="answer", reply=info["reply"],
            message=info.get("question", ""), page=info.get("page", ""),
        ))

    def _on_rated(self, item: Rated, rating: str) -> None:
        if item.kind == "remark":
            # Also a verdict on the remark's site, kind and moment.
            self.worker.note_remark_rating(item.page, item.move, item.trigger, rating)
        if self.ratings is None or not self.config.ratings.enabled:
            return
        try:
            self.ratings.rate(item, rating)
        except OSError as exc:
            log.warning("could not save a rating", exc_info=True)
            self.window.add_notice(f"Couldn't save the rating: {exc}", "#e06c6c")
        self._refresh_level(announce=True)

    def _refresh_level(self, announce: bool = False) -> None:
        """Your level with the companion, counted from the ratings file.
        With `announce`, reaching a new level says so in the window."""
        from core.relationship import count_votes, level_for

        settings = self.config.ratings
        if self.ratings is None or not (settings.enabled and settings.show_level):
            self._level = None
            self.window.set_level(None)
            return
        up, down = count_votes(self.ratings.ratings())
        level = level_for(up + down)
        if announce and self._level is not None and level.level > self._level.level:
            self.window.add_notice(
                f"Your level with the companion is now {level.level}, after {level.votes} votes. "
                f"{level.step} more for level {level.level + 1}.", "#7fd4a0")
        self._level = level
        self.window.set_level(level, up, down)

    def _on_remark(self, text: str, why: str = "", remark=None) -> None:
        """Show an unprompted remark, without stealing focus or the screen.

        With `proactive.show_reason`, the reason goes underneath it: proactive
        help disrupts less when people can see why it came (CHI 2025). The
        reason is shown, never spoken -- the speaker only ever gets the remark.
        """
        reason = why.strip() if self.config.proactive.show_reason else ""
        self.window.add_notice(text, "#9db8d6")
        if reason:
            self.window.add_reason(reason)
        if remark is not None:
            self.window.add_rating(Rated(
                kind="remark", reply=text, page=remark.page, move=remark.move,
                why=remark.why, trigger=remark.trigger,
            ))
        # Deliberately does NOT un-hide the window. An unrequested remark
        # popping a window over what you are reading is exactly the behaviour
        # that makes this kind of feature get switched off.
        if not self.window.isVisible():
            message = f"{text}\nWhy: {reason}" if reason else text
            self.tray.showMessage("Companion", message, self.icon, 6000)
        if not self.window.is_answering:
            self.window.set_status(self._idle_status)

    def _on_remark_now(self) -> None:
        """"Say something about this", from the hotkey, the tray or the window.

        Works with unprompted remarks switched off, and while Quiet is on:
        asking for a remark is not being interrupted by one.
        """
        self.window.set_status("looking for something to say…")
        self.worker.remark_now()

    def _on_watching_screen(self, watching: bool) -> None:
        self.window.add_notice(
            "Watching your screen: each remark now comes with a screenshot taken for it and "
            "discarded after. Say \"stop watching\" to end it."
            if watching
            else "Stopped watching your screen: remarks read its text again.",
            "#7d8598",
        )

    def _on_remark_declined(self, message: str) -> None:
        self.window.add_notice(message, "#7d8598")
        self.window.set_status(self._idle_status)
        if not self.window.isVisible():
            self.tray.showMessage("Companion", message, self.icon, 4000)

    def _on_language_changed(self, language: str) -> None:
        self.worker.set_speech_language(language or None)
        self.window.add_notice(
            f"{language.upper()}: it listens for {language.upper()}, replies in it and speaks "
            "with its voice."
            if language
            else "Auto: it listens in either language and replies in the one you use.",
            "#7d8598",
        )

    def _on_toggle_mute(self, checked: bool) -> None:
        self.worker.set_muted(checked)
        # Keep the window button and the tray item showing the same state,
        # whichever one was used.
        self.window.set_quiet(checked)
        if self._quiet_action is not None and self._quiet_action.isChecked() != checked:
            self._quiet_action.blockSignals(True)
            self._quiet_action.setChecked(checked)
            self._quiet_action.blockSignals(False)
        self.window.add_notice(
            "Quiet: no unprompted remarks." if checked
            else "Listening again; may comment on what you're reading.",
            "#7d8598",
        )

    def _on_timer_fired(self, message: str) -> None:
        """Surface a finished timer, even if the window is hidden."""
        self.window.add_notice(message, "#7fd4a0")
        self.tray.showMessage("Companion", message, self.icon, 8000)
        if not self.window.isVisible():
            self.show()

    def _on_escape(self) -> None:
        """Esc stops whatever is running; only hides when nothing is."""
        if self.talk is not None and self.talk.is_recording and not self.talk.held:
            # A recording started with the mic button: throw it away unsent.
            self.talk.stop()
            self._end_preview()
            self.window.set_recording(False)
            self.window.set_status(self._idle_status)
            self.window.add_notice("Recording discarded.", "#7d8598")
        elif self.worker.is_busy():
            self.worker.cancel()
        else:
            self.window.hide()

    def _on_busy_changed(self, busy: bool) -> None:
        self.window.set_answering(busy)
        if not busy:
            self.window.set_status(self._idle_status)

    def _on_watching(self, status: str) -> None:
        if not self.window.is_answering:
            self.window.set_status(status)

    def _on_clear(self) -> None:
        self.worker.clear_memory()
        self.window.clear_transcript()

    def _on_ready(self, error: str) -> None:
        if error:
            self.window.add_notice(error, "#e06c6c")
            self.window.set_status("not ready")
            self.tray.showMessage("Companion", error, self.icon, 10000)
            return
        talk = (
            f" · hold {self.config.speech.hotkey} to talk"
            if self.talk_hotkey is not None
            else ""
        )
        self._idle_status = (
            f"ready · {self.config.ollama.model} · monitor "
            f"{self.config.monitor_index} · {self.config.ui.hotkey} to toggle{talk}"
        )
        self.window.set_status(self._idle_status)

    def toggle(self) -> None:
        if self.window.isVisible():
            self.window.hide()
        else:
            self.show()

    def show(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.focus_input()

    def quit(self) -> None:
        log.info("shutting down")
        if self.talk is not None:
            self.talk.stop()
        if self.listener is not None:
            self.listener.stop()
        for hotkey in (self.hotkey, self.talk_hotkey, self.remark_hotkey, self.move_hotkey):
            if hotkey is not None:
                hotkey.release()
        self.tray.hide()
        self._drop_avatar()
        self.worker.shutdown()
        from modules.ui.avatar import shutdown_live2d

        shutdown_live2d()
        self.qt.quit()

    def run(self) -> int:
        self._refresh_level()
        self.window.show()  # shown once so winId() exists for the hotkey
        hwnd = int(self.window.winId())

        self._filter = _HotkeyFilter(self._on_hotkey)
        self.qt.installNativeEventFilter(self._filter)
        try:
            self.hotkey = GlobalHotkey(hwnd, self.config.ui.hotkey, HOTKEY_TOGGLE)
        except HotkeyError as exc:
            # Not fatal: the tray icon still works, so say so and carry on.
            log.warning("%s", exc)
            self.window.add_notice(
                f"Global hotkey unavailable: {exc}. "
                f"Use the tray icon to show and hide.",
                "#e6a15c",
            )

        if self.config.speech.enabled:
            try:
                self.talk_hotkey = GlobalHotkey(
                    hwnd, self.config.speech.hotkey, HOTKEY_TALK
                )
            except HotkeyError as exc:
                log.warning("%s", exc)
                self.window.add_notice(
                    f"Push-to-talk key unavailable: {exc}. "
                    "The mic button and typing still work.",
                    "#e6a15c",
                )
            # Created either way: without the key, the mic button still records.
            self.talk = PushToTalk(self.config, self.talk_hotkey)
            self.talk.started.connect(self._on_talk_started)
            self.talk.captured.connect(self._on_talk_captured)
            self.talk.failed.connect(self._on_talk_failed)
            self.talk.preview.connect(self._on_talk_preview)

            self.listener = HandsFreeListener(
                self.config, suppressed=self._listen_suppressed
            )
            self.listener.started.connect(self._on_listen_started)
            self.listener.utterance.connect(self._on_listen_utterance)
            self.listener.failed.connect(self._on_listen_failed)
            if self.config.speech.listen_on_start:
                self._set_listening(True)

        if self.config.proactive.remark_now_hotkey:
            try:
                self.remark_hotkey = GlobalHotkey(
                    hwnd, self.config.proactive.remark_now_hotkey, HOTKEY_REMARK
                )
            except HotkeyError as exc:
                log.warning("%s", exc)
                self.window.add_notice(
                    f"'Say something' hotkey unavailable: {exc}. "
                    "The say button still works.",
                    "#e6a15c",
                )

        # Only with an avatar switched on: a global shortcut takes its combination
        # from every other app.
        if self.config.avatar.enabled and self.config.avatar.move_hotkey:
            try:
                self.move_hotkey = GlobalHotkey(
                    hwnd, self.config.avatar.move_hotkey, HOTKEY_MOVE_AVATAR
                )
            except HotkeyError as exc:
                log.warning("%s", exc)
                self.window.add_notice(
                    f"The shortcut to move the avatar is unavailable: {exc}. "
                    "\"Move avatar\" in the tray still works.",
                    "#e6a15c",
                )

        if self.config.ui.start_hidden:
            self.window.hide()
        else:
            self.show()

        self._build_avatar()
        self.worker.start()
        return self.qt.exec()
