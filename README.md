# RoutelinkPriceWatch MVP
- A lightweight, standard-library Python web app to compare partner CSV pricing sheets. Track price shifts, SKU updates, and billing increments with SQLite scan history and notification previews.
- A local, functional MVP for comparing partner pricing sheets.

## Features

- Create and manage partners
- Upload a baseline and current CSV pricing sheet
- Parse and normalize CSV data without third-party packages
- Compare prices, billing increments, added/removed rows, and SKU/product changes
- Persist partners and scan history in SQLite
- Browse scan results and activity logs
- Re-run the most recent comparison
- RLPW.svg project logo from PROJECT R
- Persistent light/dark mode toggle with #f15a24 primary accent
- Generate a notification preview (no external email is sent)
