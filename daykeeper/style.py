"""The look of every ttk widget: rounded buttons, fields, check boxes, thin scrollbars,
the dark tab bar, the settings window — installed once on the root window."""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont, ttk

from . import theme
from .palette import (
    ACCENT,
    ACCENT_DISABLED,
    ACCENT_HOVER,
    ACCENT_INK,
    ACCENT_PRESSED,
    ACCENT_SOFT,
    LINK,
    BG,
    BORDER,
    CARD,
    CHECK_BORDER,
    DANGER,
    FIELD,
    FONT,
    pt,
    HOVER,
    MONO,
    MUTED,
    PRESSED,
    RAIL,
    RAIL_ACTIVE,
    RAIL_HOVER,
    ON_ACCENT,
    ON_RAIL,
    RAIL_TEXT,
    RAISED,
    RAISED_HOVER,
    DANGER_HOVER,
    DANGER_SOFT,
    SCROLL,
    SCROLL_HOVER,
    SIDEBAR,
    SUCCESS,
    TEXT,
)

RAISED_SHADOW = 4  # px under a raised button (the “raised” look chosen 15.10.2026)
BUTTON_SIZE = 12  # pt: button text, small buttons one less (“larger” option chosen 15.10.2026)


def install(root: tk.Tk) -> theme.Painter:
    """Configure the named fonts, the option database and every style the app uses;
    the Painter that drew the images is returned (it keeps them alive)."""
    root.configure(bg=BG)
    for name, size in (("TkDefaultFont", 11), ("TkTextFont", 11), ("TkMenuFont", 10), ("TkHeadingFont", 11)):
        tkfont.nametofont(name).configure(family=FONT, size=pt(size))
    root.option_add("*TCombobox*Listbox.font", (FONT, pt(11)))
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.relief", "flat")
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.activeBackground", ACCENT_SOFT)
    root.option_add("*Menu.activeForeground", TEXT)
    root.option_add("*Menu.activeBorderWidth", 0)
    root.option_add("*Menu.font", (FONT, pt(10)))
    for cls in ("Label", "Entry", "Text", "Listbox"):  # plain tk widgets made without a colour: the theme's text
        root.option_add(f"*{cls}.foreground", TEXT)
    root.option_add("*Entry.insertBackground", TEXT)
    root.option_add("*Text.insertBackground", TEXT)
    root.option_add("*Menu.background", CARD)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)

    style = ttk.Style(root)
    style.theme_use("clam")
    p = theme.Painter(root)
    style.configure(
        ".",
        background=BG,
        foreground=TEXT,
        font=(FONT, pt(11)),
        bordercolor=BORDER,
        focuscolor=ACCENT,
        troughcolor=BG,
    )
    style.configure("TFrame", background=BG)
    style.configure("Sidebar.TFrame", background=SIDEBAR)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Rail.TFrame", background=RAIL)

    style.configure("TLabel", background=BG)
    style.configure("Sidebar.TLabel", background=SIDEBAR)
    style.configure("SidebarMuted.TLabel", background=SIDEBAR, foreground=MUTED, font=(FONT, pt(10)))
    style.configure("Card.TLabel", background=CARD)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED, font=(FONT, pt(10)))
    style.configure("Section.TLabel", background=CARD, foreground=MUTED, font=(FONT, pt(9), "bold"))
    style.configure("Title.TLabel", background=BG, font=(FONT, pt(20), "bold"))
    style.configure("PanelTitle.TLabel", background=CARD, font=(FONT, pt(20), "bold"))
    style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=(FONT, pt(11)))
    style.configure("PanelSubtitle.TLabel", background=CARD, foreground=MUTED, font=(FONT, pt(11)))
    style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=(FONT, pt(10)))
    style.configure("Error.TLabel", background=BG, foreground=DANGER)
    style.configure("Status.TLabel", background=BG, foreground=MUTED, font=(FONT, pt(9)))
    style.configure("Panel.TLabel", background=CARD)
    style.configure("PanelMuted.TLabel", background=CARD, foreground=MUTED, font=(FONT, pt(10)))
    style.configure("PanelError.TLabel", background=CARD, foreground=DANGER)

    # fields: a rounded box whose border turns accent on focus
    for name, fill in (("TEntry", FIELD), ("Key.TEntry", BG)):
        focus = p.box(8, FIELD, ACCENT, border_width=1.5)
        p.element(style, p.el(f"{name}.rfield"), 8, [("disabled", p.box(8, BG, BORDER)), ("focus", focus)], p.box(8, fill, BORDER))
        style.layout(name, [(p.el(f"{name}.rfield"), {"sticky": "nswe", "children": [("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
        style.configure(name, padding=(10, 8), insertcolor=TEXT, selectbackground=ACCENT_SOFT, selectforeground=TEXT, fieldbackground=fill)
    style.configure("Eye.TEntry", padding=(10, 8, p.px(38), 8))  # the text stops before the eye inside the field

    p.element(style, p.el("Modern.Combobox.rfield"), 8, [("disabled", p.box(8, BG, BORDER)), ("focus", p.box(8, FIELD, ACCENT, border_width=1.5)), ("hover", p.box(8, HOVER, BORDER))], p.box(8, FIELD, BORDER))
    style.layout(
        "TCombobox",
        [(p.el("Modern.Combobox.rfield"), {"sticky": "nswe", "children": [
            ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"expand": "1", "sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]}),
        ]})],
    )
    style.configure("TCombobox", padding=(10, 6), arrowcolor=MUTED, arrowsize=10, background=CARD, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, fieldbackground=FIELD, foreground=TEXT)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", FIELD), ("disabled", BG)],
        background=[("readonly", FIELD), ("active", FIELD)],
        bordercolor=[("readonly", FIELD)],
        lightcolor=[("readonly", FIELD)],
        darkcolor=[("readonly", FIELD)],
        selectbackground=[("readonly", FIELD)],
        selectforeground=[("readonly", TEXT)],
        arrowcolor=[("active", TEXT)],
    )

    # buttons: one rounded box per style, the states differ only in colour
    buttons: list[str] = []

    def rounded(name: str, radius: float, normal: tuple, hover: tuple, pressed: tuple, disabled: tuple | None = None) -> None:
        buttons.append(name)
        theme.button(style, p, name, radius, normal, hover, pressed, disabled)
        style.layout(name, [(p.el(f"{name}.rbox"), {"sticky": "nswe", "children": [("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])

    none = (None, None)
    # raised buttons: white (or accent) on a soft shadow, no frame; pressed sinks a little
    lift = RAISED_SHADOW
    rounded("TButton", 10, (RAISED, None, lift), (RAISED_HOVER, None, lift), (PRESSED, None, lift / 2), (BG, BORDER, 0))
    style.configure("TButton", padding=(14 + lift, 8 + lift), anchor="center", background=CARD, font=(FONT, pt(BUTTON_SIZE)))
    rounded("Primary.TButton", 10, (ACCENT, None, lift), (ACCENT_HOVER, None, lift), (ACCENT_PRESSED, None, lift / 2), (ACCENT_DISABLED, None, 0))
    style.configure("Primary.TButton", foreground=ON_ACCENT, font=(FONT, pt(BUTTON_SIZE), "bold"), padding=(16 + lift, 8 + lift), background=CARD)
    style.map("Primary.TButton", foreground=[("disabled", ON_ACCENT)])
    rounded("Icon.TButton", 9, (None, None, 0), (RAISED, None, lift * 0.75), (PRESSED, None, lift / 2))
    style.configure("Icon.TButton", padding=(6 + lift * 0.75, 5 + lift * 0.75), width=3, background=CARD)
    style.map("Icon.TButton", background=[("disabled", CARD)], foreground=[("disabled", BORDER)])
    rounded("Small.TButton", 9, (RAISED, None, lift * 0.75), (RAISED_HOVER, None, lift * 0.75), (PRESSED, None, lift / 3))
    style.configure("Small.TButton", padding=(10 + lift * 0.75, 5 + lift * 0.75), font=(FONT, pt(BUTTON_SIZE - 1)), background=CARD)
    rounded("Link.TButton", 6, none, (ACCENT_SOFT, None), (ACCENT_SOFT, None))
    style.configure("Link.TButton", foreground=LINK, padding=(6, 5), background=CARD)
    style.map("Link.TButton", foreground=[("active", LINK)])
    rounded("Danger.TButton", 10, (RAISED, None, lift), (DANGER_HOVER, None, lift), (DANGER_SOFT, None, lift / 2))
    style.configure("Danger.TButton", foreground=DANGER, padding=(12 + lift, 8 + lift), background=SIDEBAR, font=(FONT, pt(BUTTON_SIZE)))
    # the corners of the rounded box already pad the label by radius + lift, so the sidebar
    # icons add little: the arrows and the bin fit beside "+ New entry" in a narrow sidebar
    rounded("DangerIcon.TButton", 9, (None, None, 0), (DANGER_HOVER, None, lift * 0.75), (DANGER_SOFT, None, lift / 2))
    style.configure("DangerIcon.TButton", foreground=DANGER, padding=(2, 2), width=2, background=SIDEBAR)
    rounded("SidebarIcon.TButton", 9, (None, None, 0), (RAISED, None, lift * 0.75), (PRESSED, None, lift / 2))
    style.configure("SidebarIcon.TButton", padding=(2, 2), width=2, foreground=MUTED, background=SIDEBAR)
    style.map("SidebarIcon.TButton", foreground=[("active", TEXT)])

    for name, font, outer in (("More.TButton", (FONT, pt(12), "bold"), SIDEBAR), ("Quiet.TButton", (FONT, pt(BUTTON_SIZE - 1)), CARD)):
        rounded(name, 9, (RAISED, None, lift * 0.75), (RAISED_HOVER, None, lift * 0.75), (PRESSED, None, lift / 3))
        style.configure(name, foreground=TEXT, font=font, background=outer)
    style.configure("More.TButton", padding=(6 + lift * 0.75, 4 + lift * 0.75), width=2, anchor="center")
    style.configure("Quiet.TButton", padding=(10 + lift * 0.75, 5 + lift * 0.75))

    # read-only drop-down lists (popup.Dropdown): a field with an arrow
    for name, outer in (("Dropdown.TButton", CARD), ("SidebarDropdown.TButton", SIDEBAR)):
        theme.button(style, p, name, 8, (FIELD, BORDER), (FIELD, CHECK_BORDER), (FIELD, ACCENT), (BG, BORDER))
        style.layout(name, [(p.el(f"{name}.rbox"), {"sticky": "nswe", "children": [("Button.padding", {"sticky": "nswe", "children": [
            ("Menubutton.indicator", {"side": "right", "sticky": ""}),
            ("Button.label", {"sticky": "w"}),
        ]})]})])
        style.configure(name, foreground=TEXT, background=outer, anchor="w", padding=(12, 7), arrowcolor=MUTED, arrowsize=4, font=(FONT, pt(11)))
        style.map(name, arrowcolor=[("active", TEXT)], foreground=[("disabled", MUTED)])

    # the dark tab bar on top
    rounded("Nav.TButton", 9, none, (RAIL_HOVER, None), (RAIL_HOVER, None))
    style.configure("Nav.TButton", foreground=RAIL_TEXT, anchor="center", padding=(10, 7), font=(FONT, pt(11)), background=RAIL)
    style.map("Nav.TButton", foreground=[("active", ON_RAIL)])
    rounded("NavActive.TButton", 9, (RAIL_ACTIVE, None), (RAIL_ACTIVE, None), (RAIL_ACTIVE, None))
    style.configure("NavActive.TButton", foreground=ON_ACCENT, anchor="center", padding=(10, 7), font=(FONT, pt(11), "bold"), background=RAIL)
    rounded("NavIcon.TButton", 9, none, (RAIL_HOVER, None), (RAIL_HOVER, None), none)  # disabled (undo with nothing to undo): just dimmer
    # macOS has no ⚙ ↶ ↷ ⏻ in its UI font and draws them small from a fallback one
    icon_size = 16 if sys.platform == "darwin" else 12
    style.configure("NavIcon.TButton", foreground=RAIL_TEXT, padding=(9, 7), width=2, font=(FONT, pt(icon_size)), background=RAIL)
    style.map("NavIcon.TButton", foreground=[("disabled", RAIL_HOVER), ("active", ON_RAIL)], background=[("disabled", RAIL)])

    rounded("Toggle.Toolbutton", 7, none, (HOVER, None), (PRESSED, None))
    style.configure("Toggle.Toolbutton", padding=(8, 5), background=CARD)
    p.element(style, p.el("Toggle.sel"), 7, [("selected", p.box(7, ACCENT_SOFT)), ("active", p.box(7, HOVER))], p.box(7, None))
    style.layout("Toggle.Toolbutton", [(p.el("Toggle.sel"), {"sticky": "nswe", "children": [("Toolbutton.padding", {"sticky": "nswe", "children": [("Toolbutton.label", {"sticky": "nswe"})]})]})])
    style.map("Toggle.Toolbutton", foreground=[("selected", LINK)])

    style.configure(
        "Treeview",
        background=SIDEBAR,
        fieldbackground=SIDEBAR,
        foreground=TEXT,
        rowheight=38,
        borderwidth=0,
        relief="flat",
        font=(FONT, pt(11)),
    )
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[("selected", ACCENT_INK)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("TPanedwindow", background=SIDEBAR)  # a gap next to a narrow pane blends with the sidebars
    style.configure("Sash", sashthickness=1, gripcount=0, background=BORDER, lightcolor=BORDER, darkcolor=BORDER, bordercolor=BORDER)
    style.configure("TSeparator", background=BORDER, lightcolor=BORDER, darkcolor=BORDER, bordercolor=BORDER)

    # thin scrollbars: a pill without arrows
    if p.el("Thin.Vertical.Scrollbar.thumb") not in style.element_names():
        style.element_create(
            p.el("Thin.Vertical.Scrollbar.thumb"), "image",
            p.image(10, 200, 3, fill=SCROLL, inset=2),
            ("pressed", p.image(10, 200, 3, fill=SCROLL_HOVER, inset=2)),
            ("active", p.image(10, 200, 3, fill=SCROLL_HOVER, inset=2)),
            border=(0, p.px(8)), sticky="ns", height=p.px(24),
        )
    style.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [(p.el("Thin.Vertical.Scrollbar.thumb"), {"expand": "1", "sticky": "nswe"})]})])
    style.configure("Vertical.TScrollbar", troughcolor=CARD, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, gripcount=0)

    # checkboxes and radio buttons: rounded squares and circles with an accent tick
    if p.el("Modern.Checkbutton.indicator") not in style.element_names():
        off = p.image(18, 18, 5, fill=FIELD, border=CHECK_BORDER, border_width=1.5, right=8)
        hover = p.image(18, 18, 5, fill=FIELD, border=ACCENT, border_width=1.5, right=8)
        on = p.image(18, 18, 5, fill=ACCENT, check=ON_ACCENT, right=8)
        on_hover = p.image(18, 18, 5, fill=ACCENT_HOVER, check=ON_ACCENT, right=8)
        style.element_create(p.el("Modern.Checkbutton.indicator"), "image", off, ("selected active", on_hover), ("selected", on), ("active", hover), sticky="")
        r_off = p.image(18, 18, 9, fill=FIELD, border=CHECK_BORDER, border_width=1.5, right=8)
        r_hover = p.image(18, 18, 9, fill=FIELD, border=ACCENT, border_width=1.5, right=8)
        r_on = p.image(18, 18, 9, fill=ACCENT, dot=ON_ACCENT, right=8)
        style.element_create(p.el("Modern.Radiobutton.indicator"), "image", r_off, ("selected", r_on), ("active", r_hover), sticky="")
    for name, bg in (("TCheckbutton", BG), ("Card.TCheckbutton", CARD)):
        style.layout(name, [("Checkbutton.padding", {"sticky": "nswe", "children": [
            (p.el("Modern.Checkbutton.indicator"), {"side": "left", "sticky": ""}),
            ("Checkbutton.label", {"side": "left", "sticky": "nswe"}),
        ]})])
        style.configure(name, background=bg)
        style.map(name, background=[("active", bg)])
    style.layout("TRadiobutton", [("Radiobutton.padding", {"sticky": "nswe", "children": [
        (p.el("Modern.Radiobutton.indicator"), {"side": "left", "sticky": ""}),
        ("Radiobutton.label", {"side": "left", "sticky": "nswe"}),
    ]})])
    style.configure("TRadiobutton", background=BG)
    style.map("TRadiobutton", background=[("active", BG)])

    # a white panel with rounded corners and a soft shadow (the unlock screen)
    if p.el("Panel.rbox") not in style.element_names():
        style.element_create(p.el("Panel.rbox"), "image", p.image(300, 300, 16, fill=CARD, shadow=14), border=p.px(34), sticky="nsew", width=p.px(68), height=p.px(68))
    style.layout("Panel.TFrame", [(p.el("Panel.rbox"), {"sticky": "nswe"})])
    style.configure("Panel.TFrame", background=BG)

    # the settings window: light section list, white rounded groups on grey
    p.element(style, p.el("Group.rbox"), 12, [], p.box(12, CARD, BORDER, matte=BG))  # opaque: a resize copies it, no blending
    style.layout("Group.TFrame", [(p.el("Group.rbox"), {"sticky": "nswe"})])
    style.configure("Group.TFrame", background=BG)
    style.configure("SetTitle.TLabel", background=SIDEBAR, font=(FONT, pt(15), "bold"))
    style.configure("SetHead.TLabel", background=BG, font=(FONT, pt(18), "bold"))
    style.configure("SetGroup.TLabel", background=BG, foreground=MUTED, font=(FONT, pt(9), "bold"))
    style.configure("CardOk.TLabel", background=BG, foreground=SUCCESS)
    style.configure("CardGood.TLabel", background=CARD, foreground=SUCCESS)  # a good state inside a white card
    style.configure("CardTitle.TLabel", background=CARD, font=(FONT, pt(11), "bold"))
    style.configure("CardError.TLabel", background=CARD, foreground=DANGER)
    style.configure("CardMono.TLabel", background=CARD, font=(MONO, pt(10)))
    rounded("SetNav.TButton", 8, none, (HOVER, None), (PRESSED, None))
    style.configure("SetNav.TButton", foreground=TEXT, anchor="w", padding=(12, 8), background=SIDEBAR)
    rounded("SetNavActive.TButton", 8, (ACCENT_SOFT, None), (ACCENT_SOFT, None), (ACCENT_SOFT, None))
    style.configure("SetNavActive.TButton", foreground=ACCENT_INK, anchor="w", padding=(12, 8), background=SIDEBAR, font=(FONT, pt(11), "bold"))
    style.layout("Card.TRadiobutton", style.layout("TRadiobutton"))
    style.configure("Card.TRadiobutton", background=CARD)
    style.map("Card.TRadiobutton", background=[("active", CARD)])
    # around a rounded image the button's own background shows: in every state it is the
    # colour the style stands on (Tk's default turns a disabled button's into a grey box)
    for name in buttons:
        mapped = {k: [tuple(x) for x in v] for k, v in (style.map(name) or {}).items()}
        own = style.lookup(name, "background")
        mapped["background"] = [x for x in mapped.get("background", []) if x[0] != "disabled"] + [("disabled", own), ("active", own), ("pressed", own)]
        style.map(name, **mapped)
    return p


# ---- raised buttons take the colour of what they stand on

SKIP_SURFACE = ("Nav", "SetNav")  # styles on a fixed surface (the dark bar, the settings menu)
CARD_FRAMES = {"Group.TFrame": CARD}  # a frame drawn as a white card over a grey background


def surface_of(widget: tk.Misc, style: ttk.Style) -> str | None:
    """The colour a widget stands on: its parent's background — the card colour for a frame
    painted as a card (Group, round_style)."""
    parent = widget.master
    if parent is None:
        return None
    try:
        if parent.winfo_class().startswith("T"):  # a ttk container
            name = str(parent.cget("style")) or parent.winfo_class()
            if name in CARD_FRAMES:
                return CARD_FRAMES[name]
            if name.startswith("R") and "x" in name:  # theme.round_style: R<radius>x<fill>x…
                return "#" + name.split("x")[1]
            colour = style.lookup(name, "background")
        else:
            colour = parent.cget("bg")
    except tk.TclError:
        return None
    return str(colour) if colour else None


def fit_button_surface(root: tk.Tk) -> None:
    """A button's shadow and rounded corners leave its own background showing around it; a
    button made with the card colour stood in a white box on a grey sidebar. When a button
    is shown, it takes a copy of its style whose background is the colour under it. The
    copy is made again at a new scale: it points at the elements of the scale it was made at."""
    style = ttk.Style(root)
    made: dict[str, str] = {}  # derived style → the Painter tag it was copied at

    def on_map(event: tk.Event) -> None:
        w = event.widget
        try:
            name = str(w.cget("style")) or "TButton"
        except (tk.TclError, AttributeError):
            return
        base = name.split("~", 1)[-1]
        if base.startswith(SKIP_SURFACE):
            return
        surface = surface_of(w, style)
        if not surface or surface.lower() == str(style.lookup(base, "background")).lower():
            return
        derived = f"{surface.lstrip('#')}~{base}"
        if derived != name:
            tag = getattr(getattr(root, "painter", None), "tag", "")
            if made.get(derived) != tag:
                style.layout(derived, style.layout(base))
                style.configure(derived, **{k: v for k, v in (style.configure(base) or {}).items()})
                style.configure(derived, background=surface)
                mapped = {k: [tuple(x) for x in v] for k, v in (style.map(base) or {}).items()}
                mapped["background"] = [("disabled", surface), ("active", surface), ("pressed", surface)]
                style.map(derived, **mapped)
                made[derived] = tag
            w.configure(style=derived)

    root.bind_class("TButton", "<Map>", on_map, add="+")
