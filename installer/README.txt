Scrinium: installation
======================

You need about 2 GB of free disk space and 4 GB of memory. You do not need to be
an administrator. The first time, it downloads about 0.7 GB: tools and the
search model. When Scrinium opens, you choose which model writes the rules: your
own provider (recommended), a local server, or isolated mode. Scrinium's own
local model is optional (2.5 GB more, best with a GPU):

    scrinium tools install local

To try it in 5 minutes, without a model or a GPU:

    scrinium demo

Linux and macOS, in a terminal inside this folder:

    sh install.sh

Windows, in PowerShell inside this folder:

    powershell -ExecutionPolicy Bypass -File install.ps1

Then, to open Scrinium:

    scrinium start

If something fails, `scrinium doctor` says what is missing and how to fix it.
To stop it: `scrinium stop`. To watch everything it does live: `scrinium attach`.
To uninstall everything, data included: `scrinium uninstall`.
