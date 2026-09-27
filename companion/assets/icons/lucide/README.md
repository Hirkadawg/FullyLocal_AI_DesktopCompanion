# Lucide icons

The button icons, from [Lucide](https://lucide.dev) — ISC licence, see `LICENSE`.

Downloaded once, with the user's approval, on 16 September 2026 from
`https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/<name>.svg`
(and `LICENSE` from the repository root). 18 files, 276–586 bytes each; checked on
arrival to contain only shapes (`svg`, `path`, `rect`, `circle`) — no scripts,
links or anything fetched. The app reads them from this folder and never goes
online for them.

| Icon | Where |
|---|---|
| mic, send | the mic button; send while recording |
| ear | the listen button |
| message-circle | the say button, and "Say something about this" in the tray |
| bell, bell-off | the quiet button; Quiet in the tray |
| languages | the language button |
| menu | the menu button |
| settings, eraser, eye-off, power | Settings, Clear, Hide, Quit in the menus |
| app-window, move | Show / hide and Move avatar in the tray (eye-off: Hide avatar) |
| folder-open, external-link, rotate-ccw, undo-2 | Choose, Open, Reset and Back to config.yaml on the settings page |

Each is drawn in one colour by `modules/ui/icon.py`: the SVGs stroke in
`currentColor`, which is replaced before drawing. To add one, download it the same
way, name it here, and list it in `tests/test_button_icons.py`.
