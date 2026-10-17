"""The “Integrations” tab of the Qt window: connections on the left, the selected one's
settings and the feed of notifications on the right. The same behaviour as
integrations_view of the Tk window.

GitLab is checked in a background thread every N minutes while the vault is open; the
token is read from a vault entry and never shown. New events go to the feed (kept in the
vault), a counter on the tab and a pop-up in the corner of the screen; the taskbar button
asks for attention (QApplication.alert, on every system)."""

from __future__ import annotations

import time
import webbrowser
from typing import Any

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ...bookmarks import entry_secret
from ...gitlab import INTERVALS, RULES, Event, GitLabError, check as gitlab_check
from ...i18n import _
from ...jobs import JobPool
from .. import icons, theme
from ..dialogs.base import button, label
from ..jobs import Waker
from ..timers import Ticker
from ..widgets.common import IconLabel, scroll_area, section, with_icon
from ..widgets.countlist import count_list
from ..widgets.flow import flow_host
from ..widgets.toast import Toast
from .base import TabPage
from ..errors import guard_events

POLL_TICK_MS = 15_000  # how often the app asks “is a check due?”
FEED_MIN_WIDTH = 380  # px; narrower than settings + this, the notifications go under the settings
FEED_PAGE = 20
EVENT_ICONS = {
    "approved": "✅", "resolved": "💬", "pipeline": "❌", "merged": "🎉",
    "review_requested": "👀", "my_threads_resolved": "✔", "replies": "↩",
}
EVENT_ROLE = Qt.ItemDataRole.UserRole + 4
MORE_ROLE = Qt.ItemDataRole.UserRole + 5


def event_text(ev: Event) -> str:
    try:
        return _(ev.text).format(**ev.args)
    except (KeyError, IndexError, ValueError):
        return ev.text


class GitLabWatcher:
    """The GitLab check through the life of the window: the pool the check runs in, the
    tick that asks whether one is due, the text of the last outcome. Kept through a lock."""

    def __init__(self, shell: Any):
        self.shell = shell
        self._wake = Waker(self._drain, owner=shell)  # the answer is taken as it comes
        self._jobs = JobPool(workers=1, name="gitlab", notify=self._wake)
        self._busy = False
        self.status = ""
        self._ticker = Ticker(shell, POLL_TICK_MS, self._tick)  # is a check due by the clock
        self._ticker.start()

    @property
    def page(self) -> IntegrationsPage | None:
        return self.shell.pages.get("integrations")

    def close(self) -> None:
        self._ticker.stop()
        self._wake.close()
        self._jobs.close()

    def _token(self) -> str:
        vault = self.shell.vault
        entry = vault.entry_by_id(vault.integrations.gitlab.entry_id) if vault else None
        return entry_secret(entry) if entry is not None else ""

    def _tick(self) -> None:
        vault = self.shell.vault
        if vault is None or self._busy or not self.shell.background_jobs or not self.shell.tab_on("integrations"):
            return
        integ = vault.integrations
        gl = integ.gitlab
        if not gl.enabled or not gl.url or not self._token():
            return
        if time.time() - integ.gitlab_checked >= gl.interval_min * 60:
            self.check_now(quiet=True)

    def check_now(self, quiet: bool = False) -> None:
        vault = self.shell.vault
        if vault is None or self._busy:
            return
        gl = vault.integrations.gitlab
        token = self._token()
        if not gl.url or not token:
            self.shell.set_status(_("Fill in the address and pick the entry with the token"))
            return
        self._busy = True
        self.shell.set_status(_("Checking…"))
        settings = type(gl).from_dict(gl.to_dict())  # a copy for the thread
        integ = vault.integrations
        old = dict(integ.gitlab_state)
        self._jobs.submit("check", gitlab_check, gl.url, token, settings, old, None, integ.since(time.time()), quiet=quiet)

    def _drain(self) -> None:
        results = self._jobs.drain()
        if not results:
            return
        self._busy = False
        vault = self.shell.vault
        if vault is None:
            return
        integ = vault.integrations
        result = results[-1]
        if not result.ok:
            exc = result.error
            integ.note_failed_check(time.time())
            self.set_status(_("Check failed: {error}").format(error=_(str(exc) if isinstance(exc, GitLabError) else repr(exc))))
            self.shell.mark_dirty("integrations")
            return
        found = result.value
        first = integ.apply_check(found, time.time())
        if found.events:
            self._notify(found.events)
        self.shell.mark_dirty("integrations")
        if first:
            self.set_status(_("Connected as @{user}. Watching {n} MR(s); changes from now on will be reported.").format(user=integ.gitlab.username, n=len(found.snapshot)))
        else:
            self.set_status(_("Checked at {time}: {n} MR(s), new events: {events}").format(time=time.strftime("%H:%M"), n=len(found.snapshot), events=len(found.events)))
        self.shell.update_integrations_badge()
        page = self.page
        if page is not None:
            page.render_feed()

    def _notify(self, events: list[Event]) -> None:
        for ev in events[:4]:
            Toast(self.shell, f"{EVENT_ICONS.get(ev.kind, '🔔')}  GitLab · {ev.title[:60]}", event_text(ev), ev.url)
        if len(events) > 4:
            Toast(self.shell, "GitLab", _("And {n} more — see the Integrations tab").format(n=len(events) - 4))
        QApplication.alert(self.shell)

    def set_status(self, text: str) -> None:
        self.status = text
        self.shell.set_status(text)
        page = self.page
        if page is not None and page.status_label is not None:
            page.status_label.setText(text)


@guard_events
class EventDelegate(QStyledItemDelegate):
    """A notification: the icon, the text and the MR title wrapped, the time at the right;
    unread ones on the soft accent."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.f_row, self.f_small = theme.font(11), theme.font(9)
        self.fm_row, self.fm_small = QFontMetrics(self.f_row), QFontMetrics(self.f_small)

    def _texts(self, index: QModelIndex | QPersistentModelIndex) -> tuple[Event | None, str]:
        ev = index.data(EVENT_ROLE)
        return ev, str(index.data(MORE_ROLE) or "")

    def _width(self, option: QStyleOptionViewItem) -> int:
        view = self.parent()
        w = option.rect.width()
        if w <= 0 and isinstance(view, QListWidget):
            w = view.viewport().width()
        return max(120, w)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> QSize:
        ev, more = self._texts(index)
        w = self._width(option)
        if ev is None:
            return QSize(w, self.fm_small.height() + 14)
        room = w - 2 * 12 - 26 - self.fm_small.horizontalAdvance("00.00 00:00") - 12
        wrap = int(Qt.TextFlag.TextWordWrap)
        h = self.fm_row.boundingRect(QRect(0, 0, room, 10_000), wrap, event_text(ev)).height()
        h += self.fm_small.boundingRect(QRect(0, 0, room, 10_000), wrap, ev.title).height()
        return QSize(w, h + 20)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex) -> None:
        t = theme.current()
        r = option.rect
        ev, more = self._texts(index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if ev is None:
            painter.setFont(self.f_small)
            painter.setPen(QColor(t.LINK))
            painter.drawText(r.adjusted(4, 0, 0, 0), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), more)
            painter.restore()
            return
        box = QRectF(r).adjusted(0.5, 3.5, -0.5, -3.5)
        hot = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QColor(t.ACCENT if hot else t.BORDER))
        painter.setBrush(QColor(t.TILE if ev.read else t.ACCENT_SOFT))
        painter.drawRoundedRect(box, 10, 10)
        x = r.x() + 12
        painter.setFont(self.f_row)
        icons.paint(painter, EVENT_ICONS.get(ev.kind, "🔔"), QRect(x, r.y() + 10, 18, self.fm_row.height()), t.TEXT, size=18)
        stamp = time.strftime("%d.%m %H:%M", time.localtime(ev.ts))
        sw = self.fm_small.horizontalAdvance(stamp)
        painter.setFont(self.f_small)
        painter.setPen(QColor(t.MUTED))
        painter.drawText(QRect(r.right() - 12 - sw, r.y() + 10, sw, self.fm_small.height()), int(Qt.AlignmentFlag.AlignRight), stamp)
        room = r.width() - 2 * 12 - 26 - sw - 12
        wrap = int(Qt.TextFlag.TextWordWrap)
        painter.setFont(self.f_row)
        painter.setPen(QColor(t.TEXT))
        main = event_text(ev)
        mh = self.fm_row.boundingRect(QRect(0, 0, room, 10_000), wrap, main).height()
        painter.drawText(QRect(x + 26, r.y() + 10, room, mh), wrap, main)
        painter.setFont(self.f_small)
        painter.setPen(QColor(t.MUTED))
        painter.drawText(QRect(x + 26, r.y() + 10 + mh, room, r.height()), wrap, ev.title)
        painter.restore()


@guard_events
class IntegrationsPage(TabPage):
    """The connections on the left, the settings of the picked one and the feed on the right."""

    name = "integrations"

    def build(self) -> None:
        self.status_label: QLabel | None = None
        self._feed_count = FEED_PAGE
        self._settings_width = 640
        _splitter, left, col, right = self.split(260, 220)
        col.addWidget(section(_("CONNECTIONS"), left))
        self.connections = count_list(left)
        for key, text in (("gitlab", "  🦊 GitLab"), ("jira", "  🧭 Jira")):
            item = QListWidgetItem(text, self.connections)
            item.setData(Qt.ItemDataRole.UserRole, key)
        self.connections.setCurrentRow(0)
        self.connections.currentRowChanged.connect(lambda _r: self.render_page())
        col.addWidget(self.connections, 1)

        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, right)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(0)
        self.settings_area, self.settings, self.settings_col = scroll_area(right, (28, 20, 28, 20))
        self.feed_panel = QWidget(right)
        self.feed_panel.setObjectName("Card")
        fcol = QVBoxLayout(self.feed_panel)
        fcol.setContentsMargins(20, 20, 28, 20)
        head = QHBoxLayout()
        head.addWidget(section(_("NOTIFICATIONS"), self.feed_panel))
        head.addStretch(1)
        read = button(_("Mark all read"), "link", self.feed_panel)
        read.clicked.connect(self._mark_all_read)
        clear = button(_("Clear"), "link", self.feed_panel)
        clear.clicked.connect(self._clear_feed)
        head.addWidget(read)
        head.addWidget(clear)
        fcol.addLayout(head)
        self.feed = QListWidget(self.feed_panel)
        self.feed.setObjectName("Feed")
        self.feed.setItemDelegate(EventDelegate(self.feed))
        self.feed.setMouseTracking(True)
        self.feed.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.feed.setWordWrap(True)
        self.feed.itemClicked.connect(self._open_item)
        fcol.addWidget(self.feed, 1)
        self.feed_empty = label(_("Nothing yet. Events appear here and as pop-ups in the corner of the screen."), "muted", self.feed_panel)
        self.feed_empty.setWordWrap(True)
        fcol.addWidget(self.feed_empty)
        self.columns.addWidget(self.settings_area, 0)
        self.columns.addWidget(self.feed_panel, 1)
        right.installEventFilter(self)
        self._right = right
        self._aside: bool | None = None

    # ---- what the window asks for

    def view_key(self) -> tuple:
        return super().view_key() + (self.shell.gitlab.status,)

    def refresh(self, keep_scroll: bool = False) -> None:
        self.render_page()

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent

        if obj is self._right and event.type() == QEvent.Type.Resize:
            self._place_columns()
        return False

    def _place_columns(self) -> None:
        """The feed right of the settings when the width allows, else under them."""
        gitlab = self.selected() == "gitlab"
        aside = gitlab and self._right.width() >= self._settings_width + FEED_MIN_WIDTH
        self.feed_panel.setVisible(gitlab)
        if aside == self._aside:
            return
        self._aside = aside
        self.columns.setDirection(QBoxLayout.Direction.LeftToRight if aside else QBoxLayout.Direction.TopToBottom)
        if aside:
            self.settings_area.setFixedWidth(self._settings_width)
        else:
            self.settings_area.setMinimumWidth(0)
            self.settings_area.setMaximumWidth(16_777_215)
        self.columns.setStretch(0, 0 if aside else 3)
        self.columns.setStretch(1, 1 if aside else 2)

    def selected(self) -> str:
        item = self.connections.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else "gitlab"

    def update_badge(self) -> None:
        self.shell.update_integrations_badge()

    # ---- the settings

    def _clear_settings(self) -> None:
        while self.settings_col.count():
            item = self.settings_col.takeAt(0)
            if item is None:
                break
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            else:
                inner = item.layout()
                if inner is not None:
                    QWidget().setLayout(inner)  # the layout and its widgets go with a throwaway owner
        self.status_label = None

    def render_page(self) -> None:
        if self.vault is None:
            return
        self._clear_settings()
        gl = self.vault.integrations.gitlab
        self.connections.item(0).setText("  🦊 GitLab" + ("   ●" if gl.enabled else ""))
        if self.selected() == "jira":
            head = label("Jira", "heading", self.settings)
            self.settings_col.addWidget(head)
            text = label(_("Not connected yet. The tracker address (Journal tab → Links…) already turns issue keys into links; notifications from Jira can follow the same scheme as GitLab."), "muted", self.settings)
            text.setWordWrap(True)
            self.settings_col.addWidget(text)
            self.settings_col.addStretch(1)
        else:
            self._render_gitlab()
        self._aside = None
        self._place_columns()
        self.render_feed()

    def _render_gitlab(self) -> None:
        integ = self.vault.integrations
        gl = integ.gitlab
        body, col = self.settings, self.settings_col
        head = QHBoxLayout()
        head.addWidget(IconLabel("🦊 GitLab", "heading", body, size=22))
        head.addStretch(1)
        watch = QCheckBox(_("Watch"), body)
        watch.setChecked(gl.enabled)

        def toggle(on: bool) -> None:
            gl.enabled = on
            self.shell.mark_dirty("integrations")
            self.connections.item(0).setText("  🦊 GitLab" + ("   ●" if gl.enabled else ""))
            if gl.enabled and not integ.gitlab_state:
                self.shell.gitlab.check_now()

        watch.toggled.connect(toggle)
        head.addWidget(watch)
        col.addLayout(head)
        about = label(_("Checks your open merge requests every few minutes and tells you what changed. The token needs the read_api scope and is taken from a password entry."), "muted", body)
        about.setWordWrap(True)
        col.addSpacing(4)
        col.addWidget(about)
        col.addSpacing(14)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(14)
        url = QLineEdit(gl.url or self.shell.default_gitlab_url(), body)
        url.setPlaceholderText("https://gitlab.example.com")
        url.setMinimumWidth(QFontMetrics(url.font()).averageCharWidth() * 36)
        entries = sorted(self.vault.entries, key=lambda e: e.name.lower())
        token = QComboBox(body)
        token.addItem(_("— pick an entry —"), "")
        for e in entries:
            token.addItem(e.name, e.id)
        current = self.vault.entry_by_id(gl.entry_id)
        token.setCurrentIndex(entries.index(current) + 1 if current in entries else 0)
        interval = QComboBox(body)
        for n in INTERVALS:
            interval.addItem(_("every {n} min").format(n=n), n)
        interval.setCurrentIndex(INTERVALS.index(gl.interval_min) if gl.interval_min in INTERVALS else 0)
        form.addRow(_("Address"), url)
        form.addRow(_("Token from entry"), token)
        form.addRow("", label(_("its secret field holds the token"), "muted", body))
        form.addRow(_("Check"), interval)
        col.addLayout(form)

        def save_form(*_args: object) -> None:
            gl.url = url.text().strip().rstrip("/")
            gl.entry_id = token.currentData() or ""
            gl.interval_min = int(interval.currentData())
            self.shell.mark_dirty("integrations")

        url.textEdited.connect(save_form)
        token.activated.connect(save_form)
        interval.activated.connect(save_form)

        col.addSpacing(18)
        col.addWidget(section(_("NOTIFY ME WHEN"), body))
        col.addSpacing(6)
        rules_host, rules = flow_host(body, 18)
        for code, (text, _on) in RULES.items():
            tick = QCheckBox(rules_host)
            with_icon(tick, f"{EVENT_ICONS[code]}  {_(text)}")
            tick.setChecked(gl.rule(code))

            def flip(on: bool, c: str = code) -> None:
                gl.rules[c] = on
                integ.gitlab_full = 0.0  # a rule just on has no facts yet: the next check asks about all
                self.shell.mark_dirty("integrations")

            tick.toggled.connect(flip)
            rules.addWidget(tick)
        col.addWidget(rules_host)
        self._render_projects()
        col.addSpacing(16)
        check = button(_("Check now"), "primary", body)
        check.clicked.connect(lambda: self.shell.gitlab.check_now())
        col.addWidget(check, 0, Qt.AlignmentFlag.AlignLeft)
        col.addSpacing(6)
        who = _("Signed in as @{user}").format(user=gl.username) if gl.username else ""
        self.status_label = label(self.shell.gitlab.status or who, "muted", body)  # under the button: a long status wraps, never cut
        self.status_label.setWordWrap(True)
        col.addWidget(self.status_label)
        col.addStretch(1)
        self._settings_width = max(560, min(760, body.sizeHint().width() + 20))

    def _render_projects(self) -> None:
        integ = self.vault.integrations
        gl = integ.gitlab
        body, col = self.settings, self.settings_col
        col.addSpacing(18)
        col.addWidget(section(_("PROJECTS"), body))
        known = sorted(set(integ.gitlab_projects) | set(gl.projects))

        def hint() -> str:
            if not known:
                return _("The projects with your open merge requests appear here after the first check.")
            return _("Only the ticked projects are watched.") if gl.projects else _("None ticked — all projects are watched.")

        note = label(hint(), "muted", body)
        note.setWordWrap(True)
        col.addWidget(note)
        host, flow = flow_host(body, 18)
        ticks: dict[str, QCheckBox] = {}

        def changed(_on: bool) -> None:
            gl.projects = [p for p in known if ticks[p].isChecked()]
            integ.gitlab_full = 0.0
            note.setText(hint())
            self.shell.mark_dirty("integrations")

        for path in known:
            tick = QCheckBox(path, host)
            tick.setChecked(path in gl.projects)
            tick.toggled.connect(changed)
            ticks[path] = tick
            flow.addWidget(tick)
        col.addWidget(host)
        self.project_ticks = ticks

    # ---- the feed

    def render_feed(self) -> None:
        if self.vault is None:
            return
        feed = self.vault.integrations.feed
        self.feed.clear()
        self.feed_empty.setVisible(not feed)
        self.feed.setVisible(bool(feed))
        for ev in feed[:self._feed_count]:
            item = QListWidgetItem(self.feed)
            item.setData(EVENT_ROLE, ev)
        rest = len(feed) - self._feed_count
        if rest > 0:
            more = QListWidgetItem(self.feed)
            more.setData(MORE_ROLE, _("Show more ({n})").format(n=rest))

    def _open_item(self, item: QListWidgetItem) -> None:
        ev = item.data(EVENT_ROLE)
        if ev is None:
            self._feed_count += FEED_PAGE
            self.render_feed()
            return
        ev.read = True
        if ev.url.startswith(("https://", "http://")):
            webbrowser.open(ev.url)
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()

    def _mark_all_read(self) -> None:
        self.vault.integrations.mark_all_read()
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()

    def _clear_feed(self) -> None:
        self.vault.integrations.clear_feed()
        self.shell.mark_dirty("integrations")
        self.render_feed()
        self.update_badge()
