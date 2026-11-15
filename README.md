# Jarvis desktop assistant

A Linux desktop assistant with a PyQt6 command bar, voice support, local model integration, optional cloud inference, and desktop controls.

This distribution contains desktop source code only. Phone access and remote pairing are omitted. It contains no saved account connections, credentials, conversation history, personal memory, voice recordings, screenshots, machine backups, model downloads, or previous Git history.

## Setup

Clone or extract this project to `~/jarvis`. Inspect installation scripts before running them: they install user services and configure Cinnamon desktop shortcuts and wallpaper.

Install Python 3 with venv support and the system tools listed by `bootstrap.sh`. Run `./bootstrap.sh` to install Python dependencies, download models and build llama.cpp with Vulkan support. Model downloads have their own licenses. Then run `./install-user.sh` for the Cinnamon setup. GNOME integration is provided in `desktop/`, `desktop-controls/`, and the matching scripts; desktop behavior depends on the installed shell version.

Credentials are configured locally by each user and must never be committed. Account and Spotify integration code is generic and has no connected accounts included. Optional account dependencies are in `requirements-accounts.txt`.

This source export has passed syntax and privacy pattern checks; installation and hardware behavior have not been tested on a clean machine.
