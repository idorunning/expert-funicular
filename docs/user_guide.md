# User guide

## Log in
The first time BrokerLedger runs it walks you through creating an admin account. On later launches, enter your username and password. After 5 wrong passwords in a row your account locks for 15 minutes.

## Create a client
From the **Clients** screen, click **+ New client** and type the client's name (and an optional reference). This creates a new per-client folder under `%APPDATA%\BrokerLedger\clients\` where their bank statements are copied.

## Import bank statements
Double-click a client to open their detail view. Drag and drop one or more statement files onto the big drop zone — PDFs, CSVs, or XLSX files are all accepted. BrokerLedger:

1. copies the file into the client's folder,
2. parses transactions,
3. asks the local LLM to categorise each one (or matches against a rule if it has seen the merchant before),
4. flags uncertain rows.

If you drop the same file twice, BrokerLedger detects it and skips the import.

## Review & correct
Click **Review transactions →**. You see every transaction with a category dropdown. Flagged rows are highlighted. To amend a category, click the Category cell (or press Enter) and pick from the dropdown — Committed and Discretionary categories are listed. Your correction is saved immediately:

- the transaction is updated,
- a **rule** is recorded so this merchant will be auto-categorised for this client next time,
- after three different clients confirm the same mapping the rule is **promoted to global** and starts helping every broker.

Toggle **Flagged only** to work through just the uncertain rows.

## Affordability
The Client detail view shows a live summary:

- Period covered
- Income (total) — credits that were categorised as income. You can override this by typing a Declared income if the client is self-employed.
- Committed total and Discretionary total
- Outgoings total
- Net disposable (= income − outgoings)
- Monthly figures (total ÷ months-in-window)

## Export
Click **Export XLSX…**. The workbook has four sheets:

- **Transactions** — every row with date, description, merchant, amount, category, group, confidence, source.
- **Category Totals** — per-category counts and sums, plus a monthly average.
- **Affordability Summary** — the same numbers you see on screen, ready to paste into a lender portal.
- **Audit** — who exported it, when, and the SHA-256 of every source statement so the spreadsheet is self-evidencing.

## Keyboard tips
- Arrow keys navigate the transactions table.
- Enter or double-click opens the category dropdown.
- Typing any character opens the dropdown too.
