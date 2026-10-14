# ccpick

Browse and attach to [Claude Code](https://code.claude.com/docs) sessions on all your SSH hosts from one terminal list: running background sessions and the past conversations `claude --resume` would offer.

![ccpick listing sessions on three hosts](docs/screenshot.svg)

Claude Code's agent view (`claude agents`) lists the background sessions on the machine it runs on. ccpick asks each of your SSH hosts for that list and for its past conversations, shows them together, and attaches to the one you pick over SSH. A past conversation continues as a background session. With [claude-swap](https://github.com/realiti4/claude-swap) on a host, it lists every account's sessions and lets you choose the account for new ones. Sessions keep running on their host when you detach or your connection drops.

## Requirements

- Python 3.10+ and OpenSSH on your machine
- On each host: Claude Code with agent view (`claude agents --json` works), `python3` (to read past conversations), and an SSH login without a prompt (a key in your agent), since ccpick lists hosts with `BatchMode=yes`

## Install

```sh
uv tool install git+https://github.com/sdraeger/ccpick
# or
pipx install git+https://github.com/sdraeger/ccpick
```

## Usage

```sh
ccpick gpu1 devbox                 # sessions on these hosts
export CCPICK_HOSTS="gpu1 devbox"  # default hosts
ccpick --list gpu1                 # tab-separated: host, account, id, state, name, cwd
```

| Key | Action |
| :- | :- |
| typing | filter by host, account, state, name or directory (every word must match) |
| `↑` `↓` | move |
| `Enter` | attach to a background session, or continue a past conversation in the background and attach |
| `Ctrl+N` | start a session on the selected host: opens its agent view, after asking which account when it has several |
| `Ctrl+S` | swap the selected host's default cswap account (`cswap switch`); the first other account is preselected, so `Ctrl+S` `Enter` swaps |
| `Ctrl+R` | refresh |
| `Esc` | quit |

In an attached session, `Ctrl+Z` returns to ccpick and `←` opens the host's agent view (`Esc` there also returns to ccpick). The session keeps running either way.

## Multiple accounts (cswap)

If a host has [claude-swap](https://github.com/realiti4/claude-swap) (`cswap`), each of its accounts has its own list of background sessions there. ccpick then:

- lists the sessions of every account, with the account in its own column
- attaches through `cswap run <n> -- attach <id>`, so a session keeps its account
- asks which account a new session should use, showing each account's 5-hour and 7-day usage and which one is the host's default login
- swaps the host's default account with `Ctrl+S`. Sessions on the default login, and plain `claude` on that host, move to the new account; sessions started on another account stay on it

Listing an account that isn't the default runs `cswap run <n>`, which sets up that account's session profile on the host the first time. Hosts without cswap use plain `claude`.

## Notes

- Past conversations (state `past`) come from Claude Code's transcripts on the host, newest first. `Enter` runs `claude --bg --resume <id>` in the conversation's directory and attaches, so the conversation survives disconnects and shows up in agent view afterward. Transcripts aren't a stable Claude Code interface; ccpick reads only titles, directories and modification times from them.
- A session running right now in a terminal (plain `claude`) belongs to that terminal and can't be attached; it shows up as a past conversation once it ends. Send it to the background with `/bg`, or run `/config defaultToAgentsView=true` on the host so new sessions start in the background.
- Commands run through the host's login shell (`$SHELL -lic`), so `PATH` and proxy settings from your profile apply.
- If the connection drops while you're attached, ccpick reconnects every 3 seconds (`Ctrl+C` gives up). Add `ServerAliveInterval 10` and `ServerAliveCountMax 3` to the host in `~/.ssh/config` so a dead connection is noticed within about 30 seconds.

## Development

```sh
uv sync
uv run pytest
```

ccpick is not affiliated with Anthropic. MIT license.
