"""Textual pilot tests for the forge tui."""
import asyncio

from textual.widgets import Input, ListItem, ListView

from forge.tui import ForgeTUI


def test_command_output_and_survival(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def scenario():
        def cli_main(argv):
            print("ran:", " ".join(argv))
            if argv[0] == "boom":
                raise RuntimeError("kaboom")

        app = ForgeTUI(cli_main)
        async with app.run_test() as pilot:
            inp = app.query_one(Input)
            inp.value = "hello world"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert "ran: hello world" in app.emitted

            # a failing command must not exit the app
            inp.value = "boom"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert "error: kaboom" in app.emitted

            inp.value = "exit"
            await pilot.press("enter")
            await pilot.pause(0.1)

    asyncio.run(scenario())


def test_completion_popup_and_tab(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "csms.yaml").write_text("service: csms\n")

    async def scenario():
        app = ForgeTUI(lambda argv: print("ran", argv))
        async with app.run_test() as pilot:
            inp = app.query_one(Input)
            inp.value = "cs"
            await pilot.pause()
            box = app.query_one("#completion", ListView)
            assert box.display
            candidates = [getattr(i, "candidate", None) for i in box.children if isinstance(i, ListItem)]
            assert "csms" in candidates

            # TAB completes into the input
            await pilot.press("tab")
            await pilot.pause()
            assert inp.value == "csms"
            assert not box.display

    asyncio.run(scenario())
