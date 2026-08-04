# Digigrids-Client
A Windows system tray application that automatically sends new FT8 / FT4 QSO (contact) records to digigrids.net, a grid-square tracking site for Ham radio operators for registered users of the site. The site is in Beta and is free.

What it does.


The client runs quietly in the Windows system tray.
Watches for new QSO records logged in your amateur radio digital modes software (FT8, FT4)
Sends each valid new contact to digigrids.net in real time, so registered users csan see a live stream of the qso data that is used for the leaderboards and live award tracking.

Requirements
Windows 10 or later
Python 3
see requirements.txt for 3rd party imports

Installation

The **easiest way to get started** is to register at https://digigrids.net and login. Then go to the QSO stream menu item and you will see a link to the installer download.

Or you can:
```
git clone https://github.com/Yumandible/digigrids-client.git
cd digigrids-client
pip install -r requirements.txt
```
Usage

When run, the client will appear as an icon in your system tray. On first run you will be prompted to enter the path to your adif file for the software you use for FT8 / FT4 communication, and your API key obtained from digigrids.net after registering there via the QSO Stream menu item. 


Then if you right click the client icon in the system tray you will see some options. Before it will send data to your account at the digigrids.net website you will need to ensure the 'watcher' is started. 


