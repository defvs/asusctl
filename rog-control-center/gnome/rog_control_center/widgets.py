# SPDX-License-Identifier: MPL-2.0
from gi.repository import Adw, GLib, Gtk


def label(text, css=None, **kwargs):
    widget = Gtk.Label(label=text, **kwargs)
    if css:
        widget.add_css_class(css)
    return widget


def button(text=None, icon=None, css=None, callback=None, tooltip=None):
    widget = Gtk.Button(label=text) if text else Gtk.Button(icon_name=icon)
    if css:
        widget.add_css_class(css)
    if callback:
        widget.connect("clicked", lambda _: callback())
    if tooltip:
        widget.set_tooltip_text(tooltip)
    return widget


def group(parent, title, description=None):
    widget = Adw.PreferencesGroup(
        title=GLib.markup_escape_text(title), description=GLib.markup_escape_text(description or "")
    )
    parent.append(widget)
    return widget


def info_row(parent, title, value="", icon=None):
    row = Adw.ActionRow(use_markup=False)
    row.set_title(title)
    row.set_title_lines(2)
    if icon:
        row.add_prefix(Gtk.Image(icon_name=icon))
    value_label = label(
        value, "dim-label", selectable=True, wrap=True, max_width_chars=28, width_chars=16, xalign=1
    )
    row.add_suffix(value_label)
    parent.add(row)
    return row, value_label


def page(title, subtitle):
    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=24,
        margin_top=32,
        margin_bottom=36,
        margin_start=24,
        margin_end=24,
    )
    heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    heading.append(label(title, "title-1", xalign=0, wrap=True))
    if subtitle:
        heading.append(label(subtitle, "dim-label", xalign=0, wrap=True))
    content.append(heading)
    clamp = Adw.Clamp(maximum_size=780, tightening_threshold=580, child=content)
    scroll = Gtk.ScrolledWindow(
        hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True, hexpand=True
    )
    return scroll, content


def empty(parent, title, description, icon="dialog-information-symbolic"):
    status = Adw.StatusPage(title=title, description=description, icon_name=icon)
    parent.append(status)
    return status


def metric(title, icon, callback):
    card = Gtk.Button(hexpand=True)
    card.add_css_class("card")
    card.connect("clicked", lambda _: callback())
    card.update_property([Gtk.AccessibleProperty.LABEL], [title])
    body = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_top=18,
        margin_bottom=18,
        margin_start=18,
        margin_end=18,
    )
    top = Gtk.Box(spacing=8)
    top.append(Gtk.Image(icon_name=icon))
    top.append(label(title, "dim-label", xalign=0))
    body.append(top)
    value = label("—", "title-1", xalign=0)
    body.append(value)
    detail = label("Reading sensors…", "caption", xalign=0, wrap=True)
    body.append(detail)
    card.set_child(body)
    return card, value, detail
