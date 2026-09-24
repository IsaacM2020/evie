"""App cards: what the planner needs to know about an app to plan a whole task in ONE go.

A card is a short guide: direct web addresses, keyboard shortcuts, where things are, and named
ACTIONS for apps that have a proper scripting interface (Finder, Notes, Mail, Settings). The
planner can only name an action and give its arguments; the AppleScript itself is a fixed template
here, and every argument is quoted into it, so a model can never write its own script. Actions that
delete or send are marked risky: read back, 3 s to say stop.
"""
import re
from dataclasses import dataclass


def q(s: object) -> str:
    """An AppleScript string literal (same escaping as the app's AppleRun.quote)."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _path(p: str) -> str:
    """A POSIX path with ~ expanded by AppleScript itself."""
    p = str(p).strip()
    return f'(POSIX path of (path to home folder)) & {q(p[2:])}' if p.startswith("~/") else q(p)


@dataclass(frozen=True)
class ActionSpec:
    args: tuple[str, ...]
    template: str  # a Python format string over quoted args
    risky: bool = False
    returns: bool = False  # the script's result is what she reads out
    say: str = ""


@dataclass(frozen=True)
class Action:
    name: str
    script: str
    risky: bool
    returns: bool
    say: str


ACTIONS: dict[str, ActionSpec] = {
    "notes_new": ActionSpec(("title", "body"),
                            'tell application "Notes" to make new note with properties {{name:{title}, body:{body}}}',
                            say="New note made."),
    "notes_latest": ActionSpec((), 'tell application "Notes" to return (name of note 1) & ": " & (plaintext of note 1)',
                               returns=True),
    "finder_open": ActionSpec(("path",), 'tell application "Finder"\nopen (POSIX file ({path}) as alias)\nactivate\nend tell',
                              say="Opened it."),
    "finder_reveal": ActionSpec(("path",), 'tell application "Finder"\nreveal (POSIX file ({path}) as alias)\nactivate\nend tell',
                                say="There it is."),
    "finder_new_folder": ActionSpec(("where", "name"),
                                    'tell application "Finder" to make new folder at (POSIX file ({where}) as alias) '
                                    'with properties {{name:{name}}}', say="Folder made."),
    "finder_find": ActionSpec(("name",), 'do shell script "mdfind -name " & quoted form of {name} & " | head -5"',
                              returns=True),
    "finder_trash": ActionSpec(("path",), 'tell application "Finder" to delete (POSIX file ({path}) as alias)',
                               risky=True, say="Moved it to the Bin."),
    "wifi": ActionSpec(("on",), 'do shell script "networksetup -setairportpower en0 {on}"', say="Done."),
    "dark_mode": ActionSpec(("on",), 'tell application "System Events" to tell appearance preferences to set dark mode to {on}',
                            say="Done."),
    "brightness": ActionSpec(("direction", "steps"),
                             'tell application "System Events"\nrepeat {steps} times\nkey code {direction}\nend repeat\nend tell',
                             say="Done."),
    "settings_open": ActionSpec(("pane",), 'open location "x-apple.systempreferences:" & {pane}', say="Opened it."),
    "mail_unread": ActionSpec((), 'tell application "Mail"\nset out to ""\nset ms to (messages of inbox whose read status is false)\n'
                                  'repeat with m in items 1 thru (min(5, count of ms)) of ms\n'
                                  'set out to out & (sender of m) & ": " & (subject of m) & linefeed\nend repeat\nreturn out\nend tell',
                              returns=True),
    "mail_draft": ActionSpec(("to", "subject", "body"),
                             'tell application "Mail"\nset m to make new outgoing message with properties '
                             '{{subject:{subject}, content:{body}, visible:true}}\n'
                             'tell m to make new to recipient with properties {{address:{to}}}\nactivate\nend tell',
                             say="Draft's open, have a look before you send it."),
}
# AppleScript has no min(): mail_unread uses this helper.
_MIN = "on min(a, b)\nif a < b then return a\nreturn b\nend min\n"


def render_action(name: str, args: dict) -> Action:
    spec = ACTIONS[name]  # KeyError: not an action Evie has
    vals = {}
    for a in spec.args:
        if a not in args or args[a] in (None, ""):
            raise ValueError(f"{name} needs {a}")
        v = args[a]
        if a in ("on",):
            vals[a] = "true" if v in (True, "true", "on", 1) else "false"
            if name == "wifi":
                vals[a] = "on" if vals[a] == "true" else "off"
        elif a == "direction":
            vals[a] = "144" if str(v).lower() in ("up", "brighter") else "145"
        elif a == "steps":
            vals[a] = str(max(1, min(16, int(v))))
        elif a in ("path", "where"):
            vals[a] = _path(v)
        else:
            vals[a] = q(v)
    script = spec.template.format(**vals)
    if "min(" in script:
        script = _MIN + script
    return Action(name, script, spec.risky, spec.returns, spec.say)


# -- the guides ---------------------------------------------------------------------------------

_WEB = """Safari (web): open pages with open_url (a new tab unless the step says same_tab). Direct addresses beat clicking:
- Google search: https://www.google.com/search?q=<words>
- Any site by name: its real address (bbc.com/news, straitstimes.com, channelnewsasia.com).
Tabs: new tab = key cmd+t, close = key cmd+w, back = key cmd+[, reload = key cmd+r.
To read or summarise a page use read. After a page opens, check it with expect url_contains."""

_YOUTUBE = """YouTube:
- Search: https://www.youtube.com/results?search_query=<words>
- A creator's newest videos: https://www.youtube.com/@<Handle>/videos (the handle is usually the channel name with no
  spaces, e.g. @NetworkChuck, @MrBeast, @veritasium). Videos there are newest first. Then pick among "videos".
- If the handle might be wrong: search the name, then find the channel link (href contains /@), open it + "/videos".
- A specific video: search for it, then pick among "videos" with what Isaac described.
- Playing: k pauses/plays, f fullscreen. If a "Skip" button shows (ads), find and press it."""

_NEWS = """News sites: open the site's front page, then pick among "articles" (long headline links in the main part).
"The most interesting" = pick the one Isaac would care about most (tech, AI, science, Singapore, cricket) and say which.
BBC: https://www.bbc.com/news  CNA: https://www.channelnewsasia.com  Straits Times: https://www.straitstimes.com
Google News: https://news.google.com"""

CARDS: dict[str, str] = {
    "Safari": _WEB,
    "WhatsApp": """WhatsApp (desktop app): sending a message ALWAYS uses the message step (it reads it back, 3 s to stop).
To read a chat: activate WhatsApp, find the chat in the chat list by name and press it, then read.""",
    "Messages": """Messages: sending ALWAYS uses the message step. To read: activate Messages, find the conversation, read.""",
    "Finder": """Finder: use actions. finder_open {path}, finder_reveal {path}, finder_new_folder {where, name},
finder_find {name} (returns matching paths), finder_trash {path} (risky: read back). Paths start with ~/ for home
(~/Desktop, ~/Documents, ~/Downloads). Rename: finder_reveal it, then key return, type the new name, key return.""",
    "Notes": """Notes: actions notes_new {title, body} and notes_latest (reads the newest note). To edit an existing note:
activate Notes, find it in the list by title, press it, then type.""",
    "System Settings": """System Settings / Mac switches: actions wifi {on: true|false}, dark_mode {on}, brightness
{direction: up|down, steps: 1-16}, settings_open {pane} with panes like com.apple.Bluetooth-Settings.extension,
com.apple.Focus-Settings.extension, com.apple.Displays-Settings.extension, com.apple.Sound-Settings.extension.
Do Not Disturb and Bluetooth on/off: settings_open the pane, then find the switch and press it.""",
    "Spotify": """Spotify app: playing/pausing/skipping songs is done by Evie's music skills, not here. In the app:
activate Spotify, key cmd+l focuses search. To like the current song: find the "Add to Liked Songs" button.""",
    "Notion": """Notion: activate Notion, key cmd+p opens search, type the page name, key return opens it.
New page: key cmd+n. Notion is an Electron app: its Accessibility tree shows the page's text and buttons.""",
    "Mail": """Mail: actions mail_unread (latest unread: sender and subject) and mail_draft {to, subject, body}
(opens a draft; Evie never sends mail herself). To read a message: activate Mail, find it in the list, press, read.""",
}

GENERAL = """Any other app: its window is read through the Mac's Accessibility tree (buttons, fields, menus by name).
Menus: menu "File > New". Fields: find the field (typeable) and set_text. Keys: key "cmd+n" etc. Buttons: find + press.
If the app isn't open, activate it first."""


def card_for(app: str, goal: str) -> str:
    parts = [f"== {app} ==", CARDS.get(app, GENERAL)]
    g = goal.lower()
    if app == "Safari":
        if re.search(r"youtube|video|watch|channel|vlog|song video|trailer", g):
            parts.append(_YOUTUBE)
        if re.search(r"news|article|headline|story|bbc|cna|straits|times", g):
            parts.append(_NEWS)
    return "\n".join(parts)
