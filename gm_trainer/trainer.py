from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from time import sleep
import logging
import os

import click
import gradio as gr
import llm
import prompt_toolkit as pt
import sqlite_utils
from ulid import ULID

# NOTE Gradio sends telemetry and analytics by default.
# Supposedly I've turned it off with the `analytics_enabled=False` argument to gr.Interface, but be wary.
# It hit these URLs, among others:
# INFO:httpx:HTTP Request: GET https://checkip.amazonaws.com/ "HTTP/1.1 200 "
# INFO:httpx:HTTP Request: POST https://api.gradio.app/gradio-initiated-analytics/ "HTTP/1.1 200 OK"
# INFO:httpx:HTTP Request: POST https://api.gradio.app/gradio-launched-telemetry/ "HTTP/1.1 200 OK"

logger = logging.getLogger(Path(__file__).name)
logging.basicConfig(level=None)

MODEL = llm.get_model("claude-3.5-sonnet")
MODEL.key = os.getenv("GM_TRAINER_OPUS_API_KEY")

SCENARIO = """The year is 1651. You and your companions woke up dawn and traveled into the foothills of the mountains of Tenerife, the most important of the Canary Islands. Now you stand before a cave whose opening is as tall as two men and as wide as a wagon. You've been told that before these islands were conquered by the Spanish, the indigenous Guanches (who still exist) would bury their mummified dead in caverns like this."""


@dataclass
class PlayerCharacter:
    name: str
    character_class: str
    level: int = 1
    spells: list[str] | None = None

    def display_details(self):
        result = [f"{self.name}", f"Level {self.level} {self.character_class}"]
        if self.spells:
            spell_display = ", ".join(self.spells)
            result.append("".join(["Spells:", spell_display]))
        return "\n".join(result)


@dataclass
class Player:
    name: str
    pc: PlayerCharacter

    def __post_init__(self):
        """Set up the LLM conversation."""
        self.conversation = MODEL.conversation()

    def format_response(self, response):
        return f"{self.pc.name}: {response.text()}"


arvak = PlayerCharacter("Arvak", "fighter", 2)
bolzar = PlayerCharacter(
    "Bolzar", "mage", 3, ["Witchbolt", "Protective Aura", "Levitate", "Sleep"]
)
alice = Player("Alice", arvak)
bob = Player("Bob", bolzar)
PLAYERS = [alice, bob]


class CommandLineUI:
    def __init__(self, session):
        self.session = session

    def run(self):
        print(f"GM: {self.session.narration}")
        while True:
            self.session.run_turn(display_fn=print)
            self.session.narration = pt.prompt("GM: ")


class WebUI:
    def __init__(self, session, port: int | None = None):
        self.port = port
        self.session = session
        # To display all player responses in one window, we have to
        # use a single chatbot output component into which we can mix
        # all player responses.
        self.chat_history = []
        self.dummy_chatbot = gr.Chatbot(label="History", height="100vh")
        self.interface = gr.Interface(
            fn=self.accept_input,
            inputs=gr.Textbox(lines=2, label="GM Input", value=self.session.narration),
            outputs=self.dummy_chatbot,
            title="Game Master Trainer",
            allow_flagging="never",
            analytics_enabled=False,
        )

    def run(self):
        """Run the web UI server on localhost. If self.port is None, fall back to Gradio's default of 7680."""
        self.interface.launch(server_port=self.port)

    def accept_input(self, gm_input):
        self.session.narration = gm_input
        self.chat_history.append((self.session.narration, None))
        # immediately show GM's input
        # you have to yield the whole chat history for all of it to display; otherwise only the latest item gets displayed
        yield self.chat_history
        # get player responses
        self.session.run_turn()
        self.chat_history.extend(
            [(None, action) for action in self.session.actions_this_round]
        )
        # show player responses
        yield self.chat_history


Seconds = int


class GameSession:
    def __init__(self, narration, db=None):
        self.narration = narration
        self.db = db
        self.actions_this_round = []
        self.actions_previous_round = []
        self.id = str(ULID()).lower()
        self.players = deepcopy(PLAYERS)

    def run_turn(self, *, tries=3, backoff_duration: Seconds = 2, display_fn=None):
        self.actions_previous_round = deepcopy(self.actions_this_round)
        self.actions_this_round = []
        for player in self.players:
            prompt = self.make_player_prompt(player)
            while True and tries > 0:
                try:
                    response = player.conversation.prompt(
                        prompt, system=self.system_prompt(player)
                    )
                    break
                except Exception:
                    # Guard againt API call failures, etc.
                    # Can't catch a more specific Exception subclass: see #2
                    tries -= 1
                    if tries == 0:
                        raise Exception(
                            f"Ran out of tries while generating response for player {player}"
                        )
                    logger.debug(
                        f"API failure or other error while generating response. Waiting {backoff_duration} seconds before trying again ({tries} tries left.)"
                    )
                    sleep(backoff_duration)
                    backoff_duration *= 2
            if self.db:
                response.log_to_db(self.db)
            resp = player.format_response(response)
            if display_fn:
                display_fn(resp)
            self.actions_this_round.append(resp)

    def make_player_prompt(self, p: Player):
        # In the case that this is the first round and no player has
        # taken her turn yet, this reduces to just giving the
        # initially-provided narration.
        return "\n".join(
            [
                *self.actions_previous_round,
                f"GM: {self.narration}",
                *self.actions_this_round,
            ]
        )

    def describe_other_players(self, p: Player):
        return "\n".join(
            [
                f"{other.name}, playing {other.pc.display_details()}"
                for other in self.players
                if p.name != other.name
            ]
        )

    def system_prompt(self, p: Player):
        return dedent(
            f"""
        You, {p.name}, are playing a tabletop RPG. Your character is {p.pc.display_details()}. Your fellow player-characters are:
        {self.describe_other_players(p)}
        The Game Master (GM) of the session will describe a scenario to you.
        You will:
        1. Ask questions of the GM. (optional)
        2. Talk with your fellow players. (optional)
        3. Declaratively state what you want your character to do. (mandatory)

        Always follow these further instructions:
        No yapping or preambles.
        No saying more than one logical thing at a time.
        No assuming that you possess any skills, items, or knowledge without confirming by asking the GM.
        No describing any game scenario elements that aren't about your character.
        No describing other character's.
        Never surround outputs with asterisks, *like this*."""
        ).strip()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.command(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.option(
    "-d",
    "--database-path",
    default="logs.db",
    help="Path to SQLite database for storing session logs (default: './logs.db'). If no database exists at that path, one will be created.",
)
@click.option(
    "-u",
    "--user-interface",
    "arg_ui",
    prompt=True,
    type=click.Choice(["cli", "web"]),
    help="Which user interface to use.",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="Port at which to serve the web UI. If the command-line UI is used, this argument is ignored.",
)
def trainer(database_path, arg_ui, port):
    """Entry point to GM Trainer."""
    session = GameSession(SCENARIO, sqlite_utils.Database(database_path))
    if arg_ui == "web":
        ui = WebUI(session, port)
    else:
        ui = CommandLineUI(session)
    ui.run()


if __name__ == "__main__":
    trainer()
