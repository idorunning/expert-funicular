# Installing a local model for BrokerLedger

BrokerLedger does all categorisation on this machine via [Ollama](https://ollama.com). Nothing about your clients' bank statements is ever sent over the network — the app talks only to `http://127.0.0.1:11434`.

## 1. Install Ollama

- Windows: download the installer from [ollama.com/download](https://ollama.com/download) and run it. Ollama starts automatically as a background service on `127.0.0.1:11434`.
- macOS: `brew install ollama` or the .dmg from the site.
- Linux: `curl -fsSL https://ollama.com/install.sh | sh`.

## 2. Pull a model

Any of these will work. The app auto-picks the first one available.

```
ollama pull gemma3:4b         # default. ~4 GB on disk, ~5 GB RAM while running.
ollama pull gemma3n:e4b       # lighter edge variant, lower RAM.
ollama pull llama3.2:3b-instruct
```

Bigger is better for accuracy; smaller is faster. For a broker's laptop `gemma3:4b` is a good default; on a tiny machine use `gemma3n:e4b`.

## 3. Verify from a terminal

```
ollama list
```

You should see the model you pulled.

## 4. Launch BrokerLedger

BrokerLedger's first-run wizard will check Ollama is reachable and tell you which model it picked. If the wizard says "not reachable", start Ollama (open a terminal and run `ollama serve` on Linux/macOS, or open the Ollama tray app on Windows) and retry.

## Notes

- The app never calls any URL other than `127.0.0.1`. There is a unit test asserting this.
- If you want to change the model later, edit it in the Settings pane.
- To run the app without Ollama (for testing), set the environment variable `BROKERLEDGER_FAKE_LLM=1`. Categorisation quality will be poor — the fake client uses keyword hints only.
