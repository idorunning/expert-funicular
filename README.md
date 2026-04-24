# Mortgage Broker Affordability Assistant

A fully-local desktop app for UK mortgage brokers. Ingests client bank statements (PDF / CSV / XLSX), uses a local LLM to categorise every transaction against the mortgage-affordability taxonomy, lets the broker correct any mistakes, totals each category, and exports an affordability spreadsheet. Nothing ever leaves the machine.

Published by Mortgage Oasis Ltd — [www.mortgage-oasis.com](https://www.mortgage-oasis.com).

## Privacy

- No outbound network calls. The app talks only to `127.0.0.1:11434` (Ollama).
- All data lives on disk under `%APPDATA%/BrokerLedger` on Windows (or `~/.brokerledger` on other platforms).
- Optional SQLCipher at-rest encryption (opt-in at first run).

## Prerequisites

1. **Python 3.11+**.
2. **[Ollama](https://ollama.com)** installed and running locally.
3. Pull a model. Any of these work; the app auto-detects the first one available:
   ```
   ollama pull gemma3:4b
   # or
   ollama pull gemma3n:e4b
   # or
   ollama pull llama3.2:3b-instruct
   ```
4. **Optional** for scanned-PDF OCR: install Tesseract OCR and Poppler, then `pip install -e .[ocr]`.

## Install & run

```
pip install -e .[dev]
python -m brokerledger
```

On first launch you are asked to create an admin user and (optionally) enable encryption at rest. The app then verifies Ollama is reachable and shows which models are available.

## Quick access for manual testing

If you want to open the app and click through it end-to-end:

1. Install dependencies:
   ```
   pip install -e .[dev]
   ```
2. Start Ollama in another terminal (if not already running):
   ```
   ollama serve
   ```
3. Ensure at least one supported model is present:
   ```
   ollama pull gemma3:4b
   ```
4. Launch the GUI:
   ```
   python -m brokerledger
   ```
5. First run: create an admin account, then log in and create a test client.

If you only want a quick non-GUI sanity check:

```
python -m brokerledger.cli --demo
```

## Workflow

1. **Log in** (admin or broker).
2. **Create client** (just a name).
3. **Drop bank statements** onto the import zone (PDF, CSV, XLSX; multiple files at once).
4. **Process** — the app parses rows, applies rule-matches for known merchants, and calls the local LLM for the rest.
5. **Review** — every transaction is shown with a category dropdown and a confidence badge. Low-confidence or ambiguous rows are highlighted. Fixing a row writes a rule into the learning store so the same merchant is auto-classified in future.
6. **Affordability** — per-category totals plus committed vs discretionary group sums and net disposable income. Monthly-average toggle.
7. **Export** — writes an XLSX with Transactions, Category Totals, Affordability Summary, and Audit sheets.

## Category taxonomy

**Committed:** Other mortgage / Rent · Spousal / Child maintenance · Electricity / Gas / Oil · Water · Communications · Television · Council tax · Car costs · Other transport costs · Service charge / Ground rent

**Discretionary:** Food · Clothing · Household maintenance · Entertainment · Child care · Holidays · Pension contributions · Investments · Insurances

Internal (filtered from outgoings): Salary/Wages · Other income · Transfer/Excluded

## Development

```
pip install -e .[dev]
pytest -q
```

GUI-free smoke test of the ingest + categorise pipeline:

```
python -m brokerledger.cli --demo
```

## Layout

See `docs/` for user and admin guides and `/root/.claude/plans/...` for the original design.
