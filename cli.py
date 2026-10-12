# cli.py
# -------------------------------------------------------------------
# Keia TUI — Win95-стиль: бирюзовый стол, бежевые окна, сайдбары.
# -------------------------------------------------------------------

import asyncio
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal
from textual.widgets import Header, Footer, Input, Static
from textual.binding import Binding
from textual.widgets import Button

from rich.text import Text
from rich.panel import Panel
from rich.box import SQUARE

from core import agent, history, facts


USER_ID = 1


# -------------------------------------------------------------------
# 1. Виджет одного сообщения
# -------------------------------------------------------------------

class Message(Static):
    """Одно сообщение в чате."""

    def __init__(self, text: str, role: str) -> None:
        super().__init__()
        self.text_content = text
        self.role = role

    def render(self) -> Panel:
        if self.role == "user":
            return Panel(
                Text(self.text_content, style="#2b1810 on #d4c5a9"),
                title="[bold #f5efdc on #3d5a80] ТЫ [/bold #f5efdc on #3d5a80]",
                title_align="left",
                border_style="#8b7355",
                box=SQUARE,
                padding=(0, 1),
            )
        elif self.role == "keia":
            return Panel(
                Text(self.text_content, style="#2b1810 on #d4c5a9"),
                title="[bold #f5efdc on #3d5a80] КЕЯ [/bold #f5efdc on #3d5a80]",
                title_align="left",
                border_style="#8b7355",
                box=SQUARE,
                padding=(0, 1),
            )
        else:
            return Text(f"  ▸ {self.text_content}", style="#c0d8d8")


# -------------------------------------------------------------------
# 2. Главное приложение
# -------------------------------------------------------------------

class KeiaApp(App):

    CSS = """
    Screen {
        background: #008080;
        color: #2b1810;
    }

    Header {
        background: #3d5a80;
        color: #f5efdc;
        text-style: bold;
        height: 1;
    }
    Header .header--title { color: #f5efdc; }

    Footer {
        background: #008080;
        color: #c0d8d8;
    }
    Footer > .footer--key {
        background: #008080;
        color: #d4e8e8;
        text-style: bold;
    }
    Footer > .footer--description { color: #c0d8d8; }

    #main { height: 1fr; }

    .side {
        background: #d4c5a9;
        color: #2b1810;
        height: 1fr;
    }

    #left-panel {
        width: 22;
        padding: 1 0;
    }

    #right-panel {
        width: 26;
        padding: 1 1;
    }

    #menu-title {
        background: #d4c5a9;
        color: #3d5a80;
        padding: 0 2;
        height: auto;
    }

    /* Кнопки меню — плоские, как строки в Win95 menu */
    .menu-btn {
        background: #d4c5a9;
        color: #2b1810;
        border: none;
        height: 1;
        padding: 0 2;
        text-align: left;
        min-width: 0;
    }

    .menu-btn:hover {
        background: #3d5a80;
        color: #f5efdc;
    }

    .menu-btn:focus {
        background: #3d5a80;
        color: #f5efdc;
        text-style: bold;
    }

    #center-column {
        width: 1fr;
        height: 1fr;
    }

    #chat-scroll {
        width: 1fr;
        height: 1fr;
        padding: 1 1;
        scrollbar-size: 0 0;
        background: #008080;
    }

    #input {
        margin: 0 1 1 1;
        border: solid #3d5a80;
        background: #d4c5a9;
        color: #2b1810;
    }
    #input:focus { border: solid #5a7fa5; }
    #input > .input--placeholder { color: #8b7355; }
    #input > .input--cursor {
        background: #3d5a80;
        color: #f5efdc;
    }

    Message { margin: 0 0 1 0; }
    .msg-system { margin: 0 0 0 2; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Выход", show=True),
        Binding("ctrl+l", "clear_screen", "Очистить", show=True),
    ]

    def __init__(self):
        super().__init__()
        self.busy = False

    # -------------------------------------------------------------------
    # Разметка
    # -------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="main"):
            # Левая колонка — вертикальный стек кнопок.
            from textual.containers import Vertical as V
            with V(id="left-panel", classes="side"):
                yield Static("KEIA OS\n─────────", id="menu-title")
                yield Button("▸ Диалог", id="btn-dialog", classes="menu-btn")
                yield Button("  Память", id="btn-facts",  classes="menu-btn")
                yield Button("  Журнал", id="btn-journal", classes="menu-btn")
                yield Button("  О Кее",  id="btn-about",  classes="menu-btn")

            # Центр — чат + input.
            with V(id="center-column"):
                with VerticalScroll(id="chat-scroll"):
                    yield Message("Соединение установлено. Кея готова.", "system")
                yield Input(
                    placeholder="напиши сообщение...  (/help — команды)",
                    id="input",
                )

            # Правое — статус.
            yield Static(self._render_right(), id="right-panel", classes="side")

        yield Footer()

    # -------------------------------------------------------------------
    # Сайдбары (Rich-рендеры внутри Static)
    # -------------------------------------------------------------------

    def _render_right(self):
        """Правое окно — статистика. Бежевый фон."""
        try:
            f_text = facts.get_relevant(USER_ID, "", limit=100)
            facts_count = len(f_text.splitlines()) if f_text else 0
        except Exception:
            facts_count = 0

        try:
            hist_count = len(history.get(USER_ID, limit=100))
        except Exception:
            hist_count = 0

        now = datetime.now().strftime("%H:%M:%S")

        content = Text()
        content.append("СИСТЕМА\n", style="bold #3d5a80 on #d4c5a9")
        content.append("─────────\n\n", style="#8b7355 on #d4c5a9")

        content.append("МОДЕЛЬ\n", style="bold #8b4513 on #d4c5a9")
        content.append("qwen2.5-1.5b\n\n", style="#2b1810 on #d4c5a9")

        content.append("ПАМЯТЬ\n", style="bold #8b4513 on #d4c5a9")
        content.append(f"● фактов: {facts_count}\n", style="#2b1810 on #d4c5a9")
        content.append(f"● диалог: {hist_count}\n\n", style="#2b1810 on #d4c5a9")

        content.append("СЕССИЯ\n", style="bold #8b4513 on #d4c5a9")
        content.append(f"{now}\n", style="#2b1810 on #d4c5a9")

        return content

    def _update_right(self) -> None:
        """Обновить правый сайдбар."""
        try:
            panel = self.query_one("#right-panel", Static)
            panel.update(self._render_right())
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Обработка клика по кнопкам меню."""
        bid = event.button.id

        if bid == "btn-dialog":
            self.action_clear_screen()
            await self._add_system("Диалог — текущий экран.")

        elif bid == "btn-facts":
            relevant = facts.get_relevant(USER_ID, "", limit=100)
            if not relevant:
                await self._add_system("фактов пока нет")
            else:
                await self._add_message(relevant, "keia")

        elif bid == "btn-journal":
            await self._add_system("Журнал ещё не подключён (дневник в разработке)")

        elif bid == "btn-about":
            await self._add_system(
                "KEIA v2.0\n"
                "Модель: qwen2.5-1.5b\n"
                "Локальный собеседник на Arch Linux.\n"
                "Ядро: Python + llama.cpp + Textual"
            )

    # -------------------------------------------------------------------
    # Жизненный цикл
    # -------------------------------------------------------------------

    def on_mount(self) -> None:
        history.init()
        self.title = "KEIA"
        self.sub_title = "qwen2.5-1.5b"
        self.query_one("#input", Input).focus()
        self._update_right()

    # -------------------------------------------------------------------
    # Отправка
    # -------------------------------------------------------------------

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        if text.startswith("/"):
            await self._handle_command(text)
            return

        await self._send_to_keia(text)

    async def _send_to_keia(self, text: str) -> None:
        if self.busy:
            await self._add_system("Кея ещё думает, подожди...")
            return

        self.busy = True
        await self._add_message(text, "user")

        # "обработка..."
        thinking = Message("обработка...", "system")
        thinking.id = "thinking"
        chat = self.query_one("#chat-scroll", VerticalScroll)
        await chat.mount(thinking)
        chat.scroll_end(animate=False)

        try:
            reply = await agent.ask(text, USER_ID)
        except Exception as e:
            await self._remove_thinking()
            await self._add_system(f"ошибка: {e}")
            self.busy = False
            return

        await self._remove_thinking()
        await self._add_message(reply, "keia")

        # Обновляем статистику справа.
        self._update_right()

        self.busy = False

    # -------------------------------------------------------------------
    # Чат
    # -------------------------------------------------------------------

    async def _add_message(self, text: str, role: str) -> None:
        chat = self.query_one("#chat-scroll", VerticalScroll)
        msg = Message(text, role)
        await chat.mount(msg)
        chat.scroll_end(animate=False)

    async def _add_system(self, text: str) -> None:
        await self._add_message(text, "system")

    async def _remove_thinking(self) -> None:
        try:
            thinking = self.query_one("#thinking")
            await thinking.remove()
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Команды
    # -------------------------------------------------------------------

    async def _handle_command(self, text: str) -> None:
        cmd = text.strip().lower()

        if cmd in ("/exit", "/quit"):
            self.exit()

        elif cmd == "/reset":
            await history.clear(USER_ID)
            await self._add_system("история диалога очищена (факты сохранены)")
            self._update_right()

        elif cmd == "/facts":
            relevant = facts.get_relevant(USER_ID, "", limit=100)
            if not relevant:
                await self._add_system("фактов пока нет")
            else:
                await self._add_message(relevant, "keia")

        elif cmd == "/help":
            await self._add_system(
                "/reset  — очистить историю\n"
                "/facts  — показать память\n"
                "/exit   — выйти\n"
                "/help   — справка"
            )

        else:
            await self._add_system(f"неизвестная команда: {cmd}")

    # -------------------------------------------------------------------
    # Действия
    # -------------------------------------------------------------------

    def action_clear_screen(self) -> None:
        chat = self.query_one("#chat-scroll", VerticalScroll)
        for child in list(chat.children):
            child.remove()
        self.call_later(self._add_system, "экран очищен")


# -------------------------------------------------------------------
# Запуск
# -------------------------------------------------------------------

def run():
    app = KeiaApp()
    app.run()


if __name__ == "__main__":
    run()