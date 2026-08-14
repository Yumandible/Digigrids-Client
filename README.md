# Digigrids-Client

A system tray application for **Windows and Linux** that automatically sends new
FT8 / FT4 QSO (contact) records to [digigrids.net](https://digigrids.net), a
grid-square tracking site for Ham radio operators, for registered users of the
site. The site is in Beta and is free.

## What it does

The client runs quietly in the system tray. It watches for new QSO records
logged by your amateur radio digital modes software (FT8, FT4) and sends each
valid new contact to digigrids.net in real time, so registered users can see a
live stream of the QSO data that is used for the leaderboards and live award
tracking. If digigrids.net is unreachable, contacts are held in a retry queue
and sent automatically later.

## Getting started

The easiest way to get started, as long as you are a licensed ham radio
operator, is to register at <https://digigrids.net> and log in. Then go to the
**QSO Stream** menu item and you will see links to the installer downloads.

You can also download the installers directly from this repository's
[Releases page](../../releases/latest):

| System | File | Install |
|---|---|---|
| Windows 10 or later | `digigridsInstaller.exe` | Download and run |
| Linux (Ubuntu / Mint / Debian) | `digigrids-client_3.0.3_all.deb` | See below |

### Linux install

Download the `.deb`, then in a terminal (from your Downloads folder):

    sudo apt install ./digigrids-client_3.0.3_all.deb

All required components are installed automatically. Launch **Digigrids
Client** from your applications menu.

> **Ubuntu (GNOME) note:** if no tray icon appears, run
> `sudo apt install gnome-shell-extension-appindicator`, then log out and back
> in. Most other desktops (Mint, Xubuntu, KDE) have tray support built in.

## Running from source instead

If you have git installed you can run the client directly from this
repository.

**Windows** (Python 3.11.9 tested — see `windows/requirements.txt` for 3rd
party imports):

```
git clone https://github.com/Yumandible/digigrids-client.git
cd digigrids-client/windows
pip install -r requirements.txt
python digigrids-client-v3.py
```

Make sure you're in the `windows` folder before running — the script needs to
find `digigrids_multi.ico` in the same folder it's run from.

**Linux** (dependencies come from your distribution's packages):

```
sudo apt install python3-pystray python3-pil python3-requests python3-tk libnotify-bin
git clone https://github.com/Yumandible/digigrids-client.git
cd digigrids-client/linux
python3 digigrids_client.py
```

## Usage

Whether you used the installer or ran from source, the client appears as an
icon in your system tray. On first run you will be prompted to enter the path
to your ADIF file for the software you use for FT8 / FT4 communication, and
your API key obtained from digigrids.net after registering there (via the QSO
Stream menu item).

Right-click the tray icon to see the options. Before the client will send data
to your account on digigrids.net, make sure the **watcher** is started (it
starts automatically once the client is configured).

Settings, logs and the retry queue are stored in:

- **Windows:** `%LOCALAPPDATA%\Digigrids\`
- **Linux:** `~/.config/digigrids/`

## Repository layout

- `windows/` — Windows client source and icon
- `linux/` — Linux client source and icon

Both clients share the same core logic and talk to the same digigrids.net API.
