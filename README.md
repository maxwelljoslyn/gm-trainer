## Introduction

GM Trainer offers RPG Game Masters (GMs) a situated learning environment for practicing spontaneous creativity.

It uses LLMs to simulate human players' responses to the GM's descriptive narration and each other's actions. This lets GMs exercise critical skills like improvising descriptions, decisively answering player questions, and giving logical game-world responses to player actions.

GM Trainer can be used as a CLI app or through a web interface.

## Rationale

At the intersection of RPGs and AI, **most current work is focused on attempting to replace human GMs** -- but a GM is responsible for real-time creation and evolution of an enormous, self-consistent game world, which AIs are nowhere close to achieving.

Being dissatisfied with what I saw in that space, I thought perhaps AI could simulate an RPG player sufficiently well to provide lifelike practice for GMs who want to work on their presentational skills. While there are still rough edges, I'm starting to be convinced that this interaction paradigm could have a real impact on how people learn to acquire the nuanced, many-faceted skill of GMing!

## How It Works

Assuming two LLM players, here's a summary of operation:

1. The GM gives an initial scenario description.
1. The scenario is sent to LLM player 1, who responds.
1. The scenario *and player 1's response* are sent to player 2, allowing player 1 to react not only to the scenario, but also to what player 1 did.
1. The GM explains how the players' actions changed the scenario. This explanation, along with both players' previous actions, forms the updated scenario.
1. The whole process repeats from step 2.

Example prompts coming soon.

## Screenshots

### Web UI

<img width="1372" alt="screenshot" src="https://github.com/maxwelljoslyn/gm-trainer/assets/11641081/0c5030af-c97a-47d2-bc20-c794d1d1f88f">

### Command Line UI

<img width="937" alt="cli-ui-screenshot" src="https://github.com/maxwelljoslyn/gm-trainer/assets/11641081/52a95a50-e66a-425f-967a-e82ff37bb08f">

## Installation and Setup

1. Clone this repo.
1. `cd gm-trainer`
1. `poetry install`
1. `touch .env`
1. Edit `.env` to add an LLM provider key as an environment variable. Currently only Claude Opus is supported, using the variable `GM_TRAINER_OPUS_API_KEY`.

## Usage

`poetry run python3 -m gm_trainer [OPTIONS]`

`--version`

Show the version and exit.

`-d,` `--database-path TEXT`

Path to SQLite database for storing session logs (default: './logs.db'). If no database exists at that path, one will be created.

`-u`, `--user-interface [cli|web]`

Which user interface to use.

`--port INTEGER`

Port at which to serve the web UI. If the command-line UI is used, this argument is ignored.
