import logging
import os
import random
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from time import sleep
from typing import Optional

import click
import gradio as gr
import llm
import prompt_toolkit as pt
import sqlite_utils
from llm import Conversation, Response
from ulid import ULID

from gm_trainer.shared import PROJECT_ROOT

# NOTE Gradio sends telemetry and analytics by default.
# Supposedly I've turned it off with the `analytics_enabled=False` argument to gr.Interface, but be wary.
# It hit these URLs, among others:
# INFO:httpx:HTTP Request: GET https://checkip.amazonaws.com/ "HTTP/1.1 200 "
# INFO:httpx:HTTP Request: POST https://api.gradio.app/gradio-initiated-analytics/ "HTTP/1.1 200 OK"
# INFO:httpx:HTTP Request: POST https://api.gradio.app/gradio-launched-telemetry/ "HTTP/1.1 200 OK"

logger = logging.getLogger(Path(__file__).name)
logging.basicConfig(level=None)

MODEL = llm.get_model("claude-3.5-sonnet")
MODEL.key = os.getenv("GM_TRAINER_API_KEY")

SCENARIO = """The year is 1651. You and your companions woke up dawn and traveled into the foothills of the mountains of Tenerife, the most important of the Canary Islands. Now you stand before a cave whose opening is as tall as two men and as wide as a wagon. You've been told that before these islands were conquered by the Spanish, the indigenous Guanches (who still exist) would bury their mummified dead in caverns like this."""

num_conversations = 0


def load_conversation(db, conversation_id: str):
    """Based on function of the same name in the llm package's cli.py.
    See https://github.com/simonw/llm/blob/96db13f53774154a10fde9f41e659937ebe2ea01/llm/cli.py#L449C23-L449C81"""
    # load the most recent row regarding this conversation
    try:
        # it's not clear to me why the conversations table contains
        # multiple rows for a given conversation, but it's something to do
        # with the fact that the name column changes with each new prompt
        # for a response that comes in the conversation.
        row = list(
            db["conversations"].rows_where(
                "id = ?", [conversation_id], order_by="rowid desc", limit=1
            )
        )[0]
    except (IndexError, sqlite_utils.db.NotFoundError):
        raise ValueError("No conversation found with id={}".format(conversation_id))
    # turn that response into a conversation
    conversation = Conversation.from_row(row)
    # load its previous responses
    for response in db["responses"].rows_where(
        "conversation_id = ?", [conversation_id]
    ):
        conversation.responses.append(Response.from_row(response))
    return conversation


class RandomIterator:
    def __init__(self, iterable):
        self.items = list(iterable)
        random.shuffle(self.items)

    def __iter__(self):
        return self

    def __next__(self):
        if not self.items:
            raise StopIteration
        return self.items.pop()


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
    db: sqlite_utils.Database
    conversation_id: Optional[str] = None

    def __post_init__(self):
        """Set up the LLM conversation."""
        global num_conversations
        num_conversations += 1
        if self.conversation_id:
            self.conversation = load_conversation(self.db, self.conversation_id)
            print(
                f"loaded old conversation for {self.name}; there are now {num_conversations} convos"
            )
        else:
            self.conversation = MODEL.conversation()
            print(
                f"created new conversation for {self.name}; there are now {num_conversations} convos"
            )

    def format_response(self, response):
        return f"{self.pc.name}: {response.text()}"


def default_players(db, conversations=None):
    if not conversations:
        conversations = {}
    andrew = PlayerCharacter("Andrew", "assassin", 2)
    alice = Player("Alice", andrew, db, conversation_id=conversations.get("Alice"))
    benjamin = PlayerCharacter("Benjamin", "mage", 1, ["Sleep", "Unseen Servant"])
    bob = Player("Bob", benjamin, db, conversation_id=conversations.get("Bob"))
    carlos = PlayerCharacter(
        "Carlos", "priest (Catholic)", 1, ["Cure Light Wounds", "Light"]
    )
    charles = Player(
        "Charles", carlos, db, conversation_id=conversations.get("Charles")
    )
    darby = PlayerCharacter("Darby", "thief", 1)
    dan = Player("Dan", darby, db, conversation_id=conversations.get("Dan"))
    return [alice, bob, charles, dan]


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
    def __init__(self, narration, db=None, conversations=None):
        self.narration = narration
        self.db = db
        self.actions_this_round = []
        self.actions_previous_round = []
        self.id = str(ULID()).lower()
        # TODO oh crap, reloading a session isn't just a matter of
        # loading conversations for all of the players: we also need
        # to set self.actions_previous_round somehow!
        # and, perhaps, have self.make_player_prompt use something
        # other than the default starting narration in order to tell
        # the player that they are continuing play.
        # Buuuut ...  let's just try it for now.
        # LLMs are good at recovering from weird stuff.
        if conversations:  # TODO need to also check if loading happened successfully...
            self.narration = "We continue playing where we left off."
        self.players = default_players(db, conversations)

    def run_turn(
        self,
        *,
        tries=3,
        backoff_duration: Seconds = 2,
        display_fn=None,
        random_turn_order=True,
    ):
        self.actions_previous_round = deepcopy(self.actions_this_round)
        self.actions_this_round = []
        order = RandomIterator(self.players) if random_turn_order else self.players
        for player in order:
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

    def players_except(self, p: Player):
        return [each for each in self.players if each is not p]

    def describe_other_players(self, p: Player):
        return "\n".join(
            [
                f"{other.name}, playing {other.pc.display_details()}"
                for other in self.players_except(p)
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
        Do not yap, preface, or ramble.
        Do your best to say only one sentence at a time.
        Do not assume that your character possess any skills, items, or knowledge: always ask the GM.
        Do not assume anything about the game scenario: always ask the GM. 
        Do not surround outputs with asterisks, *like this*.

            Examples:
            <example>
            <input>
            GM: {p.pc.name}, the last orc collapses after you hit it with your sword.
            </input>
            <output>
            I rush over to the corpse and search it.
            </output>
            </example>

            <example>
            <input>
            GM: You hear the sounds of clashing weapons over the horizon, and then the dull whomp of a fireball. A few seconds later, a saddled but riderless horse comes running over the nearest hill.
            Krandahar: I move toward the horse at a jog.
            </input>
            <output>
            I follow after Krandahar, drawing my enchanted sword as I do so.
            </output>
            </example>
            """
        ).strip()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.command(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.option(
    "-d",
    "--database-path",
    default=PROJECT_ROOT / "logs.db",
    help="Path to SQLite database for storing session logs (default: './logs.db'). If no database exists at that path, one will be created.",
)
@click.option(
    "-u",
    "--ui",
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
@click.option(
    "-c",
    "--conversation",
    type=(str, str),
    default=None,
    nargs=2,  # this many values expected per invocation of option
    multiple=True,  # option can be passed multiple times
    help=dedent(
        """
    Pair of player name and conversation ID. Can be used to resume a set of sessions.
    Example: --conversation Alice cdzj33l2j0djfl3j"""
    ),
)
def trainer(database_path, arg_ui, port, conversation):
    """Entry point to GM Trainer."""
    conversations = dict(conversation) if conversation else {}
    session = GameSession(SCENARIO, sqlite_utils.Database(database_path), conversations)
    if arg_ui == "web":
        ui = WebUI(session, port)
    else:
        ui = CommandLineUI(session)
    ui.run()


if __name__ == "__main__":
    trainer()
