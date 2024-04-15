from dataclasses import dataclass, field
from time import sleep
from copy import deepcopy
from textwrap import dedent
import os
import logging
from pathlib import Path

import llm
import click
import sqlite_utils
from ulid import ULID

logger = logging.getLogger(Path(__file__).name)
logging.basicConfig(level=logging.DEBUG)

api_key = os.getenv("GM_TRAINER_OPUS_API_KEY")
MODEL = llm.get_model("claude-3-opus")
MODEL.key = api_key


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
players = [alice, bob]


@dataclass
class GameSession:
    db: sqlite_utils.Database
    narration: str
    call_to_action: str = "What do you do?"
    id: str = str(ULID()).lower()
    # TODO learn to use field here; I cargo-culted field(default_factory=lambda: players) but that seems wrong

    def __post_init__(self):
        self.actions_this_round: list[str] = list()
        self.actions_previous_round: list[str] = list()
        self.players = deepcopy(players)

    def make_gm_dialogue(self, text):
        return f"GM: {text}\n{self.call_to_action}"

    def gm_turn(self):
        print(self.make_gm_dialogue(self.narration))

    def make_player_prompt(self, p: Player):
        # In the case that this is the first round and no player has
        # taken her turn yet, this reduces to just giving the
        # initially-provided narration.
        return "\n".join(
            [
                *self.actions_previous_round,
                self.make_gm_dialogue(self.narration),
                *self.actions_this_round,
            ]
        )

    def run_turn(self):
        is_first_turn_and_first_round = (
            not self.actions_this_round and not self.actions_previous_round
        )
        if is_first_turn_and_first_round:
            self.gm_turn()
        for player in players:
            prompt = self.make_player_prompt(player)
            backoff = 2
            while True:
                try:
                    response = player.conversation.prompt(
                        prompt, system=self.system_prompt(player)
                    )
                    break
                except Exception:
                    # guard againt overloaded APIs, etc.
                    logging.debug(
                        f"API or other error with generating response. Waiting {backoff} seconds before trying again."
                    )
                    sleep(backoff)
                    backoff *= 2
            # TODO need to include the session id, etc. here
            response.log_to_db(self.db)
            self.actions_this_round.append(player.format_response(response))
            print(player.format_response(response))
        gm_input = input("GM: ")
        self.narration = gm_input
        self.actions_previous_round = deepcopy(self.actions_this_round)
        self.actions_this_round = []

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
        You are {p.name}, a player participating in a roleplaying game
        session. Your character is {p.pc.display_details()}. Your fellow players are:
        {self.describe_other_players(p)}

        The user is the Game Master (GM) of the session. The GM will
        describe a scenario to you, then ask you what you want to do. You
        can declare an action for your character, or ask questions of the
        GM until you're ready to declare an action. You must cooperate
        with your fellow players, acting as a team through your characters
        to accomplish shared goals.

        Further instructions:
        DO respond with a declarative sentence describing each action you take.
        DO respond with questions for the GM, especially if you aren't sure what skills, items, or knowledge you possess.
        DO respond with questions or comments for your fellow players.

        DO NOT yap. No yapping.
        DO NOT give more than one action declaration, or question, or comment, at a time.
        DO NOT assume that you possess any skills, items, or knowledge unless the GM says so.
        DO NOT attempt to describe any game scenario elements.
        DO NOT attempt to describe the actions of other characters.
        DO NOT speak in stage directions surrounded by asterisks, *like this*."""
        ).strip()

    def game_loop(self):
        while True:
            self.run_turn()


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.pass_context
def cli(ctx):
    """Entry point to GM Trainer's CLI. This is a Click group, under which are the subcommands that do the real work."""
    # ensure that ctx.obj exists and is a dict,
    # in case `buddy()` called outside __main__
    ctx.ensure_object(dict)


@click.option(
    "-d",
    "--database-path",
    help="Path to database for storing session logs",
    default="logs.db",
)
@cli.command
def start(database_path):
    session = GameSession(
        sqlite_utils.Database(database_path),
        dedent(
            """
            The year is 1651. You and your companions woke up dawn
            and traveled into the foothills of the mountains of
            Tenerife, the most important of the Canary Islands. Now
            you stand before a cave whose opening is as tall as two
            men and as wide as a wagon. You've been told that before
            these islands were conquered by the Spanish, the
            indigenous Guanches (who still exist) would bury their
            mummified dead in caverns like this."""
        ).strip(),
    )
    session.game_loop()
