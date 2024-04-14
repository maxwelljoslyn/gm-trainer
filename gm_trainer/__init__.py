import llm
from textwrap import dedent
import os

from dataclasses import dataclass

api_key = os.getenv("GM_TRAINER_OPUS_API_KEY")
MODEL = llm.get_model("claude-3-opus")
MODEL.key = api_key


@dataclass
class Player:
    name: str
    pc_name: str
    pc_level: int
    pc_class: str

    def __post_init__(self):
        """Set up the LLM conversation."""
        self.conversation = MODEL.conversation()

    def format_pc(self):
        return f"{self.pc_name}, a level {self.pc_level} {self.pc_class}"

    def format_response(self, response):
        return f"{self.pc_name}: {response.text()}"


players = [Player("Alice", "Arvak", 2, "fighter"), Player("Bob", "Bolzar", 3, "mage")]


def other_players_prompt(other_players):
    return "\n".join([f"{p.name}, playing {p.format_pc()}" for p in other_players])


def system_prompt(p: Player):
    other_players = [v for v in players if p.name != v.name]
    return dedent(
        f"""
    You are {p.name}, a player participating in a roleplaying game
    session. Your character is {p.pc_name}, a level {p.pc_level}
    {p.pc_class}. Your fellow players include:
    {other_players_prompt(other_players)}

    The user is the Game Master (GM) of the session. The GM will
    describe a scenario to you, then ask you what you want to do. You
    can declare an action for your character, or ask questions of the
    GM until you're ready to declare an action. You must cooperate
    with your fellow players, acting as a team through your characters
    to accomplish shared goals.

    Respond with one declarative sentence or one question per prompt.
    No yapping. Do not describe more than one action, or give more
    than one question. Do not describe any game scenario elements, nor
    the actions of other characters."""
    ).strip()


def scenario_prompt(text):
    return f"GM: {text}\n\nWhat do you do?"


def player_turn(scenario, initial=False):
    if initial:
        print(scenario_prompt(scenario))
    for player in players:
        response = player.conversation.prompt(
            scenario_prompt(scenario), system=system_prompt(player)
        )
        scenario = f"{scenario}\n{player.format_response(response)}"
        print(player.format_response(response))
    return scenario


initial_scenario = dedent(
    f"""
The year is 1651. You and your companions woke up dawn and traveled
into the foothills of the mountains of Tenerife, the most important of
the Canary Islands. Now you stand before a cave whose opening is as
tall as two men and as wide as a wagon. You've been told that before
these islands were conquered by the Spanish, the indigenous Guanches
(who still exist) would bury their mummified dead in caverns like
this."""
).strip()


def game_loop(current_scenario):
    current_scenario = player_turn(current_scenario, initial=True)
    while True:
        what_happens = input("GM: ")
        # get all N players' most recent response to append...
        current_scenario = player_turn(what_happens)


game_loop(initial_scenario)
