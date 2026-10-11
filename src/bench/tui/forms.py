"""Small pop up screens shared by the Tasks and Projects screens: type a line, answer yes or no, pick one.

Each one is dismissed with its answer, or with None (or False) when escape cancels it.
"""
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

FORM_CSS = """
.form { width: 80; height: auto; max-height: 90%; border: round $accent; background: $panel; padding: 1 2; }
.form-hint { color: $text-muted; margin-top: 1; }
"""


class TextPrompt(ModalScreen[str | None]):
    """One line of text. Enter saves, escape cancels."""

    DEFAULT_CSS = "TextPrompt { align: center middle; }" + FORM_CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", max_length: int = 200):
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.max_length = max_length

    def compose(self) -> ComposeResult:
        with Vertical(classes="form"):
            yield Static(f"[b]{escape(self.title_text)}[/b]")
            yield Input(placeholder=self.placeholder, max_length=self.max_length, id="answer")
            yield Static("enter saves   escape cancels", classes="form-hint")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class Confirm(ModalScreen[bool]):
    """A yes or no question where no is the default: only y says yes."""

    DEFAULT_CSS = "Confirm { align: center middle; }" + FORM_CSS
    BINDINGS = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n", "answer(False)", "No"),
        Binding("enter", "answer(False)", "No", show=False),
        Binding("escape", "answer(False)", "No", show=False),
    ]

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="form"):
            yield Static(escape(self.message))
            yield Static("y yes   n, enter, or escape: no", classes="form-hint")

    def action_answer(self, yes: bool) -> None:
        self.dismiss(yes)


class Choose(ModalScreen[object]):
    """Pick one of several labeled values. Enter picks, escape cancels (dismissed with Choose.CANCELLED)."""

    CANCELLED = object()
    DEFAULT_CSS = "Choose { align: center middle; }" + FORM_CSS
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, choices: list[tuple[str, object]], start: int = 0):
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.start = start

    def compose(self) -> ComposeResult:
        with Vertical(classes="form"):
            yield Static(f"[b]{escape(self.title_text)}[/b]")
            yield OptionList(*[Option(escape(label)) for label, _ in self.choices], id="choices")
            yield Static("enter picks   escape cancels", classes="form-hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).highlighted = self.start

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(self.choices[event.option_index][1])

    def action_cancel(self) -> None:
        self.dismiss(self.CANCELLED)
