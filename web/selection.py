"""The comparison tray, carried in the querystring from screen to screen.

There is no session and no client-side state: which versions are picked travels
in the URL of every screen. That is what keeps the link shareable and what makes
"+ comparar" work with plain page loads — but it also means every screen that
offers the button has to carry the tray forward, or the next click starts from an
empty selection again.

Two parameter names, on purpose:

``v``  what the page is *about* — the version on the detail screen, the list on
       the comparison screen.
``c``  what the tray is *carrying*, on every other screen.

They collide otherwise: the detail screen already spends ``v`` on the version it
shows, so a tray under the same name would either overwrite it or be overwritten.
"""

from urllib.parse import urlencode

from web import codes

MAX_COMPARED = 4

PARAM = "c"


def from_request(request):
    """The codes picked so far, in the order they were picked."""
    return codes.parse_list(request.GET.get(PARAM), MAX_COMPARED)


def toggled(selected, code):
    """``selected`` without ``code`` if it is there, with it appended if not.

    ``None`` means the code cannot be added because the tray is full — the screen
    shows a dead button instead of a link that would silently drop the click.
    """
    if code in selected:
        return [picked for picked in selected if picked != code]
    if len(selected) >= MAX_COMPARED:
        return None
    return [*selected, code]


def url(path, selected, params=None):
    """``path`` carrying ``selected`` plus ``params``, empty values dropped."""
    fields = {key: value for key, value in (params or {}).items() if value}
    if selected:
        fields[PARAM] = ",".join(selected)
    return f"{path}?{urlencode(fields)}" if fields else path


def context(selected, path=None, params=None):
    """What every screen needs to show the tray and pass it along."""
    joined = ",".join(selected)
    return {
        "selection": joined,
        # Appended to links that leave this screen, so the tray survives.
        "selection_query": urlencode({PARAM: joined}) if selected else "",
        # The comparison screen owns `v`: that is where the tray is cashed in.
        "compare_query": urlencode({"v": joined}) if selected else "",
        "selection_count": len(selected),
        "is_full": len(selected) >= MAX_COMPARED,
        "max_compared": MAX_COMPARED,
        "selection_clear_url": url(path, [], params) if path else "",
    }
