Scrinium: installation
======================

You need about 4 GB of free disk space and, preferably, 8 GB of memory or more.
You do not need to be an administrator. The first time, it downloads about
3.2 GB (tools and local models).

Linux and macOS, in a terminal inside this folder:

    sh install.sh

Windows, in PowerShell inside this folder:

    powershell -ExecutionPolicy Bypass -File install.ps1

Then, to open Scrinium:

    scrinium start

If something fails, `scrinium doctor` says what is missing and how to fix it.
To stop it: `scrinium stop`. To watch everything it does live: `scrinium attach`.
To uninstall everything, data included: `scrinium uninstall`.
