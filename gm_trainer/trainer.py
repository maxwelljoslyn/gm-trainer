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

# NOTE Gradio sends telemetry and analytics:
# INFO:httpx:HTTP Request: GET https://checkip.amazonaws.com/ "HTTP/1.1 200 "
# INFO:httpx:HTTP Request: GET https://checkip.amazonaws.com/ "HTTP/1.1 200 "
# INFO:httpx:HTTP Request: POST https://api.gradio.app/gradio-initiated-analytics/ "HTTP/1.1 200 OK"
# INFO:httpx:HTTP Request: POST https://api.gradio.app/gradio-launched-telemetry/ "HTTP/1.1 200 OK"

logger = logging.getLogger(Path(__file__).name)
logging.basicConfig(level=None)

MODEL = llm.get_model("claude-3-opus")
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
            self.session.run_turn()
            for each in self.session.actions_this_round:
                print(each)
            self.session.narration = pt.prompt("GM: ")


class WebUI:
    def __init__(self, session):
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

    def launch(self):
        self.interface.launch()

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

    def run_turn(self, tries=3, backoff_duration: Seconds = 2):
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
            self.actions_this_round.append(player.format_response(response))

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


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.pass_context
def trainer(ctx):
    """Entry point to GM Trainer. This is a Click group, under which are the subcommands that do the real work."""
    # ensure that ctx.obj exists and is a dict,
    # in case this fn is called outside __main__
    ctx.ensure_object(dict)


@click.option(
    "-d",
    "--database-path",
    help="Path to database for storing session logs",
    default="logs.db",
)
@trainer.command()
def cli(database_path):
    session = GameSession(SCENARIO, sqlite_utils.Database(database_path))
    ui = CommandLineUI(session)
    ui.run()


@trainer.command()
def web():
    session = GameSession(SCENARIO)
    ui = WebUI(session)
    ui.launch()


if __name__ == "__main__":
    trainer()
