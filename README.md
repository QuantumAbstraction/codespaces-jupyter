# GitHub Codespaces ♥️ Jupyter Notebooks

Welcome to your shiny new codespace! We've got everything fired up and running for you to explore Python and Jupyter notebooks.

You've got a blank canvas to work on from a git perspective as well. There's a single initial commit with what you're seeing right now - where you go from here is up to you!

Everything you do here is contained within this one codespace. There is no repository on GitHub yet. If and when you’re ready you can click "Publish Branch" and we’ll create your repository and push up your project. If you were just exploring then and have no further need for this code then you can simply delete your codespace and it's gone forever.

## Connect From Another vscode.dev Session

After rebuilding the Codespace, port `8888` is forwarded privately for a Jupyter
Server. Start the server in this Codespace:

```bash
python3 -m jupyter lab --no-browser --ip=0.0.0.0 --ServerApp.allow_remote_access=True
```

Open the **Ports** view, copy the forwarded address for port `8888`, and append
the `?token=...` value printed by Jupyter. In the other vscode.dev session,
open a notebook, select **Kernel**, then **Select Another Kernel** and **Existing
Jupyter Server**, and enter that complete URL.

The forwarded port is private, so the second vscode.dev session must be signed
in to the same GitHub account or an account with access to this Codespace.
