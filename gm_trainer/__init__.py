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


alice = Player("Alice", "Arvak", 2, "fighter")
bob = Player("Bob", "Bolzar", 3, "mage")


players = [alice, bob]


def other_players_prompt(other_players):
    return "\n".join([f"{p.name}, playing {p.format_pc()}" for p in other_players])


def system_prompt(p: Player):
    other_players = [v for v in players if v.name != v.name]
    return dedent(
        f"""
    You are {p.name}, a player participating in a roleplaying game session. Your character is {p.pc_name}, a level {p.pc_level} {p.pc_class}. Your fellow players include:
    {other_players_prompt(other_players)}

    The user is the Game Master (GM) of the session. The GM will describe a scenario to you, then ask you what you want to do. You can declare an action for your character, or ask questions of the GM until you're ready to declare an action. You must cooperate with your fellow players, acting as a team through your characters to accomplish shared goals.

    Respond with one declarative sentence or one question per prompt. No yapping. Do not describe more than one action, or give more than one question. Do not describe any game scenario elements, nor the actions of other characters."""
    )


scenario = f"""
The year is 1651. You and your companions woke up dawn and traveled into the foothills of the mountains of Tenerife, the most important of the Canary Islands. Now you stand before a cave whose opening is as tall as two men and as wide as a wagon. You've been told that before these islands were conquered by the Spanish, the indigenous Guanches (who still exist) would bury their mummified dead in caverns like this."""


def scenario_prompt(text: str):
    return dedent(
        f"""
    GM: {text}

    What do you do?"""
    )


def player_turn(scenario):
    print(scenario_prompt(scenario))
    p1, p2 = players[0], players[1]
    response = p1.conversation.prompt(
        scenario_prompt(scenario), system=system_prompt(p1)
    )
    updated_scenario = "\n".join([scenario, p1.format_response(response)])
    next_response = p2.conversation.prompt(
        scenario_prompt(updated_scenario), system=system_prompt(p2)
    )
    final_scenario = "\n".join([updated_scenario, p2.format_response(next_response)])
    print(p1.format_response(response))
    print(p2.format_response(next_response))
    return final_scenario


def game_loop():
    first_turn = player_turn(scenario)
    # TODO have to make sure that the player two response makes it to player one.
    what_happens = input("What happens, GM?")
    second_turn = player_turn(what_happens)


game_loop()
